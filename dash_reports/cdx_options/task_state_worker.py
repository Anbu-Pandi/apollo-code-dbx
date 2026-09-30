import time


def exchange_task_state(tasks, retain_ms, store_path=None):
    import pandas as pd

    from data_access.storage import SharedFrameCache

    store = SharedFrameCache(store_path) if store_path else SharedFrameCache.local()
    if tasks:
        store.write_many([
            ('cdx_task_state', 'active', task['task_id'], 'task', pd.DataFrame([task]))
            for task in tasks
        ])
    cutoff = int(time.time() * 1000) - retain_ms
    return [
        task
        for frame in store.read_many('cdx_task_state', 'active', 'task')
        for task in frame.to_dict('records')
        if task['updated_ms'] >= cutoff
    ]


def sync_task_state(tasks, retain_ms, store_path=None):
    from common.utils.jobutil import JobExecutor

    executor = JobExecutor('process', max_workers=1, timeout=120)
    try:
        results = executor.submitjob(
            exchange_task_state, [[tasks, retain_ms, store_path]],
            initconfig=store_path is None, job_name='cdx_task_state',
        )
        result = results[0]
        if result['error']:
            raise RuntimeError(result['error'])
        return result['result']
    finally:
        executor.cleanup()
