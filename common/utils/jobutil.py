import concurrent
import itertools
import logging
import os
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from functools import partial

from joblib import Parallel, delayed

from apo_config.config import config
from common.utils.dfutil import tolist
from common.utils.logutil import getlogfile


def _extract_api_error_message(e: Exception) -> str:
    import re
    err_str = str(e)
    err_type = type(e).__name__
    
    if "'message':" in err_str:
        match = re.search(r"'message':\s*'([^']+)'", err_str)
        if match:
            return f"{err_type}: {match.group(1)}"
    
    if '"message":' in err_str:
        match = re.search(r'"message":\s*"([^"]+)"', err_str)
        if match:
            return f"{err_type}: {match.group(1)}"
    
    if hasattr(e, 'message'):
        return f"{err_type}: {e.message}"
    
    if hasattr(e, 'body') and isinstance(e.body, dict):
        msg = e.body.get('error', {}).get('message') or e.body.get('message')
        if msg:
            return f"{err_type}: {msg}"
    
    clean = err_str[:200] if len(err_str) > 200 else err_str
    return f"{err_type}: {clean}"


def retrycode(func, retrycount=3, wait_on_error_seconds=60, jitter_seconds=0, exponential_backoff=False, non_retryable=None):
    import random
    import re
    tries = 0
    last_error = None
    while (tries < retrycount):
        try:
            tries += 1
            return func()
        except Exception as e:
            last_error = e
            err_str = str(e).lower()

            if non_retryable and any(str(x).lower() in err_str for x in non_retryable):
                logging.error(f'🚫 Non-retryable error on try {tries}/{retrycount}, failing immediately: {_extract_api_error_message(e)}')
                raise Exception(_extract_api_error_message(e)) from e

            # Auth / permission / firewall failures never recover by blind retrying — fail
            # fast instead of burning the full retry budget (and, worse, treating them as a
            # rate limit and waiting even longer). e.g. Azure "This ... request is not
            # authorized. The storage account's 'Firewalls...' settings may be blocking...".
            is_auth_error = any(x in err_str for x in [
                'not authorized', 'unauthorized', 'authorization', 'access denied',
                'permission denied', 'forbidden', 'firewall',
            ]) or re.search(r'\b(401|403)\b', err_str) is not None
            if is_auth_error:
                logging.error(f'🚫 Auth/permission error on try {tries}/{retrycount}, failing immediately: {_extract_api_error_message(e)}')
                raise Exception(_extract_api_error_message(e)) from e

            # Match rate-limit / overload on whole tokens so unrelated messages (an auth or
            # storage error that merely contains the substring "rate"/"limit"/"busy") don't
            # get misclassified and pushed into an extended wait.
            is_rate_limit = re.search(r'\b(rate[ _-]?limit|too many requests|429|quota|throttl\w*)\b', err_str) is not None
            is_overload = re.search(r'\b(overload\w*|529|503|capacity|busy|unavailable)\b', err_str) is not None

            logging.error(f'Func Failed: {func} on {tries}/{retrycount} try\nError:{traceback.format_exc()}')
            if tries == retrycount:
                raise Exception(_extract_api_error_message(e)) from e
            
            if exponential_backoff:
                base_wait = min(wait_on_error_seconds * (2 ** (tries - 1)), 300)
            else:
                base_wait = wait_on_error_seconds
            
            if is_rate_limit or is_overload:
                base_wait = max(base_wait, 60) * 1.5
                logging.warning(f'⚠️ Rate limit/overload detected, extended wait')
            
            wait = base_wait + random.uniform(0, jitter_seconds)
            logging.error(f'Waiting {wait:.0f}s for {tries + 1}th try/{retrycount}')
            time.sleep(wait)


def notebook_parallel_job(func, params, max_workers=8, initconfig=False):
    def _func(setup_args, func, param):
        from apo_config.config import config
        if setup_args is not None:
            env, logfile, logfolder, configdict = setup_args
            config.init(env, configdict=configdict)
        return JobExecutor.execute_func_in_error_block(lambda: func(*param))

    setup_args = None
    if initconfig:
        setup_args = JobExecutor._setup_args()
    params = [(setup_args, func, param) for param in params]
    result = Parallel(n_jobs=max_workers, prefer="processes")(delayed(_func)(*param) for param in params)
    return result


class JobExecutor():
    executor_dict = {'thread': partial(ThreadPoolExecutor, thread_name_prefix='T'),
                      'process': partial(ProcessPoolExecutor, max_tasks_per_child=1)}

    def __init__(self, exec_type='thread', max_workers=5, timeout=None, executor_options=None, **backend_opts):
        self.job_executor = None
        self.timeout = timeout
        self.max_workers = max_workers
        self.exec_type = (exec_type or '').lower()
        self._executor_options = dict(executor_options or {})
        self._backend = None
        if self.exec_type in JobExecutor.executor_dict:
            executor = JobExecutor.executor_dict[self.exec_type]
            self.job_executor = executor(max_workers=max_workers, **self._executor_options)
        elif self.exec_type:
            # Built-in types (thread/process) handled above. Anything else is a pluggable
            # backend that self-registers with the registry (see common.utils.parallel_registry).
            # common names no concrete backend — backends live in/under data_access and register
            # themselves. Unknown type with no registration falls back to main-thread (unchanged).
            # backend_opts are extra, backend-specific knobs (e.g. ray/spark num_cpus per task);
            # thread/process ignore them.
            from common.utils import parallel_registry
            factory = parallel_registry.get_backend_factory(self.exec_type)
            if factory is not None:
                self._backend = factory(max_workers=max_workers, timeout=timeout, **backend_opts)
            else:
                logging.warning(f'no registered parallel backend for exec_type={self.exec_type!r}; '
                                f'falling back to main thread')

        self.async_res_dict = {}
        logging.info(f'created executor with type: {self.exec_type}/{self.job_executor or self._backend}')

    @staticmethod
    def _setup_args(job_name=None):
        env = config.getenv()
        logfile = None
        logfolder = None
        configdict = config._CONFIGDICT
        overrides = config.overrides
        if not getlogfile() is None:
            logfolder, logfile = os.path.split(getlogfile())
            logfile = logfile.split('.')[0]
            if job_name is not None:
                logfile = logfile + '_' + job_name
        return env, logfile, logfolder, configdict, overrides

    @staticmethod
    def _get_args(params, **func_kwargs):
        if isinstance(params, dict):
            return ((), {**params, **func_kwargs})
        else:
            return (tuple(params or []), func_kwargs)

    @staticmethod
    def _create_log(logfolder, logfile, i):
        from common.utils.logutil import setuplogger, resetlogfile
        if not logfile is None:
            str_i = "{0:0=3d}".format(i)
            log_create_completed = False
            retrycount = 0
            maxretries = 999
            while (not log_create_completed):
                try:
                    str_r = "{0:0=3d}".format(retrycount)
                    logfilei = f"{logfile}_{str_i}_{str_r}"
                    setuplogger(logfilei, logfolder=logfolder, logconsole=False)
                    log_create_completed = True
                except Exception as e:
                    retrycount += 1
                    resetlogfile()
                    if retrycount >= maxretries:
                        logging.error(
                            f'could not setup log for process {i} after {retrycount} retries for file: {logfilei}')
                        raise e
        else:
            setuplogger()

    @staticmethod
    def _func_call_wrapper(exec_type, func_to_call, init_config, _icounter, setup_args, *args, **kw_args):
        env, logfile, logfolder, configdict, overrides = setup_args

        from apo_config.config import config

        if exec_type == 'process':
            JobExecutor._create_log(logfolder, logfile, _icounter)
            if init_config:
                config.init(env, configdict=configdict, overrides=overrides)

        logging.info(f'job executor call with num params: {len(args)}')
        retval = func_to_call(*args, **kw_args)
        if exec_type == 'process':
            logging.shutdown()

        return retval

    @staticmethod
    def get_func_args(exec_type, func, logfile_index, paramdict, job_name=None, initconfig=False, **func_kwargs):
        args, func_kwargs = JobExecutor._get_args(paramdict, **func_kwargs)
        func = partial(JobExecutor._func_call_wrapper, exec_type, func, initconfig, logfile_index,
                       JobExecutor._setup_args(job_name=job_name))
        retval = (func, args, func_kwargs)
        return retval

    @staticmethod
    def execute_func_in_error_block(func):
        try:
            res = {'result': func(), 'error': None}
        except:
            err = traceback.format_exc()
            res = {'result': None, 'error': err}
            logging.error(f'{func}: generated an exception: {err}')
        return res

    def get_results(self, res_dict):
        results = {}

        total = len(res_dict)
        log_step = max(1, total // 5)
        try:
            for j, future in enumerate(concurrent.futures.as_completed(res_dict, timeout=self.timeout)):
                i, func, paramdict = res_dict[future]
                results[i] = JobExecutor.execute_func_in_error_block(future.result)
                done = j + 1
                logging.debug(f'completed {done} th job ({i} th job in list)')
                if done % log_step == 0 or done == total:
                    logging.info(f'completed {done}/{total} jobs ({done / total:.0%})')
        except concurrent.futures.TimeoutError:
            logging.warning(f'Timeout after {self.timeout}s. Completed {len(results)}/{len(res_dict)} jobs')
        except Exception as e:
            logging.error(f'Unexpected exception: {str(e)}')

        for future, (i, func, paramdict) in res_dict.items():
            if i not in results:
                results[i] = {'result': None, 'error': f'Timeout after {self.timeout}s'}
                logging.warning(f'Job {i} timed out')

        return [results[i] for i in sorted(results.keys())]

    @staticmethod
    def is_done(res_dict, jobnum=None):
        for future, meta in res_dict.items():
            (i, func, paramdict) = meta
            if (jobnum is None or jobnum == i) and not future.done():
                return False
        return True

    @staticmethod
    def cancel_job(res_dict, jobnum=None):
        assert not JobExecutor.is_done(res_dict, jobnum), "jobs completed and cannot be cancelled"
        for future, meta in res_dict.items():
            (i, func, paramdict) = meta
            if (jobnum is None or jobnum == i) and not future.done():
                future.cancel()

    @staticmethod
    def _iter_funcs_params(funcs, params):
        funcs = tolist(funcs)
        params = tolist(params, replacenone=True)

        assert (len(funcs) == len(params or [])) or (len(funcs) == 1) or \
               (len(params or []) <= 1), "size of funcs and params dont match"

        if len(funcs) == 1 and len(params or []) >= 1:
            funcs = funcs * len(params or [])
        if len(params or []) == 1:
            params = params * len(funcs)

        for func, paramdict in itertools.zip_longest(funcs, params):
            yield func, paramdict

    def submitjob(self, funcs, params=None, initconfig=False, job_name=None, use_async_dict=False, **func_kwargs):
        logging.info(f"running submitjob for {job_name}: {funcs} with {len(params or [])} params")
        if self._backend is not None:
            return self._backend.submitjob(funcs, params, initconfig=initconfig, job_name=job_name, **func_kwargs)
        if isinstance(self.job_executor, (ThreadPoolExecutor, ProcessPoolExecutor)):
            res_dict = self.submitjob_async(funcs, params,
                                            initconfig=initconfig,
                                            job_name=job_name,
                                            use_async_dict=use_async_dict,
                                            **func_kwargs)
            res = self.get_results(res_dict)
            self.async_flush()

        else:
            res = self.submitjob_mainthread(funcs, params,
                                            **func_kwargs)

        logging.info(f"returning submitjob results")
        return res

    def submitjob_mainthread(self, funcs, params=None, initconfig=False, job_name=None, use_async_dict=False,
                             **func_kwargs):
        logging.info(f"running submitjob_mainthread for {funcs} with {len(params or [])} params")
        res = []
        for i, (func, paramdict) in enumerate(JobExecutor._iter_funcs_params(funcs, params)):
            # Bind to FRESH names: do not reuse `func_kwargs`, or one iteration's call kwargs
            # (which include the dict params) leak into the next call via **func_kwargs and
            # override its params — collapsing every task to the first param's values.
            (func_to_call, call_args, call_kwargs) = JobExecutor.get_func_args(self.exec_type, func, i, paramdict,
                                                                               initconfig=False,
                                                                               job_name=None,
                                                                               **func_kwargs)
            res += [JobExecutor.execute_func_in_error_block(
                lambda fc=func_to_call, a=call_args, k=call_kwargs: fc(*a, **k))]
        logging.info(f"returning submitjob_mainthread results")
        return res

    def _recreate_executor(self):
        max_workers = self.job_executor._max_workers if self.job_executor else 5
        logging.warning(f'recreating broken {self.exec_type} executor (max_workers={max_workers})')
        self.job_executor = JobExecutor.executor_dict[self.exec_type](max_workers=max_workers, **self._executor_options)

    def submitjob_async(self, funcs, params=None, initconfig=False, job_name=None, use_async_dict=True, **func_kwargs):
        if self.job_executor is None:
            logging.warning(f'only supported for {JobExecutor.executor_dict.keys()}')
            return self.submitjob_mainthread(funcs, params, **func_kwargs)

        if isinstance(self.job_executor, ProcessPoolExecutor) and getattr(self.job_executor, '_broken', False):
            self._recreate_executor()

        logging.info(f"running submitjob_async for {job_name}: {funcs} with {len(params or [])} params")

        res_dict = {}
        for i, (func, paramdict) in enumerate(JobExecutor._iter_funcs_params(funcs, params)):
            (func_to_call, args, job_kwargs) = JobExecutor.get_func_args(self.exec_type, func, i, paramdict,
                                                                          initconfig=initconfig,
                                                                          job_name=job_name,
                                                                          **func_kwargs)
            try:
                futures_res = self.job_executor.submit(func_to_call, *args, **job_kwargs)
            except BrokenProcessPool:
                self._recreate_executor()
                futures_res = self.job_executor.submit(func_to_call, *args, **job_kwargs)
            res_dict[futures_res] = (i, func, paramdict)

        logging.info(f"returning submitjob_async results")
        if use_async_dict:
            self.async_res_dict.update(res_dict)
        return res_dict

    def async_flush(self):
        logging.info(f'flushing async jobs: {len(self.async_res_dict.keys())}')

        success_jobs = []
        error_jobs = []
        for future, params in self.async_res_dict.items():
            if future.done():
                res = JobExecutor.execute_func_in_error_block(future.result)
                if not res['error'] is None:
                    error_jobs.append(future)
                    logging.error(f"error in job: \n{res['error']}")
                else:
                    success_jobs.append(future)
                    logging.info(f"completed job")

        [self.async_res_dict.pop(k) for k in success_jobs + error_jobs]
        logging.info(
            f'pending jobs: {len(self.async_res_dict.keys())}. '
            f'completed jobs: {len(success_jobs) + len(error_jobs)} / '
            f'errors: {len(error_jobs)}')

    def cleanup(self, wait=False):
        """Explicitly clean up resources"""
        logging.info(f'cleaning up executor with type: {self.exec_type}/{self.job_executor}')
        if self.job_executor is not None:
            # Cancel any pending futures
            for future in list(self.async_res_dict.keys()):
                if not future.done() and not future.cancelled():
                    future.cancel()

            # Clear the dictionary
            self.async_res_dict.clear()

            # Reuse the shutdown logic from __exit__
            processes = getattr(self.job_executor, '_processes', None) or {}
            for process in list(processes.values()):
                if process is not None and process.is_alive():
                    process.terminate()
            self.job_executor.shutdown(wait=wait, cancel_futures=True)

            self.job_executor = None

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit method"""
        self.cleanup()

    def __del__(self):
        """Destructor to ensure cleanup"""
        try:
            self.cleanup()
        except:
            # Avoid errors during garbage collection
            pass

    def __enter__(self):
        """Context manager entry method"""
        return self


def _test_proc(n, i, delay=2):
    from time import sleep
    sleep(delay)
    logging.info(f'processing n={n} with i={i}')
    return n ** 2 * i


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()

    config.init('dev')

    rg = range(10)

    threadexecutor = JobExecutor('thread')
    params = [dict(n=i) for i in rg]

    res = threadexecutor.submitjob(_test_proc, params, initconfig=True, i=1)
    logging.info(res)

    res_dict = threadexecutor.submitjob_async(_test_proc, params, i=1)
    logging.info(res_dict)
    logging.info(threadexecutor.is_done(res_dict))
    logging.info(threadexecutor.get_results(res_dict))

    procexecutor = JobExecutor('process')
    params = [dict(n=i) for i in rg]
    res = procexecutor.submitjob(_test_proc, params, i=1)
    logging.info(res)

    res_dict = procexecutor.submitjob_async(_test_proc, params, i=1)
    logging.info(res_dict)
    logging.info(procexecutor.is_done(res_dict))
    logging.info(procexecutor.get_results(res_dict))
