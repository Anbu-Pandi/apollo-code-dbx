import logging

import pyspark.sql.functions as sparkfuncs

from common.dfvalidation.schemavalidation import SchemaValidation
from common.dfvalidation.tsvalidation import TSValidation
from data_access.utils import tolist
from data_access.validation.helpers import VALIDATERESULTSCHEMA, _dateadd


def tsvalidatepandas(df, tsv_params, date_columns, gby_columns, fromdate=None, uniq_ts=True):
    try:
        numeric_cols = list(tsv_params['cols'].keys())
        tsvalidatecols = date_columns + gby_columns + numeric_cols
        if not uniq_ts and len(df) > 0:
            df[numeric_cols] = df[numeric_cols].astype(float)
            df = df[tsvalidatecols] \
                .groupby(date_columns + gby_columns).mean().reset_index()
        tsv = TSValidation(df, **tsv_params)
        errors = tsv.validate(fromdate)
        return errors
    except Exception as e:
        errmsg = f"TS validate failed at df group:\n" \
                 f"{df[gby_columns].drop_duplicates().head().to_string()}\n"
        raise Exception(errmsg) from e


def schemavalidatepandas(df, schema, gby_columns):
    try:
        errors = SchemaValidation.validate(df, schema)
        if (len(df) > 0) and (len(gby_columns) > 0):
            errors['group'] = str(df.iloc[0][gby_columns].values)
        else:
            errors['group'] = ''
        return errors
    except Exception as e:
        errmsg = f"Schema validate failed at df group:\n" \
                 f"{df[gby_columns].head().to_string()}\n" \
                 f"full df:\n{df.head().to_string()}"
        raise Exception(errmsg) from e


class Validator:
    def __init__(self, dfname, tsv_params, schema, uniq_ts=True):
        self.dfname = dfname
        self.tsv_params = tsv_params
        self.schema = schema
        self.date_columns = tolist(tsv_params['datecol'])
        self.uniq_ts = uniq_ts

        if 'groupbycol' in tsv_params:
            self.gby_columns = tolist(tsv_params['groupbycol'])
        else:
            self.gby_columns = []

    @staticmethod
    def _getdtcomparepred(lhs, rhs, compare):
        if compare == '==':
            return lhs == rhs
        elif compare == '>=':
            return lhs >= rhs + ' 00:00:00'
        elif compare == '<=':
            return lhs <= rhs + ' 23:59:59'
        raise Exception('Invalid compare!')

    @staticmethod
    def _getdatepredicate(date_columns, dt, compare):
        date_columns = tolist(date_columns)
        pred = None
        if dt is not None:
            dt = TSValidation._todatestr(dt, format='%Y-%m-%d')
            pred = Validator._getdtcomparepred(sparkfuncs.col(date_columns[0]), dt, compare)
            for dtcolum in date_columns[1:]:
                pred = pred & Validator._getdtcomparepred(sparkfuncs.col(dtcolum), dt, compare)

        return pred

    def _getpredicate(self, fromdate=None, todate=None):
        predfrom = Validator._getdatepredicate(self.date_columns, fromdate, '>=')
        predto = Validator._getdatepredicate(self.date_columns, todate, '<=')
        if not (predfrom is None or predto is None):
            pred = predto & predfrom
        elif predfrom is None:
            pred = predto
        elif predto is None:
            pred = predfrom
        else:
            pred = None
        return pred

    def tsvalidate(self, df, fromdate=None, todate=None):
        tsdf = df.select('*')
        pred = self._getpredicate(_dateadd(fromdate, months=-1), todate)
        if pred is not None:
            logging.info(f'filter ts {self.dfname} df using predicate: {pred}')
            tsdf = tsdf.filter(pred)

        uniq_ts = self.uniq_ts
        tsv_params = self.tsv_params
        date_columns = self.date_columns
        gby_columns = self.gby_columns

        def _tsvalidate(df):
            return tsvalidatepandas(df, tsv_params, date_columns, gby_columns,
                                    fromdate=fromdate,
                                    uniq_ts=uniq_ts)

        tserrors = tsdf.groupby(self.gby_columns) \
            .applyInPandas(_tsvalidate, schema=VALIDATERESULTSCHEMA)

        return tserrors

    def schemavalidate(self, df, fromdate=None, todate=None):
        schemadf = df.select('*')
        pred = self._getpredicate(fromdate, todate)
        if pred is not None:
            logging.info(f'filter schema {self.dfname} df using predicate: {pred}')
            schemadf = schemadf.filter(pred)

        schema = self.schema
        gby_columns = self.gby_columns

        def _schemavalidate(df):
            return schemavalidatepandas(df, schema, gby_columns)

        schemaerrors = schemadf.groupby(self.gby_columns) \
            .applyInPandas(_schemavalidate, schema=VALIDATERESULTSCHEMA)

        return schemaerrors

    def validatedf(self, df, fromdate=None, todate=None, num_partitions=20_000):
        logging.info(f'running df validate: {self.dfname}')
        if len(self.gby_columns) > 0:
            logging.info(f'partitioning df: {self.dfname} by: {self.gby_columns}')
            df = df.repartition(num_partitions, *self.gby_columns)
        df = df.cache()
        tserrors = self.tsvalidate(df, fromdate, todate)
        schemaerrors = self.schemavalidate(df, fromdate, todate)
        dfs = schemaerrors.union(tserrors)
        logging.info(f'completed {self.dfname} df  validate')
        return dfs
