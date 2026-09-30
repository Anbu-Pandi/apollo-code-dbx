import glob
import logging
import os
import shutil
import traceback

import pandas as pd

from apo_config.config import config
from common.utils import dtutil
from common.utils.jobutil import JobExecutor
from common.utils.numutil import xlstring_to_float
from dash_reports.issuer_debt_in_index_movers.data import bond_index
from dash_reports.issuer_debt_in_index_movers.data import etf_change
from dash_reports.issuer_debt_in_index_movers.data import utils as index_performance_helpers
from dash_reports.report_common.data.config import INPUT_PATH_FOLDER
from dash_reports.report_common.utils.dates import reportdates
from dash_reports.trace.data import trace_flow
from data_access.beta_attribution import APP
from data_access.dbtables import ICE_INDEX_APP
from data_access.spark.api import sparkmanager
from data_access.spark.azureblob import BlobDownloader
from common.utils import sqlutil

def get_data_folder(folder_date=None):
    folder_path = reportdates.get_dt_folder(INPUT_PATH_FOLDER, folder_date)
    return folder_path


def get_file_name(folder_date=None, start_date=None, end_date=None, ):
    (start_date, end_date, prev_risk_date, year_start_date, risk_year_start_date) = \
        reportdates.get_report_folder_dates(folder_date, start_date=start_date, end_date=end_date)
    logging.info(f'report dates: '
                 f'{(start_date, end_date, prev_risk_date, year_start_date, risk_year_start_date)}')

    filenamesdict = {'pnl': 'pnl_attribution_dynamic_{}_{}.csv'.format(start_date.strftime("%Y-%m-%d"),
                                                                       end_date.strftime("%Y-%m-%d")),
                     'beta_diff': 'Beta Dif Report {} to {}.csv'.format(
                         dtutil.dateadd(start_date, '-1k').strftime("%Y%m%d"),
                         end_date.strftime("%Y%m%d")),
                     'roll_issuer': 'pnl_rolling_hist_{}_{}_Issuer Ult.csv'.format(
                         year_start_date.strftime("%Y-%m-%d"),
                         end_date.strftime("%Y-%m-%d")),
                     'cur_risk': 'risk_enhanced_dynamic_{}.csv'.format(end_date.strftime("%Y%m%d")),
                     'roll_sector': 'pnl_rolling_hist_{}_{}_Issuer Ult GICS Industry Sector.csv'.format(
                         year_start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")),
                     'roll_strategy': 'pnl_rolling_hist_{}_{}_FO Strategy Broad Category.csv'.format(
                         year_start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")),
                     'roll_rating': 'pnl_rolling_hist_{}_{}_Final Rating.csv'.format(
                         year_start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")),
                     'risk_series': 'risk_enhanced_dynamic_{}_{}.csv'.format(
                         risk_year_start_date.strftime("%Y-%m-%d"),
                         end_date.strftime("%Y-%m-%d")),
                     'prev_risk': 'risk_enhanced_dynamic_{}.csv'.format(prev_risk_date.strftime("%Y%m%d")),
                     }

    return filenamesdict


def flatten_dict(df_dict, prefix=None):
    flat_dict = {}
    for k, df in df_dict.items():
        k = '_'.join(filter(lambda x: pd.notnull(x), [prefix, k]))
        if isinstance(df, pd.DataFrame):
            flat_dict[k] = df
        elif isinstance(df, dict):
            flat_dict.update(flatten_dict(df, k))

    return flat_dict


class DataStore:
    def init(self,
             dt=None, startdt=None, enddt=None,
             clearlocalcache=False, fastload=False):
        self.paraminit(dt, startdt, enddt, clearlocalcache, fastload)
        self.datainit()
        self.jobber.submitjob_async(sparkmanager.init)

    def paraminit(self, dt=None, startdt=None, enddt=None,
                  clearlocalcache=False, fastload=False):
        self.cache_dict = {}
        self.cache_background_processing_keys = set()
        self.clearlocalcache = clearlocalcache
        self.fastload = fastload
        self.localcachedir = os.path.join(config.getconfig(APP, 'local_cache_dir') or '.',
                                          '__beta_attrib_report_datastore__')
        logging.info(f'setting local cache dir: {self.localcachedir}')
        if self.clearlocalcache and os.path.exists(self.localcachedir):
            logging.info(f'clearing {self.localcachedir}')
            shutil.rmtree(self.localcachedir, ignore_errors=True)
        if not os.path.exists(self.localcachedir):
            os.makedirs(self.localcachedir)

        self.reportdt = dt
        self.reportstartdt = startdt
        self.reportenddt = enddt

        self.datafolder = get_data_folder(self.reportdt)
        self.filenamesdict = get_file_name(self.reportdt, self.reportstartdt, self.reportenddt)

        self.jobber = JobExecutor(exec_type='thread', max_workers=10)

    def clear_background_processing(self, key):
        if key in self.cache_background_processing_keys:
            logging.info(f"cache processing removed for key {key}")
            self.cache_background_processing_keys.remove(key)
            logging.info(f"cache processing request for keys {self.cache_background_processing_keys}")

    def add_background_processing(self, key, createdf_func):
        if (key not in self.cache_background_processing_keys):
            self.cache_background_processing_keys.add(key)
            logging.info(f"cache processing request added for key: {key}")
            trace_volume_create_func = lambda: self.read_or_create_cache_for_df(key=key, createdf_func=createdf_func)
            self.jobber.submitjob_async([trace_volume_create_func], initconfig=True)
            logging.info(f"cache processing requests: {self.cache_background_processing_keys}")
        else:
            logging.info(f"cache processing request in progress for key: {key} "
                         f"in {self.cache_background_processing_keys}")

    def set_app_key(self, key, value):
        if value is not None:
            logging.info(f"cache set for key {key}")
            self.cache_dict[key] = value
            self.clear_background_processing(key)

    def get_app_key(self, key):
        if key in self.cache_dict and self.cache_dict[key] is not None:
            logging.info(f"cache response for key {key}")
            self.clear_background_processing(key)
            return self.cache_dict[key]

        logging.warning(f"cache miss for key: {key}")

    @staticmethod
    def _upload_azure(filepath):
        client = BlobDownloader()
        cache_path = config.getconfig(APP, 'azure_cache_dir')
        client.upload_file(filepath, cache_path)

    @staticmethod
    def writedf(df, filepath, upload_azure=False):
        logging.info(f'writing df: {df.shape} to path: {filepath}, upload_azure: {upload_azure}')
        try:
            df.to_parquet(filepath)
        except:
            logging.warning(f'failed to write as parquet, using hdf: {filepath}')
            df.to_hdf(filepath, key='df', mode='w')
        if upload_azure:
            DataStore._upload_azure(filepath)

    @staticmethod
    def readdf(filepath):
        logging.info(f'reading df from path: {filepath}')
        try:
            df = pd.read_parquet(filepath)
        except:
            logging.warning(f'failed to read as parquet, using hdf: {filepath}')
            df = pd.read_hdf(filepath, key='df', mode='r')
        logging.info(f'finished reading df: {df.shape} from path: {filepath}')
        return df

    def read_or_create_filecache_for_dfs(self, cachefile_full_path, createdf_func=None, upload_azure=False):
        if os.path.exists(cachefile_full_path):
            df = DataStore.readdf(cachefile_full_path)
            return df

        elif cachefile_full_path.endswith('*.cache') and len(glob.glob(cachefile_full_path)) > 0:
            key_associated_files = glob.glob(cachefile_full_path)
            retdict = {}
            for keyfile in key_associated_files:
                key = keyfile[len(cachefile_full_path) - 4:-3]
                keyfile = cachefile_full_path.replace('*', key)
                retdict[key] = self.read_or_create_filecache_for_dfs(keyfile, None, upload_azure=upload_azure)
            return retdict

        elif not createdf_func is None:
            logging.info(f"creating df for localcache file: {cachefile_full_path}")
            df = createdf_func()
            if not os.path.exists(self.localcachedir):
                os.makedirs(self.localcachedir)
            if isinstance(df, pd.DataFrame):
                DataStore.writedf(df, cachefile_full_path, upload_azure=upload_azure)
            elif isinstance(df, dict) and cachefile_full_path.endswith('*.cache'):
                logging.info(f"writing dfs to localcache file: {cachefile_full_path}")
                dfs = flatten_dict(df)
                for key, dfi in dfs.items():
                    keyfile = cachefile_full_path.replace('*', key)
                    DataStore.writedf(dfi, keyfile, upload_azure=upload_azure)
            return df
        logging.warning(f"filecache miss for file: {cachefile_full_path}")

    def read_or_create_cache_for_df(self, key, createdf_func=None, upload_azure=False):
        df = self.get_app_key(key)
        if df is None:
            if '*' in key:
                cachefile_full_path = os.path.join(self.localcachedir, key + '.cache')
                files = glob.glob(cachefile_full_path)
                if len(files) > 0:
                    cachefile_full_path = sorted(files)[-1]
                else:
                    cachefile_full_path = cachefile_full_path.replace('*', '__generic__')
            else:
                cachefile_full_path = os.path.join(self.localcachedir, key + '.cache')
            df = self.read_or_create_filecache_for_dfs(cachefile_full_path, createdf_func=createdf_func,
                                                       upload_azure=upload_azure)
            if not df is None:
                self.set_app_key(key, df)
        return df

    def read_or_create_cache_for_df_background(self, key, createdf_func, upload_azure=False):
        df = self.read_or_create_cache_for_df(key, upload_azure=upload_azure)
        if df is None and createdf_func is not None:
            self.add_background_processing(key, createdf_func)
        return df

    def read_or_create_cache_for_csv(self, key, upload_azure=False, **kwargs):
        filename = os.path.join(self.datafolder, self.filenamesdict[key])
        key = self.filenamesdict[key].replace('.csv', '')
        return self.read_or_create_cache_for_df(key=key,
                                                createdf_func=lambda: pd.read_csv(filename, **kwargs),
                                                upload_azure=upload_azure)

    def read_or_create_cache_for_dict(self, key, createdict_func):
        prefix = f'{key}_'
        dictkeys = list(filter(lambda x: prefix == x[:len(prefix) + 1], datastore.cache_dict))
        if len(dictkeys) != 0:
            retdict = {}
            for dkey in dictkeys:
                retdict[dkey[len(prefix):]] = datastore.cache_dict[dkey]
            return retdict
        else:
            cachefiles_full_path = os.path.join(self.localcachedir, key + '*.cache')
            retdict = self.read_or_create_filecache_for_dfs(cachefiles_full_path, createdict_func)
            return retdict

    def get_risk_series(self):
        return self.read_or_create_cache_for_csv('risk_series', parse_dates=['As Of Date'])

    def get_pnl(self):
        return self.read_or_create_cache_for_csv('pnl', parse_dates=['As Of Date'])

    def get_beta_diff(self):
        return self.read_or_create_cache_for_csv('beta_diff')

    def get_cur_risk(self):
        return self.read_or_create_cache_for_csv('cur_risk', parse_dates=['As Of Date'],
                                                 converters={'Modified Duration': xlstring_to_float,
                                                             'Spread Duration': xlstring_to_float})

    def get_prev_risk(self):
        return self.read_or_create_cache_for_csv('prev_risk', parse_dates=['As Of Date'],
                                                 converters={'Modified Duration': xlstring_to_float,
                                                             'Spread Duration': xlstring_to_float})

    def get_roll_strategy(self):
        return self.read_or_create_cache_for_csv('roll_strategy')

    def get_roll_issuer(self):
        return self.read_or_create_cache_for_csv('roll_issuer')

    def get_roll_sector(self):
        return self.read_or_create_cache_for_csv('roll_sector')

    def get_roll_rating(self):
        return self.read_or_create_cache_for_csv('roll_rating')

    def get_trace_history(self, uselocalfiles=True):
        if uselocalfiles:
            trace_history_key = 'weekly_trace_*'
        else:
            trace_history_key = trace_flow.get_recent_parquet_file()
        df = self.read_or_create_cache_for_df(key=trace_history_key, createdf_func=trace_flow.get_trace_history)
        return df

    def get_trace_volume(self, dt):
        dt = dtutil.todate(dt)
        key = f'trace_{dt}_volume_mtd'
        df = self.read_or_create_cache_for_df_background(key, lambda: trace_flow.get_trace_volume(dt, dt,
                                                                                                  tenor='mtd'))
        return df

    def get_etf_change(self, dt):
        dt = dtutil.todate(dt)
        fromdt = dtutil.bomonth(dt)
        key = f'etf_change_mtd_{dt}'
        df = self.read_or_create_cache_for_df_background(key, lambda: etf_change.get_etf_change(fromdt, dt)[1])
        return df

    def get_index_returns_only(self, index, dt):
        dt = dtutil.todate(dt)
        key = f'{index}_{dt}_returns'
        df = self.read_or_create_cache_for_df(key,
                                              lambda: index_performance_helpers.get_index_movers(index, dt))
        return df

    def get_index_returns(self, index, dt, trades=None, etf_change=None):
        dt = dtutil.todate(dt)
        df = self.get_index_returns_only(index, dt)
        if not trades is None:
            df = bond_index.add_data_by_instrument_id(df, trades, join_col='cusip')
        if not etf_change is None:
            df = bond_index.add_data_by_instrument_id(df, etf_change, join_col='cusip')

        return df

    def get_index_reference_returns(self, index, dt):
        dt = dtutil.todate(dt)
        key = f'{index}_{dt}_reference_returns'
        df = self.read_or_create_cache_for_df(key, lambda: index_performance_helpers.get_reference_returns(index, dt))
        return df

    def get_issuer_debt_df(self, dt):
        dt = dtutil.todate(dt)
        key = f'{dt}_issuer_debt'
        func = lambda: sqlutil.readdfsql(f"select * from dbo.debt_features where asof_date='{dt}' ", app=ICE_INDEX_APP)
        df = self.read_or_create_cache_for_df(key, func, upload_azure=True)
        return df

    def indexdatainit(self):
        refdt = dtutil.today()

        for index in ['H0A0', 'SPBDAL', 'C0A0',
                      'EB00', 'ER00',
                      'H0A1', 'H0A2', 'H0A3', 'H0A4',
                      'C0A1', 'C0A2', 'C0A3', 'C0A4', ]:

            for dt in [dtutil.dateadd(refdt, '-1b'),
                       dtutil.eoweek(dtutil.dateadd(refdt, '-1w'), use_bday=True),
                       dtutil.eomonth(dtutil.dateadd(refdt, '-1m'), use_bday=True),
                       dtutil.eoyear(dtutil.dateadd(refdt, '-1y'), use_bday=True)]:
                try:
                    trades = self.get_trace_volume(dt)
                    etf_change = self.get_etf_change(dt)
                    self.get_index_returns(index, dt, trades, etf_change)
                    self.get_index_reference_returns(index, dt)
                except:
                    logging.error(f'index data init error: {index} for date: {dt}\n{traceback.format_exc()}')

    @staticmethod
    def get_network_dir_path():
        return os.path.join(config.getconfig(APP, 'network_dir'))

    def datainit(self):
        if not self.fastload:
            self.get_cur_risk()
            self.get_prev_risk()
            self.get_risk_series()
            self.get_pnl()
            self.get_roll_issuer()
            self.get_roll_strategy()
            self.get_roll_rating()
            self.get_roll_sector()
            self.get_beta_diff()

            sparkmanager.init()
            self.get_trace_history(uselocalfiles=False)
            self.indexdatainit()


datastore = DataStore()

if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()
    config.init('prod')
    sparkmanager.init()

    datastore.paraminit()
    datastore.indexdatainit()

    temp = lambda: datastore._upload_azure(
        r'\\pue2mal01\e$\_cache\__beta_attrib_report_datastore__\2022-10-18_issuer_debt.cache')

    je = JobExecutor()
    je.submitjob(temp)
    pass
