"""Bounded, input-keyed runs hosted in the dedicated CDX Research process."""
import copy
import json
import logging
import threading
import time
import uuid
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import date


def validate_inputs(kind, values):
    if kind not in ('quotes', 'model'):
        raise ValueError('Unknown research page')
    values = dict(values or {})
    start = date.fromisoformat(str(values.get('start', ''))[:10])
    end = date.fromisoformat(str(values.get('end', ''))[:10])
    if start > end:
        raise ValueError('Start date must be on or before end date')
    if (end - start).days > (31 if kind == 'quotes' else 3660):
        raise ValueError('Choose at most 31 days for quotes or 10 years for the model')
    result = {'start': start.isoformat(), 'end': end.isoformat()}
    if kind == 'quotes':
        result['twids'] = sorted(set(values.get('twids') or []))
        if len(result['twids']) > 12 or any(not isinstance(t, str) or len(t) > 60 for t in result['twids']):
            raise ValueError('Choose up to 12 instruments')
        result['interval'] = int(values.get('interval', 300))
        if not 10 <= result['interval'] <= 3600:
            raise ValueError('Quote sampling must be between 10 and 3600 seconds')
    else:
        result['rebuild'] = bool(values.get('rebuild', False))
        result['tenors'] = sorted(set(values.get('tenors') or ['1m', '3m']))
        if not set(result['tenors']) <= {'1m', '3m', '6m'}:
            raise ValueError('Choose 1m, 3m or 6m tenors')
        result['deltas'] = sorted(set(float(d) for d in (values.get('deltas') or [0.15, 0.25, 0.5, 0.75, 0.85])))
        if len(result['deltas']) > 9 or any(not 0 < d < 1 for d in result['deltas']):
            raise ValueError('Choose up to 9 deltas between 0 and 1')
        result['window'] = int(values.get('window', 90))
        if not 5 <= result['window'] <= 504:
            raise ValueError('Realized correlation window must be 5–504 observations')
        result['weekly_window'] = int(values.get('weekly_window', 12))
        result['half_life'] = float(values.get('half_life', 4))
        if not 2 <= result['weekly_window'] <= 104 or not 0 < result['half_life'] <= 104:
            raise ValueError('Weekly window must be 2–104 and EW half-life must be greater than zero and at most 104 weeks')
    return result


class ResearchJobs:
    def __init__(self, runner=None, max_jobs=24, workers=3):
        self._runner = runner
        self._max_jobs = max_jobs
        self._workers = workers
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='CDXResearch')
        self._lock = threading.RLock()
        self._jobs = {}

    def start(self, kind, values, force=False):
        params = validate_inputs(kind, values)
        key = json.dumps([kind, params], sort_keys=True)
        now = time.time()
        with self._lock:
            matches = [j for j in self._jobs.values() if j['key'] == key]
            latest = max(matches, key=lambda j: j['created'], default=None)

            if latest and (latest['running'] or (not force and now - latest['created'] < (60 if kind == 'quotes' else 900))):
                return self._snapshot(latest, latest['revision'])
            if kind == 'model' and any(j['running'] and j['kind'] == 'model' for j in self._jobs.values()):
                raise ValueError('A correlation model is already running. Please retry when it finishes.')
            if sum(j['running'] for j in self._jobs.values()) >= self._workers:
                raise ValueError('Research workers are busy. Please retry shortly.')
            while len(self._jobs) >= self._max_jobs:
                finished = [j for j in self._jobs.values() if not j['running']]
                if not finished:
                    raise ValueError('Research workers are busy. Please retry shortly.')
                del self._jobs[min(finished, key=lambda j: j['created'])['id']]
            job = dict(id=uuid.uuid4().hex, key=key, kind=kind, params=params,
                       created=now, finished=None, running=True, revision=0, panels=[], error=None)
            self._jobs[job['id']] = job
            self._pool.submit(self._run, job)
            return self._snapshot(job, job['revision'])

    def _run(self, job):
        try:
            if self._runner:
                runner = self._runner
            else:
                from dash_reports.cdx_research.pipeline import run
                runner = run
            for panel in runner(job['kind'], job['params']):
                with self._lock:
                    job['revision'] += 1
                    panel = panel | {'revision': job['revision'], 'completed': time.time()}
                    job['panels'].append((job['revision'], zlib.compress(json.dumps(panel).encode('utf-8'))))
                import psutil
                memory = psutil.Process().memory_info()
                logging.info('CDX %s panel=%s rss_mb=%.0f private_mb=%.0f', job['kind'], panel['title'],
                             memory.rss / 2**20, getattr(memory, 'private', memory.rss) / 2**20)
        except Exception as exc:
            logging.exception('CDX research run failed: %s', job['kind'])
            with self._lock:
                job['error'] = str(exc)
        finally:
            with self._lock:
                job['running'] = False
                job['finished'] = time.time()
                job['revision'] += 1

    def _snapshot(self, job, since):
        return copy.deepcopy({k: v for k, v in job.items() if k != 'key' and k != 'panels'}) | {
            'panels': [json.loads(zlib.decompress(payload)) for revision, payload in job['panels'] if revision > since]}

    def poll(self, job_id, since=0):
        with self._lock:
            if job_id not in self._jobs:
                raise ValueError('This run expired or the server restarted. Refresh to start again.')
            return self._snapshot(self._jobs[job_id], int(since))

    def close(self):
        self._pool.shutdown(wait=True)
