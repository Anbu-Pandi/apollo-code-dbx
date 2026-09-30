import logging
import os
import shutil

import pandas as pd

from apo_config.config import config
from common.utils import dtutil
from data_access.dbtables import BETA_EST_SUBAPP
from data_access.storage import SharedFrameCache


class CacheManager:
    def __init__(self, cache_subdir: str, shared_cache=None):
        self.cache_subdir = cache_subdir
        self._shared_cache = shared_cache

    @property
    def shared_cache(self):
        if self._shared_cache is None:
            self._shared_cache = SharedFrameCache.local()
        return self._shared_cache

    def get_cache_dir(self, asof_date=None) -> str:
        base = config.getconfig(BETA_EST_SUBAPP, 'local_cache_dir')
        root = os.path.join(base, self.cache_subdir)
        d = os.path.join(root, dtutil.todate(asof_date).strftime('%Y%m%d')) if asof_date else root
        os.makedirs(d, exist_ok=True)
        return d

    def cache_path(self, asof_date, entity_id: str, key: str) -> str:
        safe_id = entity_id.replace('-', '_').replace('/', '_')
        return os.path.join(self.get_cache_dir(asof_date), f'{safe_id}_{key}.parquet')

    def read_cache(self, asof_date, entity_id: str, key: str) -> pd.DataFrame:
        cached = self.shared_cache.read(self.cache_subdir, asof_date, entity_id, key)
        if cached is not None:
            logging.info(f'cache hit: {self.cache_subdir}/{asof_date}/{entity_id}/{key}')
            return cached
        path = self.cache_path(asof_date, entity_id, key)
        if os.path.exists(path):
            logging.info(f'cache hit: {path}')
            cached = pd.read_parquet(path)
            self.shared_cache.write(self.cache_subdir, asof_date, entity_id, key, cached)
            return cached
        return None

    def write_cache(self, df: pd.DataFrame, asof_date, entity_id: str, key: str):
        logging.info(
            f'writing shared cache: {self.cache_subdir}/{asof_date}/{entity_id}/{key} shape={df.shape}'
        )
        self.shared_cache.write(self.cache_subdir, asof_date, entity_id, key, df)

    def read_many(self, asof_date, entity_ids, keys):
        entity_ids = list(entity_ids)
        entries = self.shared_cache.read_entries(self.cache_subdir, asof_date, entity_ids)
        result = {}
        missing = []
        migrations = []
        for entity_id in entity_ids:
            values = {}
            for key in keys:
                cached = entries.get((entity_id, key))
                if cached is None:
                    path = self.cache_path(asof_date, entity_id, key)
                    if os.path.exists(path):
                        cached = pd.read_parquet(path)
                        migrations.append(
                            (self.cache_subdir, asof_date, entity_id, key, cached)
                        )
                if cached is None:
                    missing.append(entity_id)
                    break
                values[key] = cached
            else:
                result[entity_id] = values
        self.shared_cache.write_many(migrations)
        return result, list(dict.fromkeys(missing))

    def write_many(self, asof_date, data_by_entity):
        self.shared_cache.write_many([
            (self.cache_subdir, asof_date, entity_id, key, frame)
            for entity_id, data in data_by_entity.items()
            for key, frame in data.items()
        ])

    def purge_old(self, keep_days: int = 7):
        root = self.get_cache_dir()
        cutoff = dtutil.dateadd(dtutil.today(), f'-{keep_days}d')
        self.shared_cache.purge_before(self.cache_subdir, dtutil.todate(cutoff))
        for name in os.listdir(root):
            subdir = os.path.join(root, name)
            if not os.path.isdir(subdir) or len(name) != 8 or not name.isdigit():
                continue
            if dtutil.todate(name) < dtutil.todate(cutoff):
                logging.info(f'purging old cache: {subdir}')
                shutil.rmtree(subdir, ignore_errors=True)
