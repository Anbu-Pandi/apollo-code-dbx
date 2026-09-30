import logging
from datetime import datetime, date

import numpy as np
import pandas as pd
import scipy.stats as stats

from common.utils import dtutil
from common.utils.dfutil import tolist


class TSValidation():
    VALIDATETYPES = {
        'missing': [],
        'stale': [('window', 126)],
        'percentile': [('window', 126), ('threshold', .01)],
        'zscore': [('window', 126), ('threshold', 3)],
        'range': [('lo', -1e32), ('hi', 1e32)],
        'percent': [('window', 1), ('threshold', 1e32)],
        'change': [('window', 1), ('threshold', 1e32)],
    }

    def _getvaluesbycol(self, vals, defaultvalue=None):
        valbycol = {}
        if type(vals) == dict:
            for c in self.cols:
                if c in vals:
                    valbycol[c] = vals[c]
                elif '*' in vals:
                    valbycol[c] = vals['*']
                else:
                    valbycol[c] = defaultvalue
        else:
            for c in self.cols:
                valbycol[c] = vals

        return valbycol

    @staticmethod
    def _todatestr(s, format='%Y%m%d %H:%M:%S'):
        return pd.to_datetime(s).strftime(format) if isinstance(s, (str, datetime, date, np.datetime64)) \
            else str(s)

    @staticmethod
    def _listtostr(l):
        l = list(l)
        smalll = l[:min(len(l), 5)]
        smalll = [TSValidation._todatestr(x) for x in smalll]
        if len(l) > 0:
            totallen = len(l)
            smalllen = len(smalll)
            if totallen == smalllen:
                return f"{','.join(smalll)}"
            else:
                return f"{','.join(smalll)} and {totallen - smalllen} more in the list"

        return ''

    def _parsetypes(self, validatetypes):
        if validatetypes is None:
            validatetypes = TSValidation.VALIDATETYPES.keys()
        elif isinstance(validatetypes, (list, tuple)):
            validatetypes = [x.lower() for x in tolist(validatetypes)]
        elif isinstance(validatetypes, (dict)):
            validatetypes = {k: self._parsetypes(v) for k, v in validatetypes.items()}
        else:
            validatetypes = []

        return validatetypes

    def _parseargs(self, **validateargs):
        self.defaultparamvalues = {}
        for vt in TSValidation.VALIDATETYPES:
            for args in TSValidation.VALIDATETYPES[vt]:
                if type(args) == tuple:
                    if len(args) > 1:
                        self.defaultparamvalues[f"{vt}_{args[0]}"] = args[1]
                    else:
                        self.defaultparamvalues[f"{vt}_{args[0]}"] = None
                else:
                    self.defaultparamvalues[f"{vt}_{args}"] = None

        actualparams = {k.lower(): v for (k, v) in validateargs.items() if k.lower() in self.defaultparamvalues}
        actualparams.update({k: v for (k, v) in self.defaultparamvalues.items()
                             if k not in actualparams})

        missingparams = [k for (k, v) in actualparams.items() if v is None]
        assert (len(missingparams) == 0), f"missing params: {self._listtostr(missingparams)}"

        return actualparams

    def _parsecolsdict(self, cols, validatetypes, **validateargs):
        if (cols is None) or (isinstance(cols, (tuple, list))):
            return cols, validatetypes, validateargs

        collist = []
        validateargs = {}
        validatetypes = {}

        def updateargs(col, argparam, argval):
            argparam = argparam.lower()
            if not argparam in validateargs:
                validateargs[argparam] = {}
            validateargs[argparam].update({col: argval})

        def updatetype(col, argparam):
            arg = argparam.split('_')[0]
            if not col in validatetypes:
                validatetypes[col] = []
            if not arg in validatetypes[col]:
                validatetypes[col] += [arg]

        if isinstance(cols, dict):
            for col, args in cols.items():
                collist += [col]
                for argparam, argval in args.items():
                    if argval is not None:
                        updateargs(col, argparam, argval)
                    updatetype(col, argparam)

        return (collist, validatetypes, validateargs)

    def __init__(self, df, datecol=None, groupbycol=None, validatetypes=None, cols=None, **validateargs):
        cols, validatetypes, validateargs = self._parsecolsdict(cols, validatetypes, **validateargs)
        self.validatetypes = self._parsetypes(validatetypes)
        self.validateargs = self._parseargs(**validateargs)
        self.df = df
        self.dfgroups = None

        if not datecol is None:
            self.datecol = datecol
            self.df[datecol] = pd.to_datetime(self.df[datecol])
        else:
            assert groupbycol is None, "cannot group when index is used as datecol. reset index to use datecol and groupbycol."
            assert len(df.index.names) == 1, f"index is used as datecol. got multiple index: {df.index.names} "
            self.df.index = pd.to_datetime(self.df.index)
            self.datecol = self.index.names[0]
            self.df = self.df.reset_index()

        self.groupbycol = [] if groupbycol is None else tolist(groupbycol)
        self.df = self.df.drop_duplicates()
        gby = self.groupbycol + [self.datecol]
        assert not self.df[gby].duplicated().any(), f"{gby} in df must be unique"

        if not isinstance(self.validatetypes, dict):
            assert np.all([x in TSValidation.VALIDATETYPES for x in self.validatetypes]), "check validate inputs"

        cols = [x.lower() for x in tolist(cols, replacenone=True)]
        self.cols = [x for x in self.df.columns if (x not in gby) and ((x.lower() in cols) or len(cols) == 0)]

        if len(self.cols) == 0:
            logging.warning(
                f'no valid columns selected to validate: {self._listtostr(cols)},\ndf columns: {self.df.columns}')

        if not isinstance(self.validatetypes, dict):
            self.validatetypes = self._getvaluesbycol(self.validatetypes, [])
        self.validateargs = {k: self._getvaluesbycol(v, self.defaultparamvalues[k])
                             for (k, v) in self.validateargs.items()}

        self.df = self.df.sort_values(by=gby)
        logging.info(f'TS validate types: {self.validatetypes}')
        logging.info(f'TS validate args: {self.validateargs}')
        logging.info(f'TS validate col: {self.cols}')
        logging.info(f'TS date col: {self.datecol}')
        logging.info(f'TS group by col: {self.groupbycol}')

    def _createdfgroups(self):
        if self.dfgroups is None:
            self.dfgroups = self.df.groupby(self.groupbycol)

    def validate(self, fromdate=None):
        self._createdfgroups()
        validaterrors = pd.DataFrame(
            columns=['column', 'validatetype', 'group', 'description', 'failedcount', 'totalcount'])

        for (groupname, dfg) in self.dfgroups:
            for col in self.cols:
                dfc = dfg[pd.notnull(dfg[col])]
                if col in self.validatetypes:
                    for vtype in self.validatetypes[col]:
                        totalcount = len(dfc) if fromdate is None \
                            else len(dfc[dfc[self.datecol] >= pd.to_datetime(fromdate)])
                        funcname = f'{vtype}check'
                        args = {'df': dfc, 'c': col}
                        addtnlargs = {argname.replace(vtype + '_', ''): varg[col]
                                      for argname, varg in self.validateargs.items()
                                      if argname[:len(vtype) + 1] == vtype + '_'}
                        args.update(addtnlargs)
                        validatemethod = getattr(self, funcname)

                        logging.debug(f'running {funcname} for group: {groupname} col: {col} with args: {addtnlargs}')
                        errorsbycols = validatemethod(**args)
                        logging.debug(f'completed {funcname}, for col: {col}')

                        if col in errorsbycols:
                            faileddts = errorsbycols[col]
                            faileddts = [x for x in faileddts
                                         if (fromdate is None) or (pd.to_datetime(x) >= pd.to_datetime(fromdate))]
                            if len(faileddts) > 0:
                                validaterrors = pd.concat([validaterrors, pd.DataFrame({
                                    'column': col,
                                    'validatetype': vtype,
                                    'group': str(groupname),
                                    'description': self._listtostr(faileddts),
                                    'failedcount': len(faileddts),
                                    'totalcount': totalcount}, index=[0])])

        return validaterrors

    def stalecheck(self, df, c, window):
        stalecolumns = {}
        staledf = df.groupby((df[c] != df[c].shift(1)).cumsum()).cumcount(ascending=False) + 1
        dts = df[self.datecol][staledf > window].values
        if len(dts) > 0:
            stalecolumns[c] = dts

        return stalecolumns

    def missingcheck(self, df, c):
        missingdts = {}
        if len(df) > 1:
            alldts = df.loc[:, self.datecol].dt.normalize()
            dts = df.loc[df[c].dropna().index, self.datecol].map(dtutil.todate).unique()
            startdt = alldts.min()
            enddt = alldts.max()
            missingdts[c] = sorted(list(set(dtutil.daterange(startdt, enddt)) - set(dts)))

        return missingdts

    def _statcheck(self, df, statdf, lowthreshold, hithreshold, c):
        failedcols = {}
        dts = df[self.datecol][(statdf <= lowthreshold) | (statdf >= hithreshold)].values
        if len(dts) > 0:
            failedcols[c] = dts

        return failedcols

    def percentilecheck(self, df, c, window, threshold):
        def percentilefunc(x):
            return stats.percentileofscore(x.values, x.values[-1]) / 100

        statdf = df[c].rolling(window=window, min_periods=window, center=False) \
            .apply(percentilefunc)
        lowthreshold = max(0, min(1, min(np.abs(threshold), 1 - np.abs(threshold))))
        hithreshold = 1 - lowthreshold
        return self._statcheck(df, statdf, lowthreshold, hithreshold, c)

    def zscorecheck(self, df, c, window, threshold):
        def zscore(x, window):
            r = x.rolling(window=window, min_periods=window, center=False)
            m = r.mean().shift(1)
            s = r.std(ddof=0).shift(1)
            z = (x - m) / s
            return z

        statdf = zscore(df[c], window)
        lowthreshold = - np.abs(threshold)
        hithreshold = - lowthreshold
        return self._statcheck(df, statdf, lowthreshold, hithreshold, c)

    def rangecheck(self, df, c, lo, hi):
        failedcols = {}
        statdf = df[[c]].applymap(lambda x: (x >= hi) or (x <= lo))
        dts = df[self.datecol][statdf[c]].values
        if len(dts) > 0:
            failedcols[c] = dts

        return failedcols

    def percentcheck(self, df, c, window, threshold):
        lorange = -np.abs(threshold)
        hirange = -lorange
        failedcols = {}
        statdf = (df[[c]] / df[[c]].shift(-window) - 1) \
            .applymap(lambda x: (x >= hirange) or (x <= lorange))
        dts = df[self.datecol][statdf[c]].values
        if len(dts) > 0:
            failedcols[c] = dts

        return failedcols

    def changecheck(self, df, c, window, threshold):
        lorange = -np.abs(threshold)
        hirange = -lorange
        failedcols = {}
        statdf = (df[[c]] - df[[c]].shift(-window)) \
            .applymap(lambda x: (x >= hirange) or (x <= lorange))
        dts = df[self.datecol][statdf[c]].values
        if len(dts) > 0:
            failedcols[c] = dts

        return failedcols


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger('tsvalidation')

    pd.set_option('display.max_columns', None)
    pd.set_option('display.max_rows', 10)

    tsdf = pd.read_pickle(r'testdata\tsvalidation.pkl')
    # tsv = TSValidation(tsdf, datecol='date', groupbycol=['key1', 'key2'],
    #                    range_lo={'col1': 0}, range_hi={'col1': 90})
    tsv = TSValidation(tsdf, datecol='date', groupbycol=['key1', 'key2'],
                       cols={'col1': {'range_lo': 0, 'range_hi': 90}})

    errors = tsv.validate()

    pass
