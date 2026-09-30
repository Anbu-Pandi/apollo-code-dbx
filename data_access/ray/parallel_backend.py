"""`ray` parallel backend for JobExecutor (exec_type='ray').

A local/laptop driver spins a SINGLE_USER ML ephemeral job cluster (via data_access.spark.dbjobs)
and runs the self-contained _ray_entry.py on it. That launcher does setup_ray_cluster + ray.remote
fan-out and writes results back through a UC volume. Same contract as JobExecutor.submitjob:
ordered [{'result','error'}].

The remote fan-out logic lives in _ray_entry.py (self-contained), NOT here, because this module
(data_access.ray.*) is new code not yet present in the APO wheels deployed on the cluster. This
module is pure orchestration: marshal the call -> spin cluster -> run launcher -> collect results.
The user function is cloudpickled by value, so the cluster needs only the deployed wheels for the
function's own imports. (Once data_access.ray ships in the wheels, an on-cluster in-process path
that skips the job submit can be added.)

Top-level imports stay light (registry + stdlib); databricks SDK / cloudpickle load at call time.
"""
import logging
import pickle
import uuid

from common.utils.jobutil import JobExecutor
from common.utils.parallel_registry import register_backend


def make_backend(max_workers=8, timeout=None, num_cpus=1, max_retries=0,
                 job_node_type_id=None, cores_per_node=None, **_ignored):
    return RayBackend(max_workers=max_workers, timeout=timeout, num_cpus=num_cpus,
                      max_retries=max_retries, job_node_type_id=job_node_type_id,
                      cores_per_node=cores_per_node)


# Self-register on import. common never names this backend.
register_backend('ray', make_backend)


def _cloudpickle():
    """cloudpickle is REQUIRED on the submitting host: it serializes params (and any by-value
    function fallback) so closures/lambdas/__main__ funcs survive the trip. Plain pickle would
    serialize them by reference (e.g. '__main__.fn'), which fails on the cluster. We refuse to
    silently fall back to that."""
    try:
        import cloudpickle
        return cloudpickle
    except ImportError as e:
        raise ImportError("ray backend requires 'cloudpickle' on the submitting host "
                          "(pip install cloudpickle)") from e


def _encode_func(func):
    """Portable representation of a function for the cluster.

    If the function is importable by (module, qualname) — i.e. it lives in a deployed wheel,
    not in __main__ or a closure — ship a plain `{'__funcref__': [module, qualname]}` marker.
    The cluster re-imports it; nothing is pickled by value, so this is robust across cloudpickle
    versions. Otherwise return the function itself, to be cloudpickled BY VALUE (e.g. ad-hoc
    probe functions defined in __main__) — that path requires cloudpickle matched to the cluster.
    """
    import importlib
    mod = getattr(func, '__module__', None)
    qn = getattr(func, '__qualname__', None)
    if mod and qn and mod != '__main__' and '<locals>' not in qn:
        try:
            obj = importlib.import_module(mod)
            for part in qn.split('.'):
                obj = getattr(obj, part)
            if obj is func:
                return {'__funcref__': [mod, qn]}
        except Exception:
            pass
    return func


def _dumps_call(payload, pairs):
    """Serialize the call. Funcref-only payloads are pure data -> stdlib pickle (version-robust,
    no cloudpickle needed). A by-value function (e.g. a __main__ probe) or an unpicklable param
    falls back to cloudpickle (which then needs cloudpickle matched to the cluster)."""
    if any(callable(enc) for enc, _ in pairs):
        return _cloudpickle().dumps(payload)
    try:
        return pickle.dumps(payload)
    except Exception:
        return _cloudpickle().dumps(payload)


def _loads_results(blob):
    """Load the results envelope (pure data: dicts/strings/bytes incl. Arrow-IPC markers) with
    stdlib pickle, and rebuild any DataFrame results from their Arrow IPC bytes."""
    try:
        results = pickle.loads(blob)
    except Exception:
        results = _cloudpickle().loads(blob)
    return [_decode_result(r) for r in results]


def _decode_result(item):
    val = item.get('result') if isinstance(item, dict) else None
    if isinstance(val, dict) and '__arrow__' in val:
        import pyarrow as pa
        item = dict(item)
        item['result'] = pa.ipc.open_stream(val['__arrow__']).read_all().to_pandas()
    return item


class RayBackend:
    def __init__(self, max_workers=8, timeout=None, num_cpus=1, max_retries=0,
                 job_node_type_id=None, cores_per_node=None):
        self.max_workers = max_workers   # target concurrent tasks
        self.timeout = timeout
        self.num_cpus = num_cpus          # cores per ray task
        self.max_retries = max_retries    # ray task re-executions on worker death (0 = at-most-once)
        self.job_node_type_id = job_node_type_id # None -> apo_data_access.parallel config
        self.cores_per_node = cores_per_node              # None -> apo_data_access.parallel config

    def submitjob(self, funcs, params=None, initconfig=False, job_name=None, **func_kwargs):
        from pathlib import Path
        from data_access.spark import dbjobs

        dbjobs.require_env_config()  # fail fast if prod/uat isn't configured (defaults are dev)
        # Encode each function as an importable (module, qualname) ref when possible, so the
        # common case (functions in deployed wheels) ships no pickled code at all.
        pairs = [(_encode_func(func), paramdict)
                 for func, paramdict in JobExecutor._iter_funcs_params(funcs, params)]
        setup_args = JobExecutor._setup_args(job_name=job_name)
        min_nodes, max_nodes = dbjobs.worker_node_bounds(
            self.max_workers, n_tasks=len(pairs), num_cpus_per_task=self.num_cpus,
            cores_per_node=self.cores_per_node)

        w = dbjobs.client()
        run_uuid = uuid.uuid4().hex
        marshal_dir = dbjobs.pconf('marshal_volume_dir').rstrip('/') + f'/{run_uuid}'
        call_uri = f'{marshal_dir}/call.pkl'
        result_uri = f'{marshal_dir}/result.pkl'

        payload = {'pairs': pairs, 'func_kwargs': func_kwargs, 'setup_args': setup_args,
                   'ray_min_nodes': min_nodes, 'ray_max_nodes': max_nodes, 'num_cpus': self.num_cpus,
                   'max_retries': self.max_retries}
        dbjobs.volume_put(w, call_uri, _dumps_call(payload, pairs))

        # Per-run launcher path (under run_uuid) so concurrent submits never race on a shared
        # workspace file (delete-then-upload of a fixed path corrupts overlapping jobs).
        entry_local = Path(__file__).parent / '_ray_entry.py'
        ws_run_dir = f'{dbjobs.workspace_dir(w)}/{run_uuid}'
        remote_entry = f'{ws_run_dir}/_ray_entry.py'
        dbjobs.upload_workspace_file(w, entry_local.read_bytes(), remote_entry)

        node_type_id = self.job_node_type_id or dbjobs.pconf('job_node_type_id')
        run_name = f'ray_{job_name or "jobexec"}_{run_uuid[:8]}'
        logging.info(f'ray backend: spinning ephemeral cluster ({max_nodes} nodes, {node_type_id}, '
                     f'{self.num_cpus} cpu/task) for {len(pairs)} tasks')
        try:
            run_id, result_state, run = dbjobs.submit_python_task_and_poll(
                w, run_name, remote_entry,
                ['--call-uri', call_uri, '--result-uri', result_uri],
                # standard runtime; ray comes from the requirements.txt task library (no ML runtime)
                dbjobs.new_cluster_spec(w, use_ml_runtime=False, min_workers=min_nodes, max_workers=max_nodes,
                                        node_type_id=self.job_node_type_id),
                libraries=dbjobs.task_libraries(),
                timeout_sec=self.timeout or 7200,
            )
            if result_state != 'SUCCESS':
                err = dbjobs.first_task_error(w, run)
                raise RuntimeError(f'ray job {run_id} did not succeed: result_state={result_state}\n{err}')

            results = _loads_results(dbjobs.volume_get(w, result_uri))
            logging.info(f'ray job {run_id} returned {len(results)} results')
            return results
        finally:
            # Always remove the marshalling files — call.pkl carries the pickled config
            # (credentials) — and the per-run launcher, even when the run failed.
            dbjobs.cleanup_run(w, workspace_dir=ws_run_dir, volume_files=[call_uri, result_uri])
