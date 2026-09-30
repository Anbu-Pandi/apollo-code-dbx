import inspect
import logging
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pathos

from apo_config.config import config
from common.Email import Email
from common.utils.logutil import getlogfile


class multiProcessing():
    def __init__(self, targetProcessesList=None, paramsList=None, multiProcessNameDict=None, resultType='errors'):
        self.processes = targetProcessesList
        self.params = paramsList
        self.resultType = resultType
        self.processesName = multiProcessNameDict

    def _setup_args(self):
        env = config.getenv()
        logfile = None
        logfolder = None
        configdict = config._CONFIGDICT
        overrides = config.overrides
        if not getlogfile() is None:
            logfolder, logfile = os.path.split(getlogfile())
        return env, logfile.split('.')[0], logfolder, configdict, overrides

    def _func_call(self, func, i, setup_args, *args):
        env, logfile, logfolder, configdict, overrides = setup_args

        from common.utils.logutil import setuplogger
        from apo_config.config import config
        if logfile is not None:
            stri = "{0:0=3d}".format(i)
            setuplogger(f"{logfile}_{stri}", logfolder=logfolder)
        else:
            setuplogger()

        config.init(env, configdict=configdict, overrides=overrides)

        return func(*args)

    def split_df_by_col(self, df, partition_col=None, num_partitions=10):
        if partition_col:
            df_sorted = df.sort_values(by=partition_col)

            unique_ids = df_sorted[partition_col].unique()
            num_ids = len(unique_ids)
            ids_per_partition = np.ceil(num_ids / num_partitions).astype(int)

            df_chunks = []
            for i in range(num_partitions):
                start_idx = i * ids_per_partition
                end_idx = start_idx + ids_per_partition
                ids_for_partition = unique_ids[start_idx:end_idx]

                partition = df_sorted[df_sorted[partition_col].isin(ids_for_partition)]
                df_chunks.append(partition)
        else:
            df_chunks = np.array_split(df, num_partitions)

        return df_chunks

    def runMultiProcessFunction(self, function, df, partition, split_df_by_col=None, shrdVar=None):
        """
        This function is used for running single function in parallel
        :param function: name ofthe function to run in parallel
        :param df: DataFrame for partition
        :param partition: numbers of partition to be done
        :param shrdVar: additional params ofthe function that is common to all instance of function
        :return: No data will be returned; but the function called should be written as such that it performs all the
        operation. For Future -  enhancement needs to be done to have results shared between child and parent process
        """

        """Info about caller function and module named"""
        all_stack_frames = inspect.stack()
        caller_stack_frame = all_stack_frames[1]
        caller_name = caller_stack_frame[3]
        module_name = caller_stack_frame[1].split('/')[-1]

        logging.info("In MultiProcess")
        jobs = []

        df_split = self.split_df_by_col(df, partition_col=split_df_by_col, num_partitions=partition)

        manager = pathos.helpers.mp.Manager()

        if shrdVar != None:
            ns = manager.Namespace()
            ns.extraParams = shrdVar

        # manager = multiprocessing.Manager()
        return_Obj = manager.dict()

        i = 1

        """To start multiprocess"""
        for df in df_split:
            logging.info(('Running {} instance of function in Parallel'.format(i)))
            p = pathos.helpers.mp.Process(target=self._func_call,
                                          args=(function, i, self._setup_args(),
                                                df, ns.extraParams, return_Obj, i))

            jobs.append(p)
            p.start()
            i = i + 1

        """Wait for all jobs to finish running"""
        for job in jobs:
            job.join()

        if self.resultType == 'errors':
            error = dict(return_Obj)
            if not len(error) == 0:
                logging.info(error)
                Email.send_email(error, 'Python Code: Parallel Function Run - {0} of {1} - Failed'
                                 .format(caller_name, module_name), 'pbhargava@apollo.com')  # email_to
            else:
                Email.send_email('No Error', 'Python Code: Parallel Function Run - {0} of {1} - Success'
                                 .format(caller_name, module_name), 'pbhargava@apollo.com')
        elif self.resultType == 'values':
            Email.send_email('No Error', 'Python Code: Parallel Function Run - {0} of {1} - Success'.
                             format(caller_name, module_name), 'pbhargava@apollo.com')
            return dict(return_Obj)

    def runMultiProcess(self):
        """
        This function is used to run different scripts in parallel. Look at the constructor for params required.
        :return: in case of error in the parallel process, error is mail.
        """
        """Info about caller function and module named"""
        all_stack_frames = inspect.stack()
        caller_stack_frame = all_stack_frames[1]
        caller_name = caller_stack_frame[3]
        module_name = caller_stack_frame[1].split('/')[-1]

        logging.info("Running MultiProcess Function")

        jobs = []

        manager = multiprocessing.Manager()
        return_dict = manager.dict()
        if isinstance(self.params, list):
            self.params.append(return_dict)

        """To start multiprocess"""
        for (i, process) in enumerate(self.processes):
            if isinstance(self.params, list):
                if not self.processesName == None:
                    processName = self.processesName[process]
                    self.params.append(processName)
                    logging.info(('Running Parallel Process: ' + processName))
                p = multiprocessing.Process(target=self._func_call,
                                            args=tuple([process, i, self._setup_args()]) +
                                                 tuple(self.params))
            elif isinstance(self.params, dict):
                p = multiprocessing.Process(target=self._func_call,
                                            args=tuple([process, i, self._setup_args()]) +
                                                 tuple([self.params[process]]))

            jobs.append(p)
            p.start()

            if isinstance(self.params, list):
                if not self.processesName == None:
                    self.params.remove(processName)

        """Wait for all jobs to finish running"""
        for job in jobs:
            job.join()

        if self.resultType == 'errors':
            error = dict(return_dict)
            if not len(error) == 0:
                logging.info(error)
                Email.send_email(error, 'Python Code: Parallel Processes {} of {} - Failed'
                                 .format(caller_name, module_name), 'pbhargava@apollo.com')  # email_to
            else:
                Email.send_email('No Error', 'Python Code: Parallel Processes {} of {} - Success'
                                 .format(caller_name, module_name), 'pbhargava@apollo.com')
        elif self.resultType == 'values':
            Email.send_email('No Error', 'Python Code: Parallel Processes {} of {} - Success'
                             .format(caller_name, module_name), 'pbhargava@apollo.com')
            return dict(return_dict)


class concurrentProcessing():
    """Work in Progress. Not to be used anywhere"""

    def __init__(self):
        pass

    def runConcurentFunc(self, funcName, df, partition, extraParams=None):

        """Info about caller function and module named"""
        all_stack_frames = inspect.stack()
        caller_stack_frame = all_stack_frames[1]
        caller_name = caller_stack_frame[3]
        module_name = caller_stack_frame[1].split('/')[-1]

        logging.info("Running concurrent function Function")
        df_split = np.array_split(df, partition)
        manager = multiprocessing.Manager()
        if extraParams != None:
            ns = manager.Namespace()
            ns.extraParams = extraParams
        pool = ProcessPoolExecutor(10)
        result = []
        for df in df_split:
            future = pool.submit(funcName, df, extraParams)

        data = future.result()
        logging.info(('Type of DFata returned by Multiprcoessing: ', type(data)))


def square(n, q):
    sq = n * n
    q.put(sq)


if __name__ == '__main__':
    classObj = multiProcessing(square, )
