"""`spark` parallel backend for JobExecutor (exec_type='spark').

Thin adapter over SparkManager.submitjob (UDF fan-out on Spark executors). DB-LIGHT tasks
only — NOT for beta attribution or anything doing UC SQL / importing the full `src` package
inside the UDF (those fail on executors). See SparkManager.submitjob for the mechanism.

Top-level imports stay light; SparkManager is imported at call time so importing this
module to self-register is cheap.
"""
import logging

from common.utils.jobutil import JobExecutor
from common.utils.parallel_registry import register_backend


def make_backend(max_workers=8, timeout=None, **_ignored):
    return SparkBackend(max_workers=max_workers, timeout=timeout)


# Self-register on import. common never names this backend.
register_backend('spark', make_backend)


class SparkBackend:
    def __init__(self, max_workers=8, timeout=None):
        self.max_workers = max_workers
        self.timeout = timeout

    def submitjob(self, funcs, params=None, initconfig=False, job_name=None, **func_kwargs):
        from data_access.spark.api import sparkmanager

        pairs = list(JobExecutor._iter_funcs_params(funcs, params))
        distinct_funcs = {func for func, _ in pairs}
        if len(distinct_funcs) != 1:
            raise ValueError('spark backend supports a single func over many params')

        func = pairs[0][0]
        param_tuples = []
        for _, paramdict in pairs:
            if isinstance(paramdict, dict):
                raise ValueError('spark backend requires positional param tuples, not dict params')
            param_tuples.append(tuple(paramdict or []))

        logging.info(f'spark backend: submitting {len(param_tuples)} params for {getattr(func, "__name__", func)}')
        return sparkmanager.submitjob(func, param_tuples, initconfig=initconfig, **func_kwargs)
