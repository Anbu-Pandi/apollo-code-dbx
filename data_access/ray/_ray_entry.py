"""Self-contained remote runner for the ray backend, executed by a Databricks spark_python_task.

CRITICAL: this launcher must NOT import data_access.ray / dbjobs / the ray backend module.
That code is brand-new and is NOT in the APO wheels deployed on the cluster, so importing it
fails (ModuleNotFoundError). The launcher therefore carries the remote fan-out logic itself
and depends only on:
  * stdlib + ray (preinstalled on the ML runtime) + cloudpickle (ray dep),
  * the PRE-EXISTING JobExecutor static helpers and apo_config.config from the deployed wheels,
  * the user's function dependencies — the user function is cloudpickled BY VALUE, so its body
    travels in the payload; only its imports (e.g. common.utils.sqlutil) must be in the wheels.

Inputs (task parameters): --call-uri (cloudpickled payload) and --result-uri, both UC volume
paths FUSE-mounted at /Volumes on the SINGLE_USER cluster. Payload keys:
    pairs          list of (func, paramdict)  -- from JobExecutor._iter_funcs_params
    func_kwargs    dict                        -- extra kwargs for every call
    setup_args     (env, logfile, logfolder, configdict, overrides) -- for config.init
    ray_min_nodes / ray_max_nodes  int         -- setup_ray_cluster worker bounds
"""
import argparse


def _pickle():
    try:
        import cloudpickle as cp
    except Exception:
        import pickle as cp
    return cp


def _decode_func(enc):
    """Inverse of the backend's _encode_func: resolve a {'__funcref__': [module, qualname]}
    marker by importing it on the worker; otherwise `enc` is the function itself (by value)."""
    if isinstance(enc, dict) and '__funcref__' in enc:
        import importlib
        module, qualname = enc['__funcref__']
        obj = importlib.import_module(module)
        for part in qualname.split('.'):
            obj = getattr(obj, part)
        return obj
    return enc


def _encode_result(item):
    """DataFrame results travel as Arrow IPC bytes (version-stable) wrapped in a marker; other
    results stay as-is. The envelope is then stdlib-pickled (pure data)."""
    try:
        import io
        import pandas as pd
        import pyarrow as pa
        val = item.get('result') if isinstance(item, dict) else None
        if isinstance(val, pd.DataFrame):
            sink = io.BytesIO()
            table = pa.Table.from_pandas(val)
            with pa.ipc.new_stream(sink, table.schema) as writer:
                writer.write_table(table)
            item = dict(item)
            item['result'] = {'__arrow__': sink.getvalue()}
    except Exception:
        pass
    return item


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--call-uri', required=True)
    ap.add_argument('--result-uri', required=True)
    args = ap.parse_args()

    cp = _pickle()
    with open(args.call_uri, 'rb') as f:
        payload = cp.loads(f.read())

    env, logfile, logfolder, configdict, overrides = payload['setup_args']
    func_kwargs = payload['func_kwargs']

    from apo_config.config import config
    config.init(env, configdict=configdict, overrides=overrides)

    import ray
    from ray.util.spark import setup_ray_cluster, shutdown_ray_cluster
    from common.utils.jobutil import JobExecutor

    print(f'setup_ray_cluster min={payload["ray_min_nodes"]} max={payload["ray_max_nodes"]}', flush=True)
    setup_ray_cluster(min_worker_nodes=payload['ray_min_nodes'], max_worker_nodes=payload['ray_max_nodes'])
    ray.init(ignore_reinit_error=True)
    try:
        # max_retries=0 -> at-most-once: a task is never re-executed (no duplicate dates).
        # A worker/node death surfaces as that task's error rather than a silent re-run.
        @ray.remote(num_cpus=payload.get('num_cpus', 1), max_retries=payload.get('max_retries', 0))
        def _task(enc, paramdict):
            from apo_config.config import config as _config
            _config.init(env, configdict=configdict, overrides=overrides)
            func = _decode_func(enc)
            call_args, call_kwargs = JobExecutor._get_args(paramdict, **func_kwargs)
            return JobExecutor.execute_func_in_error_block(lambda: func(*call_args, **call_kwargs))

        # Collect per-future so one failed task (e.g. dead worker, retries off) doesn't sink
        # the whole batch; its slot becomes {result: None, error: ...}. Order matches pairs.
        futures = [_task.remote(enc, paramdict) for enc, paramdict in payload['pairs']]
        results = []
        for fut in futures:
            try:
                results.append(ray.get(fut))
            except Exception as e:
                results.append({'result': None, 'error': f'{type(e).__name__}: {e}'})
    finally:
        shutdown_ray_cluster()

    import pickle
    results = [_encode_result(r) for r in results]
    try:
        blob = pickle.dumps(results)
    except Exception:
        blob = cp.dumps(results)
    with open(args.result_uri, 'wb') as f:
        f.write(blob)
    print(f'RAY_DONE wrote {len(results)} results to {args.result_uri}', flush=True)


if __name__ == '__main__':
    main()
