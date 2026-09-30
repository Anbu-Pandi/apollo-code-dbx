import datetime
import getpass
import logging
from collections import Counter

import numpy as np
import pandas as pd

from apo_config.configdb import configdb, ENVIRONMENTS

DEFAULT_ENV = 'dev'


class _Config:
    _ENV = None
    _CONFIGDICT = {}
    overrides = {}
    last_updated = datetime.datetime.now()

    def _get_configdict_from_db(self, dbenv, **kwargs):
        configdb.init(dbenv, self.getuser(actual=True))
        configdict = configdb.readconfigdict(self.getuser(), self.getuser(actual=True))
        return configdict

    def _update_nans_in_configdict(self):
        updated_dict = {}
        for k, v in self._CONFIGDICT.items():
            if pd.isnull(k[0]):
                updated_dict[('', k[1])] = v
            else:
                updated_dict[k] = v
        self._CONFIGDICT = updated_dict

    def is_stale(self):
        if (datetime.datetime.now() - self.last_updated).total_seconds() >= 1 * 60 * 60:  # is stale every 1 hour
            logging.warning(f'stale config. last updated: {self.last_updated}')
            return True
        return False

    def init(self, env=DEFAULT_ENV, overrides=None, configdict=None, runas=None, **kwargs):
        self.configdict = configdict
        self.overrides = overrides
        self._init(env, runas)

        import pandas as pd
        pd.DataFrame.iteritems = pd.DataFrame.items
        pd.Series.iteritems = pd.Series.items

    def init_and_get_configdict(self, env=DEFAULT_ENV, overrides=None, configdict=None, runas=None, **kwargs):
        self.init(env=env, overrides=overrides, configdict=configdict, runas=runas, **kwargs)
        return self._CONFIGDICT

    def _init(self, env, runas=None):
        self.runas = runas
        if (self._ENV is None) or (self.is_stale()):
            logging.info(
                'reading config env=%s overrides=%s supplied_config=%s',
                env,
                bool(self.overrides),
                isinstance(self.configdict, dict),
            )
            assert env.lower() in ENVIRONMENTS, f"{env} not valid. please choose from: {ENVIRONMENTS}"
            self._ENV = env.lower()
            self.last_updated = datetime.datetime.now()
            if self.configdict is None or not isinstance(self.configdict, dict):
                self._CONFIGDICT = self._get_configdict_from_db(env)
            else:
                self._CONFIGDICT = self.configdict
            self._update_nans_in_configdict()
            app_counts = Counter(app or '(global)' for app, _ in self._CONFIGDICT)
            logging.info(
                'config loaded env=%s entries=%s apps=[%s]',
                self._ENV,
                len(self._CONFIGDICT),
                ', '.join(f'{app}:{count}' for app, count in sorted(app_counts.items())),
            )
            before_new_line = lambda x: x.split('\n')[0]
            key_printer = lambda k, v: f"{str(k).ljust(50, ' ')[:50]} = {before_new_line(str(v))[:100]}"
            logging.debug(f"configdict set to:\n\t" +
                         "\n\t".join([key_printer(k, v) for k, v in self._CONFIGDICT.items()]))
        else:
            assert env.lower() == self._ENV.lower(), f"cannot set env to {env}. already set to {self._ENV}"

        def _update_k(k):
            if isinstance(k, (list, tuple, set)):
                assert len(k) == 2, f'tuple/list/set key has to be (app,key) combo. invalid: {k}'
                k = tuple([k_.lower() for k_ in k])
            else:
                k = ('', str(k).lower())
            return k

        self.overrides = self.overrides or {}
        self.overrides = {_update_k(k): v for k, v in self.overrides.items()}
        logging.info(f"config connected to env: {self._ENV}")

    def getenv(self):
        return self._ENV

    def getuser(self, actual=False):
        logged_user = getpass.getuser().lower()
        if logged_user.startswith('spark-'):
            logged_user = 'root'
        if actual: return logged_user
        return self.runas or logged_user

    def getconfig(self, app, key):
        if self.is_stale():
            self._init(self._ENV)
        app = app or ''
        if (app, key.lower()) in self.overrides:
            return self.overrides[(app, key.lower())]
        if ('', key.lower()) in self.overrides:
            return self.overrides[('', key.lower())]

        if (app.lower(), key.lower()) in self._CONFIGDICT:
            return self._CONFIGDICT[(app.lower(), key.lower())]
        if ('', key.lower()) in self._CONFIGDICT:
            return self._CONFIGDICT[('', key.lower())]
        return None

    def getappconfig(self, app):
        if self.is_stale():
            self._init(self._ENV, self.runas)
        appconfig = {}
        for ((ak, kk), v) in self._CONFIGDICT.items():
            if (ak is None and kk not in appconfig) or (ak is not None and ak == app.lower()):
                appconfig[kk] = v
        return appconfig

    def setconfig(self, app, key, value):
        configdb.saveappconfigkey(self.getenv(), app, np.nan, key, value, isuseroverride=False)

    def setuseroverride(self, app, key, value):
        configdb.saveappconfigkey(self.getenv(), app, self.getuser(), key, value, isuseroverride=True)

    def setconfig(self, app, configdict):
        for (k, v) in configdict.items():
            self.setconfig(app, k, v)

    def importconfig(self, df, clean=False):
        if clean:
            configdb.clean()
        for (i, dfrow) in df.iterrows():
            configdb.saveappconfigkey(dfrow['env'], dfrow['app'], dfrow['username'],
                                      dfrow['configkey'], dfrow['configvalue'],
                                      isuseroverride=pd.notnull(dfrow['username']))


config = _Config()


def required_config(app, key):
    """Fetch a config value, raising if it is missing/None.

    Pure config access -- no service-topology logic (that lives in
    common.service_discovery)."""
    value = config.getconfig(app, key)
    if value is None:
        raise RuntimeError(f"Missing dbo.app_config value: {app}.{key}")
    return value


if __name__ == '__main__':
    # df = pd.read_clipboard()
    config.init('prod')
    # config.importconfig(df, clean=False)
    print(config.getconfig('apo_data_access.spark', 'spark_driver'))
    print(config.getconfig('apo_data_access', 'user'))
    print(config.getconfig('apo_data_access', 'database_name'))

    print(config.getconfig('apo_data_access.spark', 'spark_driver'))

    print(config._CONFIGDICT)

    pass
