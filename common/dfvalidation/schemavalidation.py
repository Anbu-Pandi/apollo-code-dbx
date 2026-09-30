import logging
from datetime import date, datetime
from functools import partial

import numpy as np
import pandas as pd

from common.utils.dfutil import tolist


class SchemaValidation():

    @staticmethod
    def _validatecolsmandatory(df, col, ismandatory):
        if (ismandatory and col not in df.columns):
            return (f'{col} missing from df columns: {df.columns}', len(df))
        return None

    @staticmethod
    def _validatecolsnans(df, col, allownans):
        dfs = df[col]
        dfnulls = dfs.isnull()
        if (not allownans and dfnulls.any()):
            return (f'{col} in df contains null: {dfs[dfnulls].head()}', len(dfs[dfnulls]))
        return None

    @staticmethod
    def _validatecolsuniq(df, col, isuniq):
        dfs = df[col]
        dfdupes = df[col].duplicated()
        if (isuniq and dfdupes.any()):
            return (f'{col} in df contains duplicates: {dfs[dfdupes].head()}', len(dfs[dfdupes]))
        return None

    @staticmethod
    def _validatecolsdtypes(df, col, dtypes):
        if not isinstance(dtypes, (tuple, list)):
            dtypes = (dtypes,)
        dfs = df[col].replace(to_replace='None', value=np.nan).dropna()
        if len(dtypes) == 0:
            dtypematches = dfs.map(lambda x: True)
        elif type in [type(x) for x in dtypes]:
            dtypematches = dfs.map(lambda x: isinstance(x, dtypes))
        elif len(dtypes) == 1 and dtypes[0][:2] == 'r:':
            dtypematches = dfs.str.contains(dtypes[0][2:], regex=True)
        else:
            dtypematches = dfs.isin(dtypes)
        if not dtypematches.all():
            return (f"{col} in df don't match dtypes: {dtypes} {dfs[~dtypematches].head()}", len(dfs[~dtypematches]))

        return None

    @staticmethod
    def _validatecols(df, colinfoschema):
        errorsdf = pd.DataFrame()
        colcheckfuncs = {
            'ismandatory': SchemaValidation._validatecolsmandatory,
            'allownans': SchemaValidation._validatecolsnans,
            'dtypes': SchemaValidation._validatecolsdtypes,
            'isunique': SchemaValidation._validatecolsuniq,
        }
        for col in colinfoschema:
            colschema = colinfoschema[col]
            for (validatetype, colvalidatefunc) in colcheckfuncs.items():
                if (col in df.columns) or (validatetype == 'ismandatory'):
                    errval = colvalidatefunc(df, col, colschema[validatetype])
                    if not (errval is None):
                        errdf = pd.DataFrame({
                            'column': col,
                            'validatetype': validatetype,
                            'description': errval[0],
                            'failedcount': errval[1],
                            'totalcount': len(df)
                        }, index=[0])
                        errorsdf = pd.concat([errorsdf, errdf])

        return errorsdf

    @staticmethod
    def _validateuniq(df, uniqcols):
        uniqcols = tolist(uniqcols, replacenone=True)
        if len(uniqcols) > 0:
            dupes = df[uniqcols].duplicated()
            if dupes.any():
                dfdupes = df[uniqcols][dupes]
                return (f'non unique values: {dfdupes.head()}', len(dfdupes))
        return None

    @staticmethod
    def _validatemptydf(df, allowempty):
        if (not allowempty) and len(df) == 0:
            return ('empty df not allowed', 0)
        return None

    @staticmethod
    def _validateforcetypes(df, forcetypes, dtypeschema):
        if (forcetypes):
            for col in df.columns:
                dtypes = dtypeschema[col]
                if not SchemaValidation._validatecolsdtypes(df, col, dtypes) is None:
                    logging.info(f"forcedtypes: {col} to type: {dtypes}")
                    dtypes = tolist(dtypes)
                    istype = lambda t: np.any([x in t for x in dtypes])
                    if istype([float, int]):
                        df[col] = pd.to_numeric(df[col], errors='coerce')
                    if istype([date, datetime]):
                        df[col] = pd.to_datetime(df[col], errors='coerce')
                    if istype([str, object]):
                        df[col] = df[col].map(str)

    @staticmethod
    def _createcolsmandatory(df, col):
        return True

    @staticmethod
    def _createcolsnans(df, col):
        if df[col].isnull().any():
            return True
        return False

    @staticmethod
    def _createcolsuniq(df, col):
        if not df[col].duplicated().any():
            return True
        return False

    @staticmethod
    def _createcolsdtypes(df, col):
        dfs = df[col].dropna()
        if len(dfs) > 0:
            return tuple(x for x in dfs.map(lambda x: type(x)).drop_duplicates())
        return ()

    @staticmethod
    def _createcols(df, col):
        colschema = {}

        colschema['ismandatory'] = SchemaValidation._createcolsmandatory(df, col)
        colschema['allownans'] = SchemaValidation._createcolsnans(df, col)
        colschema['isunique'] = SchemaValidation._createcolsuniq(df, col)
        colschema['dtypes'] = SchemaValidation._createcolsdtypes(df, col)
        colschema['description'] = col.replace('_', ' ').title()

        return colschema

    @staticmethod
    def _createuniqcols(df):
        clist = []
        for c in df.columns:
            clist += [c]
            if not df[clist].duplicated().any():
                return clist
        return []

    @staticmethod
    def _createmptydf(df, ):
        allowempty = len(df) == 0
        return allowempty

    @staticmethod
    def _checktype(v, t):
        typerrormsg = f"{type(v)} doesn't match types: {t}\nvalues: {v}"
        listtypes = [list, tuple]
        if type(t) in listtypes:
            nonlisttypes = tuple([x for x in t if x not in listtypes])
            if type(v) in listtypes:
                assert np.all([isinstance(x, nonlisttypes) for x in v]), typerrormsg
            else:
                assert isinstance(v, nonlisttypes), typerrormsg
        else:
            assert isinstance(v, t), typerrormsg

    @staticmethod
    def checkschema(schema):
        assert type(schema) == dict, f"schema should be of the type dict but is {type(schema)}"

        schematypes = {'cols': (dict), 'uniquecols': (list, tuple, str), 'allowempty': bool, 'forcetypes': bool}
        mandatorytags = ['cols']
        for schematypekey in schematypes:
            assert (schematypekey in mandatorytags and schematypekey in schema) or \
                   (not schematypekey in mandatorytags), \
                f"missing key: {schematypekey}"
            if schematypekey in schema:
                SchemaValidation._checktype(schema[schematypekey], schematypes[schematypekey])

        coltypes = {
            'ismandatory': bool,
            'allownans': bool,
            'isunique': bool,
            'dtypes': (list, tuple, type, str, date, datetime, float, int),
            'description': str
        }
        mandatorytags = ['dtypes', 'description']
        for coltypekey in coltypes:
            assert (coltypekey in mandatorytags and np.all(
                [coltypekey in schema['cols'][x] for x in schema['cols']])) or \
                   (not coltypekey in mandatorytags), \
                f"missing key: {coltypekey} in cols"
            for x in schema['cols']:
                if coltypekey in schema['cols'][x]:
                    logging.info(f'checking for column: {x}, col type: {coltypekey}')
                    SchemaValidation._checktype(schema['cols'][x][coltypekey], coltypes[coltypekey])

        return True

    @staticmethod
    def createschema(df):
        schema = {}
        allowempty = SchemaValidation._createmptydf(df)
        schema['allowempty'] = allowempty
        uniquecols = SchemaValidation._createuniqcols(df)
        schema['uniquecols'] = uniquecols
        schema['forcetypes'] = False
        schema['cols'] = {}
        for c in df.columns:
            colschema = SchemaValidation._createcols(df, c)
            schema['cols'][c] = colschema

        assert SchemaValidation.checkschema(schema), "Please check schema input"

        return schema

    @staticmethod
    def validate(df, schema, validatetypesubset=None):
        assert SchemaValidation.checkschema(schema), "Please check schema input"
        validatefuncs = {
            'allowempty': SchemaValidation._validatemptydf,
            'uniquecols': SchemaValidation._validateuniq,
            'forcetypes': partial(SchemaValidation._validateforcetypes,
                                  dtypeschema={c: schema['cols'][c]['dtypes'] for c in schema['cols']}),
            'cols': SchemaValidation._validatecols
        }
        errorsdf = pd.DataFrame(columns=['column', 'validatetype', 'description', 'failedcount', 'totalcount'])
        validatetypesubset = validatetypesubset or list(validatefuncs.keys())
        for (validatetype, validatefunc) in validatefuncs.items():
            if (validatetype in schema) and (validatetype in validatetypesubset):
                validateargs = schema[validatetype]
                errval = validatefunc(df, validateargs)
                if not errval is None:
                    if type(errval) == pd.DataFrame:
                        errdf = errval
                    else:
                        errdf = pd.DataFrame({
                            'column': '*',
                            'validatetype': validatetype,
                            'description': errval[0],
                            'failedcount': errval[1],
                            'totalcount': len(df)
                        }, index=[0])
                    errorsdf = pd.concat([errorsdf, errdf])

        return errorsdf


if __name__ == '__main__':
    from apo_config.config import config

    config.init('prod')

    from common.utils.logutil import setuplogger

    setuplogger('schemavalidation')

    from common.utils import sqlutil

    tsdf = sqlutil.readdfsql(f"select * from dbo.ice_index_all where asof_date='2023-01-23'")

    pd.set_option('display.max_columns', None)
    pd.set_option('display.max_rows', 10)

    schema = SchemaValidation.createschema(tsdf)
    logging.info(schema)
    schema['forcetypes'] = False
    SchemaValidation.checkschema(schema)
    errors = SchemaValidation.validate(tsdf.loc[[False] * len(tsdf), :], schema, validatetypesubset=['allowempty'])
    logging.info(errors)

    pass
