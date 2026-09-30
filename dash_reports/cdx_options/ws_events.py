import json
import logging
import math
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from concurrent.futures import TimeoutError as FuturesTimeout

from dash_reports.cdx_options.scheduler import interactive_activity, stage_timer


def _safe_dumps(obj):
    try:
        return json.dumps(obj, default=str, allow_nan=False)
    except ValueError:
        txt = json.dumps(obj, default=str)
        for token in ('NaN', '-Infinity', 'Infinity'):
            txt = txt.replace(f': {token}', ': null').replace(f'[{token}', '[null').replace(f'{token}]', 'null]').replace(f', {token}', ', null')
        return txt


def _message_for_request(msg, request_id):
    try:
        payload = json.loads(msg)
        if request_id is None:
            payload.pop('request_id', None)
        else:
            payload['request_id'] = request_id
        return _safe_dumps(payload)
    except Exception:
        return msg


def _tab_payload_has_content(key, payload):
    if not isinstance(payload, dict) or payload.get('_cleared') or payload.get('_empty') or payload.get('_error'):
        return False
    if key == 'vol_grids':
        grids = payload.get('grids') or {}
        return any(grid.get('data') for grid in (grids.values() if isinstance(grids, dict) else grids)
                   if isinstance(grid, dict))
    if key == 'trade_blotter':
        blotters = payload.get('blotters') or {}
        return any(blotter.get('data') for blotter in (blotters.values() if isinstance(blotters, dict) else blotters)
                   if isinstance(blotter, dict))
    if key == 'positions':
        return bool(payload.get('figures'))
    if key == 'expiry_charts':
        return bool(payload.get('exp_vols_figure') or payload.get('spot_ts_figure') or payload.get('charts'))
    if key == 'option_stack':
        return any(stack.get('records') for stack in payload.get('stacks') or [] if isinstance(stack, dict))
    return bool(payload)


class _SerializedWebSocket:

    def __init__(self, raw_ws):
        self._raw_ws = raw_ws
        self._send_lock = threading.Lock()
        self._closed = threading.Event()

    def send(self, data):
        if self._closed.is_set():
            raise RuntimeError('websocket closed')
        with self._send_lock:
            if self._closed.is_set():
                raise RuntimeError('websocket closed')
            try:
                return self._raw_ws.send(data)
            except Exception:
                self._closed.set()
                raise

    def receive(self, *args, **kwargs):
        if self._closed.is_set():
            raise RuntimeError('websocket closed')
        try:
            result = self._raw_ws.receive(*args, **kwargs)
        except Exception:
            self._closed.set()
            raise
        if result is None and self._closed.is_set():
            raise RuntimeError('websocket closed')
        return result

    def mark_closed(self):
        self._closed.set()


def _normalize_selection(value, numeric=False):
    values = value if isinstance(value, (list, tuple)) else ([] if value in (None, '') else [value])
    normalized = []
    for item in values:
        if item in (None, ''):
            continue
        if numeric:
            try:
                normalized.append(str(float(item)))
                continue
            except (TypeError, ValueError):
                pass
        normalized.append(str(item))
    return normalized


def _selection_matches(value, expected, numeric=False):
    expected_norm = _normalize_selection(expected, numeric=numeric)
    if not expected_norm:
        return True
    return _normalize_selection(value, numeric=numeric) == expected_norm


def _selection_label(product=None, strike=None, expiry=None):
    parts = [str(product or '')]
    strike_vals = _normalize_selection(strike, numeric=True)
    expiry_vals = _normalize_selection(expiry, numeric=False)
    if strike_vals:
        parts.append(f'K={",".join(strike_vals)}')
    if expiry_vals:
        parts.append(f'Exp={",".join(expiry_vals)}')
    return ' | '.join([part for part in parts if part])


class CDXEventBus:

    SUPPRESS_WINDOW = 3.0

    def __init__(self):
        self._clients = []
        self._lock = threading.Lock()
        self._suppress_until = {}
        self._subscriptions = {}
        self._override_active = {}
        self._override_params = {}

    def register(self, ws):
        with self._lock:
            self._clients.append(ws)
            self._subscriptions[id(ws)] = {}

    def unregister(self, ws):
        if hasattr(ws, 'mark_closed'):
            ws.mark_closed()
        with self._lock:
            if ws in self._clients:
                self._clients.remove(ws)
            self._suppress_until.pop(id(ws), None)
            self._subscriptions.pop(id(ws), None)
            self._override_active.pop(id(ws), None)
            self._override_params.pop(id(ws), None)
        _cleanup_gen(ws)

    def suppress(self, ws, seconds=None):
        with self._lock:
            self._suppress_until[id(ws)] = time.time() + (seconds or self.SUPPRESS_WINDOW)

    def set_override_active(self, ws, active, params=None):
        with self._lock:
            if active:
                self._override_active[id(ws)] = True
                if params is not None:
                    self._override_params[id(ws)] = dict(params)
            else:
                self._override_active.pop(id(ws), None)
                self._override_params.pop(id(ws), None)

    def is_override_active(self, ws):
        with self._lock:
            return bool(self._override_active.get(id(ws)))

    def get_override_params(self, ws):
        with self._lock:
            return dict(self._override_params.get(id(ws)) or {})

    def iter_override_clients(self):
        with self._lock:
            params_map = dict(self._override_params)
            return [(ws, dict(params_map.get(id(ws)) or {}))
                    for ws in list(self._clients)
                    if params_map.get(id(ws))]

    def set_selection(self, ws, product=None, strike=None, expiry=None,
                      requested_tabs=None, active_tab=None):
        with self._lock:
            ws_id = id(ws)
            if ws_id not in self._subscriptions:
                return
            self._subscriptions[ws_id] = {
                'product': product or '',
                'strike': _normalize_selection(strike, numeric=True),
                'expiry': _normalize_selection(expiry, numeric=False),
                'requested_tabs': sorted(set(requested_tabs or [])),
                'active_tab': active_tab or '',
                'ts': time.time(),
            }

    def latest_selection(self, product=None):
        with self._lock:
            subs = list(self._subscriptions.values())
        best = None
        for sub in subs:
            if not sub.get('product'):
                continue
            if product and sub.get('product') != product:
                continue
            if best is None or float(sub.get('ts') or 0) >= float(best.get('ts') or 0):
                best = sub
        if not best:
            return {}
        return {
            'product': best.get('product') or '',
            'strike': list(best.get('strike') or []),
            'expiry': list(best.get('expiry') or []),
        }

    def grouped_subscriptions(self, products=None):
        allowed = set(products or [])
        with self._lock:
            clients = list(self._clients)
            subscriptions = dict(self._subscriptions)
            override_active = dict(self._override_active)
            override_params = dict(self._override_params)
        groups = {}
        for ws in clients:
            ws_id = id(ws)
            subscription = subscriptions.get(ws_id) or {}
            product = subscription.get('product')
            if not product or (allowed and product not in allowed):
                continue
            params = dict(override_params.get(ws_id) or {}) if override_active.get(ws_id) else {}
            key_params = {key: value for key, value in params.items() if key != 'request_id'}
            override_key = _safe_dumps(key_params) if key_params else ''
            key = (
                product,
                tuple(subscription.get('strike') or []),
                tuple(subscription.get('expiry') or []),
                override_key,
            )
            group = groups.setdefault(key, {
                'product': product,
                'strike': list(subscription.get('strike') or []),
                'expiry': list(subscription.get('expiry') or []),
                'override_params': params,
                'requested_tabs': set(),
                'clients': [],
            })
            group['requested_tabs'].update(subscription.get('requested_tabs') or [])
            group['clients'].append(ws)
        result = list(groups.values())
        for group in result:
            group['requested_tabs'] = sorted(group['requested_tabs'])
        return result

    def send_clients(self, clients, event_type, payload=None, require_override=False):
        data = payload or {}
        msg = _safe_dumps({'type': event_type, **data})
        with self._lock:
            live = set(self._clients)
            subscriptions = dict(self._subscriptions)
            override_active = dict(self._override_active)
        dead = []
        sent = 0
        for ws in clients:
            ws_id = id(ws)
            if ws not in live or bool(override_active.get(ws_id)) != bool(require_override):
                continue
            if event_type == 'tab_data' and not self._subscription_matches(subscriptions.get(ws_id) or {}, data):
                continue
            try:
                ws.send(msg)
                sent += 1
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.unregister(ws)
        return sent

    @staticmethod
    def _subscription_matches(subscription, payload):
        product = subscription.get('product')
        if not product:
            return False
        if payload.get('product') != product:
            return False
        if not _selection_matches(payload.get('strike'), subscription.get('strike'), numeric=True):
            return False
        if not _selection_matches(payload.get('expiry'), subscription.get('expiry'), numeric=False):
            return False
        return True

    def _wants_tab_data(self, ws, payload):
        subscription = self._subscriptions.get(id(ws)) or {}
        return self._subscription_matches(subscription, payload)

    def broadcast(self, event_type, payload=None):
        msg = _safe_dumps({'type': event_type, **(payload or {})})
        now = time.time()
        with self._lock:
            clients = list(self._clients)
            suppress_until = dict(self._suppress_until)
            subscriptions = dict(self._subscriptions)
            override_active = dict(self._override_active)
        wanted = []
        if event_type == 'tab_data':
            seen = set()
            for subscription in subscriptions.values():
                if not subscription.get('product'):
                    continue
                label = _selection_label(
                    subscription.get('product'),
                    subscription.get('strike'),
                    subscription.get('expiry'),
                )
                if not label or label in seen:
                    continue
                seen.add(label)
                wanted.append(label)
                if len(wanted) >= 3:
                    break
        dead = []
        sent = 0
        filtered = 0
        suppressed = 0
        override_skipped = 0
        for ws in clients:
            ws_id = id(ws)
            if event_type == 'tab_data' and override_active.get(ws_id):
                override_skipped += 1
                continue
            if event_type == 'tab_data' and now < suppress_until.get(ws_id, 0):
                suppressed += 1
                continue
            if event_type == 'tab_data' and not self._subscription_matches(subscriptions.get(ws_id) or {}, payload or {}):
                filtered += 1
                continue
            try:
                ws.send(msg)
                sent += 1
            except Exception:
                dead.append(ws)
        if dead:
            with self._lock:
                for ws in dead:
                    if hasattr(ws, 'mark_closed'):
                        ws.mark_closed()
                    if ws in self._clients:
                        self._clients.remove(ws)
                    self._suppress_until.pop(id(ws), None)
                    self._subscriptions.pop(id(ws), None)
                    _cleanup_gen(ws)
                client_count = len(self._clients)
        else:
            client_count = len(clients)
        if event_type == 'tab_data' and payload and client_count and sent == 0:
            logging.warning(
                'Bridge dropped tab_data: %s source=%s filtered=%s suppressed=%s override_skipped=%s wanted=%s',
                _selection_label(payload.get('product'), payload.get('strike'), payload.get('expiry')),
                payload.get('_source', ''),
                filtered,
                suppressed,
                override_skipped,
                wanted,
            )
        return len(msg)

    @property
    def client_count(self):
        with self._lock:
            return len(self._clients)


event_bus = CDXEventBus()


class _TaskRegistry:

    RETAIN_MS = 10 * 60 * 1000
    MAX_TASKS = 64

    def __init__(self, event_bus, shared_store=None, sync_fn=None):
        self._event_bus = event_bus
        self._lock = threading.Lock()
        self._tasks = {}
        self._seq = 0
        self._store_path = shared_store.path if shared_store is not None else None
        self._sync_fn = sync_fn
        self._pending = {}
        self._sync_thread = None
        self._last_sync = 0.0

    @staticmethod
    def _now_ms():
        return int(time.time() * 1000)

    def _trim_locked(self):
        now_ms = self._now_ms()
        stale_ids = [
            task_id
            for task_id, task in self._tasks.items()
            if task.get('status') in {'done', 'error', 'cancelled'}
            and now_ms - int(task.get('updated_ms') or 0) > self.RETAIN_MS
        ]
        for task_id in stale_ids:
            self._tasks.pop(task_id, None)
        if len(self._tasks) <= self.MAX_TASKS:
            return
        ordered = sorted(
            self._tasks.values(),
            key=lambda task: (
                0 if task.get('status') in {'running', 'queued'} else 1,
                -(task.get('updated_ms') or 0),
            ),
        )
        keep = {task['task_id'] for task in ordered[:self.MAX_TASKS]}
        self._tasks = {
            task_id: task for task_id, task in self._tasks.items()
            if task_id in keep
        }

    def _snapshot_locked(self):
        self._trim_locked()
        tasks = [dict(task) for task in self._tasks.values()]
        tasks.sort(
            key=lambda task: (
                0 if task.get('status') in {'running', 'queued'} else 1,
                -(task.get('updated_ms') or 0),
            )
        )
        return {'tasks': tasks, 'ts': self._now_ms()}

    def _publish(self, snapshot):
        self._event_bus.broadcast('task_state', snapshot)

    def _persist(self, task):
        with self._lock:
            current = self._pending.get(task['task_id'])
            if current is None or task['updated_ms'] > current['updated_ms']:
                self._pending[task['task_id']] = dict(task)
        self._sync()

    def _sync(self):
        from apo_config.config import config

        if self._sync_fn is None and self._store_path is None and config.getenv() is None:
            return
        with self._lock:
            if self._sync_thread is not None or (not self._pending and time.monotonic() - self._last_sync < 2):
                return
            self._sync_thread = threading.Thread(target=self._run_sync, daemon=True, name='CDXTaskSync')
            thread = self._sync_thread
        thread.start()

    def _run_sync(self):
        from dash_reports.cdx_options.task_state_worker import sync_task_state

        exchange = self._sync_fn or sync_task_state
        while True:
            with self._lock:
                pending = self._pending
                self._pending = {}
            try:
                tasks = exchange(list(pending.values()), self.RETAIN_MS, self._store_path)
            except Exception:
                logging.exception('CDX task state background sync failed; keeping local status')
                with self._lock:
                    for task_id, task in pending.items():
                        self._pending.setdefault(task_id, task)
                    self._last_sync = time.monotonic()
                    self._sync_thread = None
                return
            with self._lock:
                for task in tasks:
                    task_id = task["task_id"]
                    current = self._tasks.get(task_id)
                    if current is None or task["updated_ms"] > current["updated_ms"]:
                        self._tasks[task_id] = task
                snapshot = self._snapshot_locked()
                done = not self._pending
                if done:
                    self._last_sync = time.monotonic()
                    self._sync_thread = None
            self._publish(snapshot)
            if done:
                return

    def start(
        self,
        kind,
        title,
        *,
        session_id='',
        scope='session',
        status='queued',
        detail='',
        product=None,
        strike=None,
        expiry=None,
    ):
        now_ms = self._now_ms()
        with self._lock:
            self._seq += 1
            task_id = f'task-{uuid.uuid4().hex}'
            task = {
                'task_id': task_id,
                'kind': str(kind or 'task'),
                'title': str(title or kind or 'Task'),
                'scope': 'global' if scope == 'global' else 'session',
                'owner_session_id': str(session_id or ''),
                'status': str(status or 'queued'),
                'detail': str(detail or ''),
                'product': product or '',
                'strike': _normalize_selection(strike, numeric=True),
                'expiry': _normalize_selection(expiry, numeric=False),
                'selection_label': _selection_label(product, strike, expiry),
                'created_ms': now_ms,
                'updated_ms': now_ms,
                'finished_ms': None,
            }
            self._tasks[task_id] = task
            snapshot = self._snapshot_locked()
        self._publish(snapshot)
        self._persist(task)
        return task_id

    def update(self, task_id, **changes):
        if not task_id:
            return
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            for key, value in changes.items():
                if value is None:
                    continue
                if key == 'strike':
                    task[key] = _normalize_selection(value, numeric=True)
                elif key == 'expiry':
                    task[key] = _normalize_selection(value, numeric=False)
                else:
                    task[key] = value
            if any(key in changes for key in ('product', 'strike', 'expiry')):
                task['selection_label'] = _selection_label(
                    task.get('product'),
                    task.get('strike'),
                    task.get('expiry'),
                )
            task['updated_ms'] = max(self._now_ms(), task['updated_ms'] + 1)
            persisted = dict(task)
            snapshot = self._snapshot_locked()
        self._publish(snapshot)
        self._persist(persisted)

    def finish(self, task_id, status='done', detail=None):
        if not task_id:
            return
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task['status'] = str(status or 'done')
            if detail is not None:
                task['detail'] = str(detail)
            now_ms = max(self._now_ms(), task['updated_ms'] + 1)
            task['updated_ms'] = now_ms
            task['finished_ms'] = now_ms
            persisted = dict(task)
            snapshot = self._snapshot_locked()
        self._publish(snapshot)
        self._persist(persisted)

    def snapshot(self):
        self._sync()
        with self._lock:
            return self._snapshot_locked()


task_registry = _TaskRegistry(event_bus)


def _event_payload(event):
    return {k: v for k, v in event.items() if k not in ('type', '_sup', '_event_key')}


class FlightProgressBridge:

    BACKGROUND_ADMIT_WAIT_S = 2.5
    BACKGROUND_QUIET_S = 0.25

    def __init__(self, event_bus, poll_fast=0.5, poll_idle=5.0):
        self._event_bus = event_bus
        self._poll_fast = poll_fast
        self._poll_idle = poll_idle
        self._cursor = 0
        self._running = False
        self._thread = None
        self._refresh_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='CDXGen')

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logging.info('Flight progress bridge started')

    def stop(self):
        self._running = False

    def _send_group(self, group, event_type, payload):
        if group.get('override_params'):
            return self._event_bus.send_clients(
                group['clients'], event_type, payload, require_override=True,
            )
        if event_type == 'tab_data':
            return self._event_bus.broadcast(event_type, payload)
        return self._event_bus.send_clients(group['clients'], event_type, payload)

    def _refresh_group(self, group, generation, reason):
        from dash_reports.sub_servers.cdx_options_server.client import get_client

        product = group['product']
        strike = group.get('strike') or None
        expiry = group.get('expiry') or None
        params = dict(group.get('override_params') or {})
        requested_tabs = group.get('requested_tabs') or ['vol_grids']
        client = get_client()
        if not params:
            cached = client.get_cached_tabs(
                product,
                strike=strike,
                expiry=expiry,
                requested_tabs=requested_tabs,
            )
            if cached and cached.get('_stale'):
                cached.update({
                    'product': product,
                    'strike': strike,
                    'expiry': expiry,
                    '_source': 'materialized',
                    '_cache_state': 'cached-stale',
                    '_refresh_generation': generation,
                })
                self._send_group(group, 'tab_data', cached)
        if not interactive_activity.is_generation_current(generation):
            logging.info('Bridge refresh dropped for %s: generation %s superseded by %s',
                         _selection_label(product, strike, expiry), generation,
                         interactive_activity.current_generation())
            return
        interactive_activity.await_idle(self.BACKGROUND_ADMIT_WAIT_S, quiet_s=self.BACKGROUND_QUIET_S)
        if not interactive_activity.is_generation_current(generation):
            logging.info('Bridge refresh dropped after admission wait for %s: generation %s superseded by %s',
                         _selection_label(product, strike, expiry), generation,
                         interactive_activity.current_generation())
            return
        try:
            fresh = client.render_materialized_tabs(
                product,
                strike=strike,
                expiry=expiry,
                requested_tabs=requested_tabs,
                ref_px=params.get('ref_px'),
                ref_bps=params.get('ref_bps'),
                vol_shift_bps=params.get('vol_shift_bps'),
                vol_lookback=params.get('vol_lookback') or '5b',
            )
            has_tabs = isinstance(fresh, dict) and any(
                fresh.get(tab)
                for tab in requested_tabs
            )
            if not has_tabs or fresh.get('_cache_state') == 'refresh-failed':
                raise RuntimeError(str((fresh or {}).get('error') or 'empty materialized view'))
            fresh.update({
                'product': product,
                'strike': strike,
                'expiry': expiry,
                '_source': 'generation',
                '_cache_state': 'fresh',
                '_generation': generation,
            })
            fresh.pop('request_id', None)
            self._send_group(group, 'tab_data', fresh)
        except Exception as exc:
            logging.exception('Generation refresh failed for %s', _selection_label(product, strike, expiry))
            self._send_group(group, 'refresh_failed', {
                'product': product,
                'strike': strike,
                'expiry': expiry,
                'generation': generation,
                'reason': reason,
                'detail': str(exc),
            })

    def _refresh_subscriptions(self, payload):
        groups = self._event_bus.grouped_subscriptions(payload.get('products'))
        if payload.get('spot_only'):
            groups = [group for group in groups if not group.get('override_params')]
        generation = payload.get('generation')
        interactive_activity.note_generation(generation)
        reason = payload.get('reason') or 'data_changed'
        for group in groups:
            self._refresh_pool.submit(self._refresh_group, group, generation, reason)
        if groups:
            logging.info('Generation %s refreshing %s unique subscribed views', generation, len(groups))

    def _loop(self):
        from dash_reports.sub_servers.cdx_options_server.client import get_client
        backoff = self._poll_fast

        while self._running:
            if self._event_bus.client_count == 0:
                time.sleep(self._poll_idle)
                continue
            try:
                client = get_client()
                result = client.get_progress_events(since=self._cursor)
                events = result.get('events', [])
                new_cursor = result.get('cursor', self._cursor)

                if new_cursor < self._cursor:
                    self._cursor = 0
                    continue

                if events:
                    for ev in events:
                        ev_type = ev.get('type', 'unknown')
                        payload = _event_payload(ev)
                        nbytes = self._event_bus.broadcast(ev_type, payload)
                        if ev_type == 'data_generation':
                            self._refresh_subscriptions(payload)
                        if ev_type in ('tab_data', 'defaults'):
                            logging.info(f'Bridge broadcasting {ev_type} ({nbytes//1024}KB) to {self._event_bus.client_count} clients')
                    self._cursor = new_cursor
                    backoff = self._poll_fast
                else:
                    if new_cursor > self._cursor:
                        self._cursor = new_cursor
                    backoff = min(backoff * 1.5, self._poll_idle)
            except Exception:
                logging.exception('Flight progress bridge poll failed')
                backoff = self._poll_idle
            time.sleep(backoff)


progress_bridge = FlightProgressBridge(event_bus)

_command_handler = None
_ws_gens = {}
_ws_request_ids = {}
_cancelled_request_ids = {}
_ws_gens_lock = threading.Lock()

_subscribe_inflight = {}
_subscribe_inflight_lock = threading.Lock()
_override_inflight = {}
_override_inflight_lock = threading.Lock()
_config_running = False
_config_lock = threading.Lock()
_rebuild_running = False
_rebuild_lock = threading.Lock()


def _command_session_id(data, ws=None):
    session_id = str((data or {}).get('session_id') or '')
    if session_id:
        if ws is not None:
            try:
                ws._cdx_session_id = session_id
            except Exception:
                pass
        return session_id
    if ws is not None:
        existing = str(getattr(ws, '_cdx_session_id', '') or '')
        if existing:
            return existing
        fallback = f'ws-{id(ws)}'
        try:
            ws._cdx_session_id = fallback
        except Exception:
            pass
        return fallback
    return ''


def _status_detail(status, fallback=''):
    progress = (status or {}).get('progress') or {}
    if progress.get('error'):
        return str(progress.get('error'))
    label = str(progress.get('label') or '').strip()
    if label:
        return label
    ready = progress.get('products_ready') or []
    if ready:
        return f'{len(ready)} products ready'
    return str(fallback or '')


def _monitor_background_task(
    task_id,
    client,
    running_keys,
    *,
    started_detail='',
    queued_detail='',
    timeout_s=1800,
    poll_s=0.5,
):
    start_deadline = time.time() + 2.0
    deadline = time.time() + timeout_s
    saw_running = False
    while time.time() < deadline:
        try:
            status = client.get_status() or {}
        except Exception as exc:
            task_registry.finish(task_id, status='error', detail=f'Status check failed: {exc}')
            return
        progress = status.get('progress') or {}
        if progress.get('error'):
            task_registry.finish(task_id, status='error', detail=str(progress.get('error')))
            return
        is_running = any(bool(status.get(key)) for key in tuple(running_keys or ()))
        detail = _status_detail(status, started_detail or queued_detail)
        if progress.get('done') and not is_running:
            task_registry.finish(task_id, status='done', detail=detail or started_detail or 'Done')
            return
        if is_running:
            saw_running = True
            task_registry.update(task_id, status='running', detail=detail or started_detail)
        elif not saw_running and time.time() < start_deadline:
            task_registry.update(task_id, status='queued', detail=queued_detail or started_detail or 'Queued')
        else:
            task_registry.finish(task_id, status='done', detail=detail or started_detail or 'Done')
            return
        time.sleep(poll_s)
    task_registry.finish(task_id, status='error', detail='Timed out waiting for background task')


def _next_gen(ws):
    ws_id = id(ws)
    with _ws_gens_lock:
        gen = _ws_gens.get(ws_id, 0) + 1
        _ws_gens[ws_id] = gen
        return gen


def _new_request_id(prefix):
    return f'{prefix}-{uuid.uuid4()}'


def _activate_request(ws, request_id):
    request_id = str(request_id or '')
    if not request_id:
        return
    now = time.time()
    cutoff = now - 600
    with _ws_gens_lock:
        for key, ts in list(_cancelled_request_ids.items()):
            if ts < cutoff:
                _cancelled_request_ids.pop(key, None)
        previous = _ws_request_ids.get(id(ws))
        if previous and previous != request_id:
            _cancelled_request_ids[previous] = now
        _ws_request_ids[id(ws)] = request_id


def _cancel_request(ws, request_id=None):
    now = time.time()
    with _ws_gens_lock:
        active = _ws_request_ids.get(id(ws)) if ws is not None else None
        target = str(request_id or active or '')
        if target:
            _cancelled_request_ids[target] = now
        if ws is not None and (not request_id or active == request_id):
            _ws_gens[id(ws)] = _ws_gens.get(id(ws), 0) + 1
            _ws_request_ids.pop(id(ws), None)
    return target


def _is_current(ws, gen):
    with _ws_gens_lock:
        return _ws_gens.get(id(ws), 0) == gen


def _is_current_request(ws, gen, request_id=None):
    request_id = str(request_id or '')
    with _ws_gens_lock:
        if _ws_gens.get(id(ws), 0) != gen:
            return False
        if request_id and _cancelled_request_ids.get(request_id):
            return False
        if request_id and _ws_request_ids.get(id(ws)) != request_id:
            return False
        return True


def _waiter_current(waiter):
    if len(waiter) == 4:
        w_ws, w_gen, _w_task_id, w_request_id = waiter
    else:
        w_ws, w_gen, _w_task_id = waiter
        w_request_id = None
    return _is_current_request(w_ws, w_gen, w_request_id)


def _cleanup_gen(ws):
    with _ws_gens_lock:
        _ws_gens.pop(id(ws), None)
        _ws_request_ids.pop(id(ws), None)


class _RenderSuperseded(Exception):
    pass


def _await_current(future, is_current, poll_s=2.0):
    while True:
        try:
            return future.result(timeout=poll_s)
        except FuturesTimeout:
            if not is_current():
                raise _RenderSuperseded()


def _iter_current_tab_futures(tab_specs, submit_fn, is_current, max_workers=2, poll_s=0.2):
    pending = list(tab_specs)
    futures = {}
    pool = ThreadPoolExecutor(max_workers=max_workers)

    def submit_more():
        while pending and len(futures) < max_workers:
            if not is_current():
                raise _RenderSuperseded()
            spec = pending.pop(0)
            futures[pool.submit(submit_fn, spec)] = spec

    try:
        submit_more()
        while futures:
            if not is_current():
                raise _RenderSuperseded()
            done, _ = wait(futures, timeout=poll_s, return_when=FIRST_COMPLETED)
            if not done:
                continue
            for future in done:
                spec = futures.pop(future)
                yield spec, future
                submit_more()
    except _RenderSuperseded:
        for future in futures:
            future.cancel()
        raise
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _prioritize_tab_specs(tab_specs, active_tab=None, anchors=()):
    tab_key = {
        'vol-grids': 'vol_grids',
        'trade-blotter': 'trade_blotter',
        'positions': 'positions',
        'expiry-charts': 'expiry_charts',
        'option-stack': 'option_stack',
    }.get(active_tab, active_tab)
    by_key = {key: (key, label) for key, label in tab_specs}
    ordered_keys = []
    for key in (tab_key, *anchors):
        if key in by_key and key not in ordered_keys:
            ordered_keys.append(key)
    ordered_keys.extend(key for key, _ in tab_specs if key not in ordered_keys)
    return [by_key[key] for key in ordered_keys]


def _requested_tab_keys(data):
    allowed = {'vol_grids', 'trade_blotter', 'positions', 'expiry_charts', 'option_stack'}
    requested = data.get('requested_tabs') or data.get('requested_tab')
    if requested is None:
        return None
    if isinstance(requested, str):
        requested = [requested]
    keys = [str(k).strip() for k in requested if str(k).strip() in allowed]
    return keys or None


def _has_selection(value):
    if value is None or value == '':
        return False
    if isinstance(value, (list, tuple)):
        return any(v is not None and v != '' for v in value)
    return True


def set_command_handler(fn):
    global _command_handler
    _command_handler = fn


def _handle_ws_command(cmd, data, ws=None):
    if cmd == 'client_diag':
        _handle_client_diag(data, ws)
        return
    if cmd == 'cancel_job' and ws:
        _handle_cancel_job(data, ws)
        return
    from dash_reports.sub_servers.cdx_options_server.client import get_client
    client = get_client()
    if cmd == 'init' and ws:
        _handle_init(data, ws)
        return
    if cmd == 'set_refresh_interval':
        seconds = data.get('seconds', 60)
        client.set_refresh_interval(seconds)
        return
    if cmd == 'start_refresh':
        client.start_manual_refresh()
        return
    if cmd == 'subscribe' and ws:
        _handle_subscribe(data, ws)
        return
    if cmd == 'override' and ws:
        _handle_override(data, ws)
        return
    if cmd == 'config':
        _handle_config(data, ws)
        return
    if cmd == 'rebuild_grids':
        _handle_rebuild(data, ws)


def _handle_cancel_job(data, ws):
    request_id = str((data or {}).get('request_id') or '')
    cancelled = _cancel_request(ws, request_id=request_id)
    logging.info(f'WS cancel_job: request_id={cancelled or request_id or ""} ws={id(ws)}')
    try:
        ws.send(_safe_dumps({'type': 'compute_status', 'text': '', 'request_id': cancelled or request_id}))
    except Exception:
        pass


def _handle_client_diag(data, ws):
    category = str(data.get('category') or 'info')
    reason = str(data.get('reason') or '')
    wanted = _selection_label(data.get('product'), data.get('strike'), data.get('expiry'))
    incoming = _selection_label(
        data.get('incoming_product'),
        data.get('incoming_strike'),
        data.get('incoming_expiry'),
    )
    pieces = [f'WS client_diag[{category}]']
    if reason:
        pieces.append(reason)
    if wanted:
        pieces.append(f'wanted={wanted}')
    if incoming:
        pieces.append(f'incoming={incoming}')
    if data.get('tab'):
        pieces.append(f'tab={data.get("tab")}')
    if data.get('waited_ms') is not None:
        pieces.append(f'waited_ms={int(data.get("waited_ms") or 0)}')
    if data.get('source'):
        pieces.append(f'source={data.get("source")}')
    if ws is not None:
        pieces.append(f'ws={id(ws)}')
    message = ' '.join(pieces)
    if category in {'stale_tab_data', 'loading_timeout', 'error'}:
        logging.warning(message)
    else:
        logging.info(message)


def _handle_config(data, ws):
    global _config_running
    mark_time = data.get('mark_time', '')
    thresh_time = data.get('thresh_time', '4h')
    mark_pos = data.get('mark_pos', True)
    task_id = task_registry.start(
        'remark' if mark_time else 'refresh',
        f'Re-mark @ {mark_time}' if mark_time else 'Refresh live data',
        session_id=_command_session_id(data, ws),
        status='queued',
        detail=f'thresh={thresh_time} | mark_pos={bool(mark_pos)}',
    )
    with _config_lock:
        if _config_running:
            logging.info('WS config: already running, skipping')
            task_registry.finish(task_id, status='cancelled', detail='Refresh already in flight')
            if ws:
                _send_task_state(ws)
            return
        _config_running = True

    logging.info(f'WS config: mark_time={mark_time} thresh_time={thresh_time} mark_pos={mark_pos}')

    def _run():
        global _config_running
        try:
            from dash_reports.sub_servers.cdx_options_server.client import get_client
            client = get_client()
            result = client.refresh(mark_pos=mark_pos, mark_time=mark_time, thresh_time=thresh_time)
            status_name = str((result or {}).get('status') or '')
            if status_name in {'already_refreshing', 'already_loading'}:
                task_registry.update(task_id, status='queued', detail='Waiting for in-flight refresh')
            _monitor_background_task(
                task_id,
                client,
                ('loading', 'refreshing'),
                started_detail=f'Re-mark @ {mark_time}' if mark_time else 'Refreshing live data',
                queued_detail='Queued for refresh',
            )
        except Exception:
            logging.exception('WS config refresh failed')
            task_registry.finish(task_id, status='error', detail='Refresh failed')
            if ws:
                _send_error(ws, 'config', '', 'refresh_failed')
        finally:
            with _config_lock:
                _config_running = False

    threading.Thread(target=_run, daemon=True).start()


def _handle_rebuild(data, ws):
    global _rebuild_running
    task_id = task_registry.start(
        'rebuild',
        'Rebuild pricing grids',
        session_id=_command_session_id(data, ws),
        scope='global',
        status='queued',
        detail='Queued for rebuild',
    )
    with _rebuild_lock:
        if _rebuild_running:
            logging.info('WS rebuild_grids: already running, skipping')
            task_registry.finish(task_id, status='cancelled', detail='Rebuild already in flight')
            if ws:
                _send_task_state(ws)
            return
        _rebuild_running = True

    logging.info('WS rebuild_grids')

    def _run():
        global _rebuild_running
        try:
            from dash_reports.sub_servers.cdx_options_server.client import get_client
            client = get_client()
            result = client.rebuild_grids()
            status_name = str((result or {}).get('status') or '')
            if status_name == 'rebuild_in_progress':
                task_registry.update(task_id, status='queued', detail='Waiting for in-flight rebuild')
            _monitor_background_task(
                task_id,
                client,
                ('rebuilding',),
                started_detail='Rebuilding pricing grids',
                queued_detail='Queued for rebuild',
                timeout_s=3600,
            )
        except Exception:
            logging.exception('WS rebuild_grids failed')
            task_registry.finish(task_id, status='error', detail='Grid rebuild failed')
            if ws:
                _send_error(ws, 'rebuild_grids', '', 'rebuild_failed')
        finally:
            with _rebuild_lock:
                _rebuild_running = False

    threading.Thread(target=_run, daemon=True).start()


def _send_progress(ws, status):
    prog = (status or {}).get('progress', {})
    if prog:
        ws.send(_safe_dumps({'type': 'progress', **prog}))
        logging.info(f'CDX WS sent progress: done={prog.get("done")} products={len(prog.get("products_ready", []))}')


def _send_status_info(ws, client):
    try:
        info = client.get_status_info()
        if info:
            ws.send(_safe_dumps({'type': 'status_info', **info}))
    except Exception as e:
        logging.warning(f'CDX WS status_info failed: {e}')


def _send_activity(ws, client):
    try:
        activity = client.get_activity()
        if activity:
            ws.send(_safe_dumps({'type': 'activity', **activity}))
    except Exception as e:
        logging.warning(f'CDX WS activity snapshot failed: {e}')


def _send_defaults(ws, client, product=None, strike=None, expiry=None):
    try:
        defs = client.pick_defaults(product, strike, expiry)
        if defs and defs.get('product_options'):
            ws.send(_safe_dumps({'type': 'defaults', **defs}))
            logging.info(f'CDX WS sent defaults: {len(defs.get("product_options", []))} products')
    except Exception as e:
        logging.warning(f'CDX WS defaults failed: {e}')


def _send_spot_snapshot(ws, client):
    try:
        spot = client.get_spot_snapshot()
        if spot:
            ws.send(_safe_dumps({'type': 'spot_update', **spot}))
            logging.info(f'CDX WS sent spot snapshot: {len((spot or {}).get("items", []))} items')
    except Exception as e:
        logging.warning(f'CDX WS spot snapshot failed: {e}')


def _send_task_state(ws):
    try:
        ws.send(_safe_dumps({'type': 'task_state', **task_registry.snapshot()}))
    except Exception as e:
        logging.warning(f'CDX WS task_state failed: {e}')


def _handle_init(data, ws):
    task_id = task_registry.start(
        'load',
        'Initialize CDX options',
        session_id=_command_session_id(data, ws),
        status='queued',
        detail=', '.join((data.get('tickers') or [])[:3]),
    )

    def _run():
        try:
            from dash_reports.sub_servers.cdx_options_server.client import get_client
            client = get_client()
            result = client.load(
                tickers=data.get('tickers'),
                todt=data.get('todt'),
                lookback=data.get('lookback', '6m'),
                mark_pos=data.get('mark_pos', True),
                mark_time=data.get('mark_time', ''),
                thresh_time=data.get('thresh_time', '4h'),
            )
            client.set_refresh_interval(data.get('refresh', 300))
            status = client.get_status() or {}
            _send_progress(ws, status)
            _send_status_info(ws, client)
            _send_activity(ws, client)
            _send_defaults(ws, client, data.get('active_product'), data.get('strike'), data.get('expiry'))
            _send_spot_snapshot(ws, client)
            _send_task_state(ws)
            status_name = str((result or {}).get('status') or '')
            if status_name in {'already_loading', 'already_refreshing'}:
                task_registry.update(task_id, status='queued', detail='Waiting for loader')
            _monitor_background_task(
                task_id,
                client,
                ('loading', 'refreshing'),
                started_detail='Loading CDX options data',
                queued_detail='Queued to initialize',
            )
        except Exception:
            logging.exception('WS init failed')
            task_registry.finish(task_id, status='error', detail='Initialization failed')
            _send_error(ws, 'init', data.get('active_product') or '', 'init_failed')

    threading.Thread(target=_run, daemon=True).start()


def _relay_subscribe_messages(entry):
    with entry['delivery_lock']:
        with _subscribe_inflight_lock:
            waiters = list(entry['waiters'])
            messages = list(entry['messages'])
        for waiter in waiters:
            if not _waiter_current(waiter):
                continue
            ws, gen, task_id, request_id = waiter
            key = (id(ws), gen, request_id)
            sent = entry['delivered'].get(key, 0)
            for msg in messages[sent:]:
                ws.send(_message_for_request(msg, request_id))
                sent += 1
                entry['delivered'][key] = sent


def _handle_subscribe(data, ws):
    product = data.get('product')
    if not product:
        return
    strike = data.get('strike')
    expiry = data.get('expiry')
    defaults_only = bool(data.get('defaults_only')) or not (_has_selection(strike) or _has_selection(expiry))
    active_tab = data.get('active_tab')
    requested_tabs = tuple(_requested_tab_keys(data) or ())
    request_id = data.get('request_id') or _new_request_id('sub')
    task_id = task_registry.start(
        'defaults' if defaults_only else 'subscribe',
        'Load selection defaults' if defaults_only else 'Render selected tabs',
        session_id=_command_session_id(data, ws),
        status='queued',
        detail=_selection_label(product, strike, expiry) or product,
        product=product,
        strike=strike,
        expiry=expiry,
    )
    diag_category = data.get('diag_category')
    diag_reason = data.get('diag_reason')
    diag_tab = data.get('diag_tab')
    diag_waited_ms = data.get('diag_waited_ms')
    gen = _next_gen(ws)
    _activate_request(ws, request_id)
    flight_key = ('defaults_only' if defaults_only else 'full', product, str(strike), str(expiry), requested_tabs)
    event_bus.set_selection(
        ws, product, strike, expiry,
        requested_tabs=requested_tabs, active_tab=active_tab,
    )
    event_bus.set_override_active(ws, False)
    logging.info(f'WS subscribe: product={product} strike={strike} expiry={expiry} defaults_only={defaults_only} gen={gen} tabs={list(requested_tabs) or "all"}')
    if diag_category:
        logging.warning(
            'WS subscribe diag[%s]: reason=%s product=%s strike=%s expiry=%s tab=%s waited_ms=%s gen=%s',
            diag_category,
            diag_reason,
            product,
            strike,
            expiry,
            diag_tab,
            diag_waited_ms,
            gen,
        )

    with _subscribe_inflight_lock:
        existing = _subscribe_inflight.get(flight_key)
        if existing is not None:
            task_registry.update(task_id, status='queued', detail='Waiting for identical request already in flight')
            existing['waiters'].append((ws, gen, task_id, request_id))
            logging.info(f'WS subscribe gen={gen}: coalesced with inflight for {product} ({len(existing["waiters"])} waiters)')
        else:
            entry = {'waiters': [], 'messages': [], 'task_id': task_id,
                     'delivery_lock': threading.Lock(), 'delivered': {}}
            _subscribe_inflight[flight_key] = entry
    if existing is not None:
        _relay_subscribe_messages(existing)
        return

    def _stream():
        sent_count = 0
        def owner_current():
            return _is_current_request(ws, gen, request_id)

        def job_allowed():
            if owner_current():
                return True
            with _subscribe_inflight_lock:
                waiters = list(entry.get('waiters', []))
            return any(_waiter_current(waiter) for waiter in waiters)

        interactive_activity.begin()
        try:
            if not job_allowed():
                logging.info(f'WS subscribe gen={gen} superseded before compute, skipping')
                task_registry.finish(task_id, status='cancelled', detail='Superseded by newer selection')
                _send_error(ws, 'subscribe', product, 'superseded', request_id=request_id)
                return

            from dash_reports.sub_servers.cdx_options_server.client import get_client
            client = get_client()
            early_cached = None
            early_keys = set()
            if (_has_selection(strike) and _has_selection(expiry)
                    and hasattr(client, 'get_cached_tabs')):
                try:
                    early_cached = client.get_cached_tabs(
                        product, strike, expiry, wait_s=0, requested_tabs=list(requested_tabs) or None,
                    )
                    early_keys = {
                        key for key in (requested_tabs or ('vol_grids', 'trade_blotter', 'positions',
                                                          'expiry_charts', 'option_stack'))
                        if _tab_payload_has_content(key, (early_cached or {}).get(key))
                    }
                    if early_keys and job_allowed():
                        payload = dict(early_cached, type='tab_data', product=product,
                                       strike=strike, expiry=expiry, request_id=request_id, _source='subscribe')
                        if payload.get('_stale'):
                            payload['_cache_state'] = 'cached-stale'
                        msg = _safe_dumps(payload)
                        entry['messages'].append(msg)
                        if owner_current():
                            ws.send(msg)
                        _relay_subscribe_messages(entry)
                        logging.info('WS subscribe gen=%s: saved view sent before defaults for %s tabs=%s',
                                     gen, product, sorted(early_keys))
                        if (requested_tabs and early_keys == set(requested_tabs)
                                and not payload.get('_stale')
                                and payload.get('_cache_state') not in ('stale', 'cached-stale')):
                            sent_count = len(early_keys)
                            task_registry.finish(task_id, detail=f'{sent_count}/{sent_count} tabs ready from cache')
                            return
                except Exception:
                    logging.exception('WS subscribe gen=%s: early cached tab lookup failed', gen)
            task_registry.update(task_id, status='running', detail='Resolving defaults')
            render_product, render_strike, render_expiry = product, strike, expiry

            try:
                defs = client.pick_defaults(product, strike, expiry)
                if defs:
                    render_product = defs.get('active_product') or render_product
                    if not _has_selection(strike):
                        render_strike = defs.get('strike') if defs.get('strike') is not None else render_strike
                    if not _has_selection(expiry):
                        render_expiry = defs.get('expiry') if defs.get('expiry') is not None else render_expiry
                    event_bus.set_selection(
                        ws, render_product, render_strike, render_expiry,
                        requested_tabs=requested_tabs, active_tab=active_tab,
                    )
                if defs and job_allowed():
                    defs_msg = _safe_dumps({'type': 'defaults', **defs, 'request_id': request_id})
                    entry['messages'].append(defs_msg)
                    if owner_current():
                        ws.send(defs_msg)
                    _relay_subscribe_messages(entry)
            except Exception:
                logging.exception(f'WS subscribe gen={gen}: pick_defaults failed')

            if defaults_only:
                task_registry.finish(task_id, status='done' if owner_current() else 'cancelled',
                                     detail='Defaults ready' if owner_current() else 'Superseded by newer selection')
                return

            if not job_allowed():
                task_registry.finish(task_id, status='cancelled', detail='Superseded by newer selection')
                return

            base_payload = {
                'product': render_product,
                'strike': render_strike,
                'expiry': render_expiry,
                '_source': 'subscribe',
                'request_id': request_id,
            }
            try:
                context_result = client.get_calc_context(render_product, render_strike, render_expiry)
                base_payload.update({
                    'product': context_result.get('product', render_product),
                    'strike': context_result.get('strike', render_strike),
                    'expiry': context_result.get('expiry', render_expiry),
                    'calc_context': context_result.get('calc_context', {}),
                    '_calc_sig': context_result.get('_calc_sig'),
                    'defaults': context_result.get('defaults'),
                    'update_text': context_result.get('update_text'),
                    'grid_badge': context_result.get('grid_badge'),
                    'override_text': context_result.get('override_text', ''),
                })
            except Exception:
                logging.exception(f'WS subscribe gen={gen}: get_calc_context failed')

            try:
                _parts = [f'\u2699 Rendering {render_product}']
                if render_strike:
                    _parts.append(f'K={render_strike}')
                if render_expiry:
                    _parts.append(f'Exp={render_expiry}')
                if owner_current():
                    ws.send(json.dumps({
                        'type': 'compute_status',
                        'text': ' '.join(_parts) + '...',
                        'request_id': request_id,
                        'product': base_payload.get('product', render_product),
                        'strike': base_payload.get('strike', render_strike),
                        'expiry': base_payload.get('expiry', render_expiry),
                    }))
                    task_registry.update(task_id, status='running', detail=f'Rendering 0/{len(requested_tabs) or 5} tabs')
            except Exception:
                pass

            event_bus.suppress(ws)
            _TAB_LABELS = {'vol_grids': 'Grids', 'trade_blotter': 'Blotter',
                          'positions': 'Positions', 'expiry_charts': 'Charts', 'option_stack': 'Stack'}
            def _render_tab(key):
                with stage_timer('subscribe_tab', product=render_product, tab=key):
                    if hasattr(client, 'render_materialized_tabs'):
                        result = client.render_materialized_tabs(
                            render_product,
                            render_strike,
                            render_expiry,
                            requested_tabs=[key],
                        )
                        return result.get(key, {}) if isinstance(result, dict) else {}
                    renderer = getattr(client, f'render_{key}')
                    return renderer(
                        render_product,
                        render_strike,
                        render_expiry,
                        suppress_push=True,
                    )

            tab_rpcs = [
                ('vol_grids', lambda: _render_tab('vol_grids')),
                ('option_stack', lambda: _render_tab('option_stack')),
                ('expiry_charts', lambda: _render_tab('expiry_charts')),
                ('positions', lambda: _render_tab('positions')),
                ('trade_blotter', lambda: _render_tab('trade_blotter')),
            ]
            if requested_tabs:
                requested_set = set(requested_tabs)
                tab_rpcs = [spec for spec in tab_rpcs if spec[0] in requested_set]
            tab_rpcs = _prioritize_tab_specs(tab_rpcs, active_tab=active_tab, anchors=('vol_grids',))
            all_labels = [_TAB_LABELS[k] for k, _ in tab_rpcs]
            done_labels = []
            total_tabs = len(tab_rpcs) or 1
            cached_keys = set()
            try:
                cached_tabs = early_cached if early_cached is not None else (
                    client.get_cached_tabs(
                        render_product,
                        render_strike,
                        render_expiry,
                        wait_s=0,
                        requested_tabs=[key for key, _ in tab_rpcs],
                    )
                    if hasattr(client, 'get_cached_tabs') else {}
                )
                cached_stale = bool(
                    isinstance(cached_tabs, dict)
                    and (cached_tabs.get('_stale') or cached_tabs.get('_cache_state') in ('stale', 'cached-stale'))
                )
                cache_meta = {
                    name: cached_tabs.get(name)
                    for name in (
                        '_cache_state', '_stale', '_stale_reason', '_stale_at',
                        '_materialized_at', '_data_asof', '_generation', '_refresh_duration_ms',
                    )
                    if isinstance(cached_tabs, dict) and cached_tabs.get(name) is not None
                }
                if cached_stale:
                    cache_meta['_cache_state'] = 'cached-stale'
                for key, _ in list(tab_rpcs):
                    result = cached_tabs.get(key) if isinstance(cached_tabs, dict) else None
                    if not result or (cached_stale and not _tab_payload_has_content(key, result)):
                        if result and cached_stale:
                            logging.info(f'WS subscribe gen={gen}: skipped empty stale {key} for {render_product}')
                        continue
                    if isinstance(result, dict):
                        result['calc_context'] = base_payload.get('calc_context')
                        result['_calc_sig'] = base_payload.get('_calc_sig')
                    msg = _safe_dumps({'type': 'tab_data', **base_payload, **cache_meta, key: result})
                    if key not in early_keys:
                        entry['messages'].append(msg)
                        if owner_current():
                            ws.send(msg)
                        _relay_subscribe_messages(entry)
                    if cached_stale:
                        task_registry.update(
                            task_id,
                            status='running',
                            detail=f'Cached stale view shown; refreshing {_TAB_LABELS.get(key, key)}',
                        )
                        continue
                    sent_count += 1
                    cached_keys.add(key)
                    done_labels.append(_TAB_LABELS.get(key, key))
                    if owner_current():
                        task_registry.update(
                            task_id,
                            status='running',
                            detail=f'{sent_count}/{total_tabs} tabs ready from cache: {", ".join(done_labels)}',
                        )
                if cached_keys:
                    logging.info(f'WS subscribe gen={gen}: streamed cached {sorted(cached_keys)} for {render_product}')
                    tab_rpcs = [spec for spec in tab_rpcs if spec[0] not in cached_keys]
            except Exception:
                logging.exception(f'WS subscribe gen={gen}: cached tab lookup failed')

            try:
                for (key, _), future in _iter_current_tab_futures(
                    tab_rpcs,
                    lambda spec: spec[1](),
                    job_allowed,
                    max_workers=1,
                ):
                    if not job_allowed():
                        logging.info(f'WS subscribe gen={gen} superseded during stream, aborting remaining')
                        task_registry.finish(task_id, status='cancelled', detail='Superseded by newer selection')
                        break
                    try:
                        result = _await_current(future, job_allowed)
                        if result:
                            if isinstance(result, dict):
                                result['calc_context'] = base_payload.get('calc_context')
                                result['_calc_sig'] = base_payload.get('_calc_sig')
                            msg = _safe_dumps({'type': 'tab_data', **base_payload, key: result})
                            entry['messages'].append(msg)
                            sent_count += 1
                            done_labels.append(_TAB_LABELS.get(key, key))
                            if owner_current():
                                ws.send(msg)
                                task_registry.update(
                                    task_id,
                                    status='running',
                                    detail=f'{sent_count}/{total_tabs} tabs ready: {", ".join(done_labels)}',
                                )
                                remaining = [l for l in all_labels if l not in done_labels]
                                if remaining:
                                    try:
                                        ws.send(json.dumps({'type': 'compute_status',
                                            'text': f'\u2699 {", ".join(done_labels)} done \u2502 {", ".join(remaining)}...',
                                            'request_id': request_id,
                                            'product': base_payload.get('product', render_product),
                                            'strike': base_payload.get('strike', render_strike),
                                            'expiry': base_payload.get('expiry', render_expiry)}))
                                    except Exception:
                                        pass
                            _relay_subscribe_messages(entry)
                            logging.info(f'WS subscribe gen={gen}: streamed {key} for {render_product} ({sent_count}/{total_tabs})')
                        else:
                            msg = _safe_dumps({'type': 'tab_data', **base_payload, key: {'_empty': True}})
                            entry['messages'].append(msg)
                            sent_count += 1
                            if owner_current():
                                ws.send(msg)
                            _relay_subscribe_messages(entry)
                            logging.warning(f'WS subscribe gen={gen}: {key} returned empty for {render_product}')
                    except _RenderSuperseded:
                        raise
                    except Exception:
                        logging.exception(f'WS subscribe gen={gen}: {key} failed for {render_product}')
                        msg = _safe_dumps({'type': 'tab_data', **base_payload, key: {'_error': True}})
                        try:
                            entry['messages'].append(msg)
                            sent_count += 1
                            if owner_current():
                                ws.send(msg)
                            _relay_subscribe_messages(entry)
                        except Exception:
                            pass
            except _RenderSuperseded:
                logging.info(f'WS subscribe gen={gen} superseded before scheduling remaining tabs')
                task_registry.finish(task_id, status='cancelled', detail='Superseded by newer selection')

            if owner_current():
                if sent_count > 0:
                    task_registry.finish(task_id, status='done', detail=f'{sent_count}/{total_tabs} tabs ready')
                    logging.info(f'WS subscribe gen={gen}: all tabs streamed for {render_product} ({sent_count}/{total_tabs})')
                else:
                    task_registry.finish(task_id, status='error', detail='No tabs returned data')
                    _send_error(ws, 'subscribe', render_product, 'empty_result', request_id=request_id)
            else:
                task_registry.finish(task_id, status='cancelled', detail='Superseded by newer selection')
        except Exception:
            logging.exception(f'WS subscribe gen={gen}: _stream crashed for {product}')
            task_registry.finish(task_id, status='error', detail='Render failed')
        finally:
            with _subscribe_inflight_lock:
                waiters = entry.get('waiters', [])
                messages = entry.get('messages', [])
                _subscribe_inflight.pop(flight_key, None)

            if waiters and messages:
                logging.info(f'WS subscribe gen={gen}: relaying {len(messages)} msgs to {len(waiters)} waiters')
                _relay_subscribe_messages(entry)
                for waiter in waiters:
                    if len(waiter) == 4:
                        w_ws, w_gen, w_task_id, w_request_id = waiter
                    else:
                        w_ws, w_gen, w_task_id = waiter
                        w_request_id = None
                    if _is_current_request(w_ws, w_gen, w_request_id):
                        event_bus.suppress(w_ws)
                        task_registry.finish(w_task_id, status='done', detail='Reused identical in-flight result')
                    else:
                        task_registry.finish(w_task_id, status='cancelled', detail='Superseded by newer selection')
            elif waiters:
                logging.warning(f'WS subscribe gen={gen}: {len(waiters)} waiters got no data for {product}')
                for waiter in waiters:
                    w_task_id = waiter[2]
                    task_registry.finish(w_task_id, status='error', detail='No data returned')
            interactive_activity.end()
            logging.info(f'WS subscribe gen={gen}: cleanup done for {flight_key} (sent={sent_count})')

    threading.Thread(target=_stream, daemon=True).start()


def _handle_override(data, ws):
    product = data.get('product')
    if not product:
        return
    strike = data.get('strike')
    expiry = data.get('expiry')
    ref_px = data.get('ref_px')
    ref_bps = data.get('ref_bps')
    vol_shift_bps = data.get('vol_shift_bps')
    vol_lookback = data.get('vol_lookback', '5b')
    active_tab = data.get('active_tab')
    requested_tabs = tuple(_requested_tab_keys(data) or ())
    request_id = data.get('request_id') or _new_request_id('ovr')
    task_id = task_registry.start(
        'override',
        'Recalculate selected tabs',
        session_id=_command_session_id(data, ws),
        status='queued',
        detail=_selection_label(product, strike, expiry) or product,
        product=product,
        strike=strike,
        expiry=expiry,
    )
    gen = _next_gen(ws)
    _activate_request(ws, request_id)
    event_bus.set_selection(
        ws, product, strike, expiry,
        requested_tabs=requested_tabs, active_tab=active_tab,
    )
    has_override = (ref_px is not None or ref_bps is not None
                    or (vol_shift_bps not in (None, 0))
                    or (vol_lookback and vol_lookback != '5b'))
    override_params = {
        'product': product, 'strike': strike, 'expiry': expiry,
        'ref_px': ref_px, 'ref_bps': ref_bps,
        'vol_shift_bps': vol_shift_bps, 'vol_lookback': vol_lookback,
    }
    if request_id is not None:
        override_params['request_id'] = request_id
    event_bus.set_override_active(ws, has_override, params=override_params if has_override else None)
    event_bus.suppress(ws, seconds=20)
    flight_key = (product, str(strike), str(expiry), str(ref_px), str(ref_bps), str(vol_shift_bps), str(vol_lookback), requested_tabs)
    logging.info(f'WS override: product={product} gen={gen} active={has_override}')

    with _override_inflight_lock:
        existing = _override_inflight.get(flight_key)
        if existing is not None:
            task_registry.update(task_id, status='queued', detail='Waiting for identical recalculation already in flight')
            for msg in existing.get('messages', []):
                try:
                    ws.send(_message_for_request(msg, request_id))
                except Exception:
                    break
            existing['waiters'].append((ws, gen, task_id, request_id))
            logging.info(f'WS override gen={gen}: coalesced with inflight for {product}')
            return
        entry = {'waiters': [], 'messages': [], 'task_id': task_id}
        _override_inflight[flight_key] = entry

    def _compute():
        tab_specs = [
            ('vol_grids', 'Vol Grids'),
            ('option_stack', 'Option Stack'),
            ('expiry_charts', 'Expiry Charts'),
            ('positions', 'Positions'),
            ('trade_blotter', 'Trade Blotter'),
        ]
        if requested_tabs:
            requested_set = set(requested_tabs)
            tab_specs = [spec for spec in tab_specs if spec[0] in requested_set]
        tab_specs = _prioritize_tab_specs(
            tab_specs,
            active_tab=active_tab,
            anchors=('vol_grids', 'option_stack', 'expiry_charts'),
        )
        base_payload = {}
        completed = []
        failed = []
        sent_tab_count = 0

        def owner_current():
            return _is_current_request(ws, gen, request_id)

        def job_allowed():
            if owner_current():
                return True
            with _override_inflight_lock:
                waiters = list(entry.get('waiters', []))
            return any(_waiter_current(waiter) for waiter in waiters)

        def send_msg(payload, detail=None):
            msg = _safe_dumps(payload)
            with _override_inflight_lock:
                entry['messages'].append(msg)
                waiters = list(entry.get('waiters', []))
            targets = [(ws, gen, task_id, request_id)] + waiters
            for target in targets:
                if len(target) == 4:
                    target_ws, target_gen, target_task_id, target_request_id = target
                else:
                    target_ws, target_gen, target_task_id = target
                    target_request_id = None
                if not _is_current_request(target_ws, target_gen, target_request_id):
                    continue
                try:
                    if payload.get('type') == 'tab_data':
                        event_bus.suppress(target_ws)
                    target_ws.send(_message_for_request(msg, target_request_id))
                    if detail:
                        task_registry.update(target_task_id, status='running', detail=detail)
                except Exception:
                    logging.exception('WS override gen=%s: failed sending progressive payload for %s', gen, product)

        def send_status(text):
            payload = {
                'type': 'compute_status',
                'text': text,
                'product': base_payload.get('product', product),
                'strike': base_payload.get('strike', strike),
                'expiry': base_payload.get('expiry', expiry),
            }
            if request_id is not None:
                payload['request_id'] = request_id
            send_msg(payload)

        def tab_call(tab_key):
            from dash_reports.sub_servers.cdx_options_server.client import CDXFlightClient
            client = CDXFlightClient()
            try:
                if tab_key == 'vol_grids':
                    return client.render_vol_grids(
                        product, strike, expiry,
                        ref_px=ref_px, ref_bps=ref_bps,
                        vol_shift_bps=vol_shift_bps, vol_lookback=vol_lookback,
                        suppress_push=True,
                    )
                if tab_key == 'trade_blotter':
                    return client.render_trade_blotter(
                        product, strike, expiry,
                        ref_px=ref_px, ref_bps=ref_bps, vol_shift_bps=vol_shift_bps,
                        suppress_push=True,
                    )
                if tab_key == 'positions':
                    return client.render_positions(
                        product, strike, expiry,
                        ref_px=ref_px, ref_bps=ref_bps, vol_shift_bps=vol_shift_bps,
                        suppress_push=True,
                    )
                if tab_key == 'expiry_charts':
                    return client.render_expiry_charts(
                        product, strike, expiry,
                        ref_px=ref_px, ref_bps=ref_bps,
                        vol_shift_bps=vol_shift_bps, vol_lookback=vol_lookback,
                        suppress_push=True,
                    )
                if tab_key == 'option_stack':
                    return client.render_option_stack(product, strike, expiry, ref_px=ref_px, ref_bps=ref_bps, suppress_push=True)
                raise ValueError(f'unknown override tab {tab_key}')
            finally:
                client.close()

        interactive_activity.begin()
        try:
            if not job_allowed():
                task_registry.finish(task_id, status='cancelled', detail='Superseded by newer selection')
                _send_error(ws, 'override', product, 'superseded', request_id=request_id)
                return

            from dash_reports.sub_servers.cdx_options_server.client import get_client
            context_result = get_client().get_calc_context(
                product, strike, expiry,
                ref_px=ref_px, ref_bps=ref_bps,
                vol_shift_bps=vol_shift_bps, vol_lookback=vol_lookback,
            )
            base_payload = {
                'product': context_result.get('product', product),
                'strike': context_result.get('strike', strike),
                'expiry': context_result.get('expiry', expiry),
                'calc_context': context_result.get('calc_context', {}),
                '_calc_sig': context_result.get('_calc_sig'),
                'defaults': context_result.get('defaults'),
                'update_text': context_result.get('update_text'),
                'grid_badge': context_result.get('grid_badge'),
                'override_text': context_result.get('override_text', ''),
                '_source': 'override',
                'request_id': request_id,
            }
            send_msg({'type': 'tab_data', **base_payload}, detail='Accepted recalculation; starting tabs')
            send_status(f'⚙ Recalculating {product}: starting tabs...')

            try:
                for (tab_key, label), future in _iter_current_tab_futures(
                    tab_specs,
                    lambda spec: tab_call(spec[0]),
                    job_allowed,
                    max_workers=1,
                ):
                    if not job_allowed():
                        task_registry.finish(task_id, status='cancelled', detail='Superseded by newer selection')
                        return
                    try:
                        tab_result = _await_current(future, job_allowed)
                        if not tab_result:
                            failed.append(label)
                            send_status(f'⚠ {label} returned no data')
                            continue
                        completed.append(label)
                        sent_tab_count += 1
                        payload = {'type': 'tab_data', **base_payload, tab_key: tab_result}
                        if isinstance(tab_result, dict):
                            tab_result['calc_context'] = base_payload.get('calc_context')
                            tab_result['_calc_sig'] = base_payload.get('_calc_sig')
                        detail = f'Updated {len(completed)}/{len(tab_specs)} tabs'
                        if failed:
                            detail += f'; failed {", ".join(failed)}'
                        send_msg(payload, detail=detail)
                        logging.info(f'WS override gen={gen}: streamed {tab_key} for {product} ({detail})')
                        remaining = [label for key, label in tab_specs if label not in completed and label not in failed]
                        if remaining:
                            send_status(f'⚙ Done: {", ".join(completed)} | Running: {", ".join(remaining)}')
                    except _RenderSuperseded:
                        raise
                    except Exception:
                        logging.exception(f'WS override gen={gen}: {tab_key} failed for {product}')
                        failed.append(label)
                        send_status(f'⚠ {label} failed; continuing other tabs')
            except _RenderSuperseded:
                task_registry.finish(task_id, status='cancelled', detail='Superseded by newer selection')
                return

            if completed or sent_tab_count:
                detail = f'Updated {len(completed)}/{len(tab_specs)} tabs'
                if failed:
                    detail += f'; failed {", ".join(failed)}'
                task_registry.finish(task_id, status='done' if owner_current() else 'cancelled',
                                     detail=detail if owner_current() else 'Superseded by newer selection')
            else:
                task_registry.finish(task_id, status='error', detail='No tabs returned data')
                _send_error(ws, 'override', product, 'empty_result', request_id=request_id)
        except Exception:
            logging.exception(f'WS override gen={gen}: _compute crashed for {product}')
            task_registry.finish(task_id, status='error', detail='Recalculation failed')
            _send_error(ws, 'override', product, 'compute_failed', request_id=request_id)
        finally:
            with _override_inflight_lock:
                waiters = entry.get('waiters', [])
                _override_inflight.pop(flight_key, None)

            final_detail = f'Updated {len(completed)}/{len(tab_specs)} tabs' if completed else 'No tabs returned data'
            if failed:
                final_detail += f'; failed {", ".join(failed)}'
            for waiter in waiters:
                if len(waiter) == 4:
                    w_ws, w_gen, w_task_id, _w_request_id = waiter
                else:
                    w_ws, w_gen, w_task_id = waiter
                waiter_request_id = waiter[3] if len(waiter) == 4 else None
                if _is_current_request(w_ws, w_gen, waiter_request_id):
                    task_registry.finish(w_task_id, status='done' if completed else 'error', detail=f'Reused recalculation: {final_detail}')
                else:
                    task_registry.finish(w_task_id, status='cancelled', detail='Superseded by newer selection')
            send_status('')
            interactive_activity.end()
            logging.info(f'WS override gen={gen}: cleanup done for {flight_key}')

    threading.Thread(target=_compute, daemon=True).start()


def _send_error(ws, cmd, product, reason, request_id=None):
    try:
        payload = {
            'type': 'compute_error',
            'command': cmd,
            'product': product,
            'reason': reason,
        }
        if request_id is not None:
            payload['request_id'] = request_id
        ws.send(json.dumps(payload))
    except Exception:
        pass


set_command_handler(_handle_ws_command)


def _send_initial_state(ws):
    time.sleep(0.3)
    try:
        from dash_reports.sub_servers.cdx_options_server.client import get_client
        client = get_client()
        status = client.get_status() or {}
        _send_progress(ws, status)
        _send_status_info(ws, client)
        _send_activity(ws, client)
        _send_spot_snapshot(ws, client)
        _send_task_state(ws)
    except Exception as e:
        logging.warning(f'CDX WS initial status failed: {e}')


def trigger_override_recompute_for_active_clients(reason='vol_update', product_filter=None):
    """Server-side trigger: re-render every active-override client at their stored params.

    Call this AFTER vol-mark-changing events (staged reprice swap, hist refresh swap, etc.)
    so override views reflect the new vol surface at their override spot.

    Spot-tick-only paths must NOT call this — bridge filter already skips override clients
    (correct: live spot must not overwrite override view).

    `product_filter` (str) limits trigger to clients whose stored override is for that product.
    """
    overrides = event_bus.iter_override_clients()
    if not overrides:
        return 0
    dispatched = 0
    for ws, params in overrides:
        if product_filter and params.get('product') != product_filter:
            continue
        if not params.get('product'):
            continue
        synth = {
            'product': params.get('product'),
            'strike': params.get('strike'),
            'expiry': params.get('expiry'),
            'ref_px': params.get('ref_px'),
            'ref_bps': params.get('ref_bps'),
            'vol_shift_bps': params.get('vol_shift_bps'),
            'vol_lookback': params.get('vol_lookback'),
            'request_id': params.get('request_id'),
            'session_id': f'_server_{reason}_{int(time.time() * 1000)}',
        }
        try:
            _handle_override(synth, ws)
            dispatched += 1
        except Exception:
            logging.exception(f'trigger_override_recompute: failed for ws={id(ws)} product={params.get("product")}')
    if dispatched:
        logging.info(f'Server-triggered override re-render: dispatched={dispatched} reason={reason} '
                     f'product_filter={product_filter or "all"}')
    return dispatched


def register_cdx_websocket(server, sock=None):
    if sock is None:
        from flask_sock import Sock
        sock = Sock(server)

    @sock.route('/cdx-options/ws')
    def cdx_ws(ws):
        conn = _SerializedWebSocket(ws)
        event_bus.register(conn)
        logging.info(f'CDX WS connected ({event_bus.client_count} clients)')

        try:
            while True:
                raw = conn.receive(timeout=30)
                if raw is None:
                    continue
                data = json.loads(raw)
                cmd = data.get('command')
                if cmd and _command_handler:
                    _command_handler(cmd, data, ws=conn)
        except Exception as e:
            logging.debug(f'CDX WS session ended: {e}')
        finally:
            event_bus.unregister(conn)
            logging.info(f'CDX WS disconnected ({event_bus.client_count} clients)')

    progress_bridge.start()
