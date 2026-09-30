import logging
import re

import numpy as np
import pandas as pd

from common.utils import sqlutil, dtutil, dfutil
from common.utils.sqlutil import sql_replace_escape_char
from data_access.dbtables import FINDOX_APP
from data_access.dbtables.factset.utils import get_metrics_reqd_by_custom_ff, get_metric_cols_csv, get_metric_cols, \
    add_custom_ff_fields, reverse_map_columns
from data_access.dbtables.findox import sql_const
from data_access.utils import get_where_clause, join_where_clauses, tosqllist
from data_access.utils import tolist

MODEL_SQL = '''
with issuer_dt as (
select issuer_id, max(knowledge_date) as max_kd
from findox_models
where knowledge_date <= {1}
and issuer_id  IN {0}
group by issuer_id
) 
select fd.* from 
findox_models fd
join issuer_dt id on fd.issuer_id=id.issuer_id and fd.knowledge_date=id.max_kd
where fd.issuer_id  IN {0}
'''

FINANCIAL_SQL = '''
with issuer_dt as (
select issuer_id, max(knowledge_date) as max_kd
from findox_financials
where knowledge_date <= {1}
and issuer_id  IN {0}
group by issuer_id
) 
select fd.* from 
findox_financials fd
join issuer_dt id on fd.issuer_id=id.issuer_id and fd.knowledge_date=id.max_kd
where
fd.is_private=0 
and fd.issuer_id  IN {0}
'''


def get_model_info(issuer_ids=None, knowledge_date=None, api_call_type='many_latest'):
    knowledge_date = pd.to_datetime(knowledge_date or dtutil.today()).strftime('%Y-%m-%d %hh:%mm:%ss')
    sql = MODEL_SQL.format(tolist(issuer_ids, replacenone=True), knowledge_date)
    df = sqlutil.readdfsql(sql, app=FINDOX_APP)
    return df


def get_financial_model(issuer_ids=None, knowledge_date=None):
    knowledge_date = pd.to_datetime(knowledge_date or dtutil.today()).strftime('%Y-%m-%d %hh:%mm:%ss')
    sql = FINANCIAL_SQL.format(tolist(issuer_ids, replacenone=True), knowledge_date)
    df = sqlutil.readdfsql(sql, app=FINDOX_APP)
    return df


FINDOX_LINE_ID_ORDER = {
    'revenue': [215, 1752, 1390, 1561, 220, 218],
    'calc_ebitda': [335, 1388],  # 335 = Calculated EBITDA
    'reported_ebitda': [338, 1397],  # 338 = Reported EBITDA
    'adj_ebitda': [381, 1395],  # 381 = Adj. EBITDA
    'ebitda_fda': [381, 338, 335, 1395, 1397, 1388],
    # 381 = Adj. EBITDA, 338 = Reported EBITDA, 335 = Calculated EBITDA
    'capex': [145, 628, 826],
    'cash_and_equivs': [39, 30, 19, 1737, 1599, 932, 817, 820, 1784, 1871, 819, 891, 765],
    '1st_lien_debt': [611],
    'total_debt': [614, 1087],
    'net_1st_lien_debt': [609],
    'interest_expense': [209, 286, 342, 351],
    'interest_coverage': [643, 646, 644],
    # 643 = Adj. EBITDA Int. cov., 646 = Reported EBITDA Int. cov., 644 = Calculated EBITDA Int. cov.
    'fixed_charge_coverage': [650, 647, 648]
    # 650 = Adj. EBITDA fixed charge cov., 647 = Reported EBITDA fixed charge cov, 648 = Calculated EBITDA fixed charge cov
    # 'enterprise_value': [1656]
}


def get_findox_line_id_df():
    data = []
    for item_idx, (key, values) in enumerate(FINDOX_LINE_ID_ORDER.items()):
        for idx, val in enumerate(values):
            data.append({"custom_line_name": key, "line_id": val,
                         "line_id_pref_order": idx + 1, "line_id_order": item_idx})

    df = pd.DataFrame(data)
    return df


LATEST_ABRIDGED_FINANCIALS_SQL = '''
with client_issuer_names as (                           
select 
    issuer_id,
    max(client_issuer_name) as apo_issuer_name                  
from                            
    findox_models_common_ids
where
    client_issuer_name is not null AND {1}
group by
    issuer_id
), client_ids as (
select                          
    issuer_id,
    concat_ws(';', sort_array(collect_set(concat(identifier_type_name, ':', identifier)))) AS identifier
from                            
    findox_models_common_ids
WHERE {1}
group by issuer_id   
), fin as (                          
select                          
    ff.issuer_id, ff.issuer_name,                       
    created_on_date_time,   ff.cut_date,    derived_period, period_end_date,    period_id,  period_name,
    SUBSTRING(period_name, 3, 1) as period_type,
    statement_id, statement_name,
    report_type,    fiscal_year,                    
    is_private, line_name,  reported_line_name, value,line_id, model_id, calculation, reference_id,             
    is_latest,row_ordinal,currency, load_date,                      
    cids1.identifier,
    coalesce(cids2.apo_issuer_name, ff.issuer_name) as apo_issuer_name,
    row_number() over(partition by ff.issuer_id, SUBSTRING(period_name, 3, 1), statement_id, line_id order by period_end_date desc, coalesce(cut_date, '1900-01-01') desc) as period_order,
    row_number() over(partition by ff.issuer_id, SUBSTRING(period_name, 3, 1), statement_id, line_id,period_end_date order by coalesce(cut_date, '1900-01-01') desc) as period_sub_order,
    max(period_end_date) over(partition by ff.issuer_id) as latest_period_end_date
from                            
    client_ids cids1                        
left join client_issuer_names cids2 on cids1.issuer_id=cids2.issuer_id
left join (SELECT * FROM findox_financials WHERE {1}) ff
on                          
    ff.issuer_id = cids1.issuer_id                       
where                           
period_end_date is not null                         
and is_latest = 1
and is_private = 0                           
)
select * from fin
where {0}
'''

MAX_PREF = 9999


def replace_ids_with_col_names(cell, ref_id_col_map):
    if pd.isnull(cell):
        return cell
    matches = re.findall(r'\b\w{32}\b', cell)
    for match in matches:
        if match in ref_id_col_map:
            cell = cell.replace(match, ref_id_col_map[match])
    return cell


def _process_financial_df(df, line_id_df):
    line_ids = line_id_df['line_id'].tolist()
    df['ref_line_name'] = df['line_name'] + '/' + df['period_name'].fillna('')
    reference_id_map = df.dropna(subset='reference_id').set_index(['reference_id'])['ref_line_name'].to_dict()
    df = pd.merge(df, line_id_df, on='line_id')
    df['line_name'] = df['custom_line_name'].combine_first(df['line_name'])

    df = df[df['line_id'].isin(line_ids) &
            (df['period_sub_order'] == 1) &
            df['period_id'].isin([1, 2, 3, 4, 5, 6, 7]) &
            df['statement_name'].isin(['Aggredium Standard', 'Balance Sheet',
                                       'Income Statement', 'EBITDA', 'Cap Table', 'Tear Sheet'])
            ]
    not_null = pd.notnull(df['calculation'])
    df.loc[not_null, 'calculation_formula'] = df.loc[not_null, 'calculation'].apply(
        lambda x: replace_ids_with_col_names(x, reference_id_map))

    def _drop_dupes(df):
        df = df.dropna(subset=['value'])
        df = df.sort_values(
            ['period_name', 'issuer_name', 'line_name', 'line_id_pref_order', 'statement_id', 'row_ordinal',
             'line_id_order', ])
        df = df.drop_duplicates(keep='first', subset=['period_name', 'issuer_name', 'line_name'])
        return df

    df['row_ordinal'] = df['line_id_order'].combine_first(df['row_ordinal'])
    df['line_id_pref_order'] = df['line_id_pref_order'].fillna(MAX_PREF)

    df = _drop_dupes(df)
    df = _add_custom(df)
    df = _drop_dupes(df)

    return df


def _add_custom(df):
    df = df.set_index(['issuer_id', 'period_name'])

    def _get_col(col):
        coldf = df[df['line_name'] == col]
        coldf['value'] = pd.to_numeric(coldf['value'], errors='coerce')
        return coldf

    line_1st_debt = _get_col('1st_lien_debt')
    cash_and_equivs = _get_col('cash_and_equivs')
    interest_expense = _get_col('interest_expense')
    capex = _get_col('capex')
    ebitda_fda = _get_col('ebitda_fda')

    net_1st_lien_debt = line_1st_debt.copy()
    net_1st_lien_debt['value'] = line_1st_debt['value'] - cash_and_equivs['value']
    net_1st_lien_debt = net_1st_lien_debt.dropna(subset=['value'])
    net_1st_lien_debt['reported_line_name'] = 'net_1st_lien_debt'
    net_1st_lien_debt['line_name'] = 'net_1st_lien_debt'
    net_1st_lien_debt['custom_line_name'] = 'net_1st_lien_debt'
    net_1st_lien_debt['line_id_pref_order'] = MAX_PREF
    net_1st_lien_debt['reference_id'] = np.nan
    net_1st_lien_debt['calculation'] = ' + @ ' + line_1st_debt['reference_id'] + ' - ' + cash_and_equivs['reference_id']
    net_1st_lien_debt['calculation_formula'] = ' + @ ' + line_1st_debt['line_name'] + ' - ' + cash_and_equivs[
        'line_name']

    fcc = ebitda_fda.copy()
    fcc['value'] = ebitda_fda['value'] / (interest_expense['value'] + capex['value'])
    fcc['reported_line_name'] = 'fixed_charge_coverage'
    fcc['line_name'] = 'fixed_charge_coverage'
    fcc['custom_line_name'] = 'fixed_charge_coverage'
    fcc['line_id_pref_order'] = MAX_PREF
    fcc['reference_id'] = np.nan
    fcc['calculation'] = ' + @ ' + ebitda_fda['reference_id'] + ' / (' + interest_expense['reference_id'] + \
                         ' + ' + capex['reference_id'] + ')'
    fcc['calculation_formula'] = ' + @ ' + ebitda_fda['line_name'] + ' / (' + interest_expense['line_name'] + \
                                 ' + ' + capex['line_name'] + ')'

    interest_coverage = ebitda_fda.copy()
    interest_coverage['value'] = ebitda_fda['value'] / interest_expense['value']
    interest_coverage['reported_line_name'] = 'interest_coverage'
    interest_coverage['line_name'] = 'interest_coverage'
    interest_coverage['custom_line_name'] = 'interest_coverage'
    interest_coverage['line_id_pref_order'] = MAX_PREF
    interest_coverage['reference_id'] = np.nan
    interest_coverage['calculation'] = ' + @ ' + ebitda_fda['reference_id'] + ' / ' + interest_expense['reference_id']
    interest_coverage['calculation_formula'] = ' + @ ' + ebitda_fda['line_name'] + ' / ' + interest_expense['line_name']

    incr_df = pd.concat([net_1st_lien_debt, fcc, interest_coverage], axis=0).reset_index()

    retdf = pd.concat([df.reset_index(), incr_df], axis=0, ignore_index=True)
    return retdf


def _latest_financials(issuer_ids, latest_period):
    line_id_df = get_findox_line_id_df()
    issuer_clause = get_where_clause('issuer_id', issuer_ids)
    period_clause = 'period_end_date=latest_period_end_date' if latest_period else '1=1'
    where_clause = join_where_clauses(' AND ', [period_clause, issuer_clause])
    sql = LATEST_ABRIDGED_FINANCIALS_SQL.format(where_clause, issuer_clause or '1=1')
    return _process_financial_df(sqlutil.readdfsql(sql, app=FINDOX_APP), line_id_df)


def get_latest_financial_models(issuer_ids=None):
    return _latest_financials(issuer_ids, latest_period=False)


def get_latest_period_financials(issuer_ids=None):
    return _latest_financials(issuer_ids, latest_period=True)


ALL_ISSUERS_SQL = '''
select                          
    distinct issuer_id, coalesce(client_issuer_name, issuer_name) as apo_issuer_name                     
from                            
    findox_models
where is_latest=1
'''


def get_all_issuers():
    df = sqlutil.readdfsql(ALL_ISSUERS_SQL, app=FINDOX_APP)
    return df


def normalize_df(df, custom_ff_fields):
    df['qtr'] = df['calendar_period']
    df['ticker'] = df['company_ticker']

    if 'data_qrt' in df.columns:
        df['dataitem_value'] = df['data_qrt']
    if 'data_ytd' in df.columns:
        df['dataitem_value'] = df['data_ytd']
    if 'data_ltm' in df.columns:
        df['dataitem_value'] = df['data_ltm']
    if 'data_fy' in df.columns:
        df['dataitem_value'] = df['data_fy']
    if 'data_hy' in df.columns:
        df['dataitem_value'] = df['data_hy']
    if 'dataitem_value' in df.columns:
        df['value'] = df['dataitem_value']
    df['company_name'] = df['client_issuer_name'].combine_first(df['system_issuer_name'])
    df['period_end_date'] = df['end_of_reporting_period']
    df['filing_date'] = df['report_issue_date']
    df['currency'] = df['reporting_currency']
    df['formula_metric'] = df['label'].map(sql_const.REVERSE_CAPIQ_FINDOX_MAP)
    df['dataitem_id'] = df['formula_metric']
    df['dataitem_name'] = df['label']
    for zf in sql_const.ZERO_DEFAULT_FIELDS:
        if zf not in df['formula_metric'].values and len(df) > 0:
            zero_row = df.drop_duplicates('company_id').iloc[:1].copy()
            zero_row[['formula_metric', 'dataitem_id', 'label', 'dataitem_name']] = zf
            zero_row[['value', 'dataitem_value']] = 0
            df = pd.concat([df, zero_row], ignore_index=True)
    if len(custom_ff_fields) > 0:
        df = add_custom_ff_fields(df, custom_ff_fields, 'formula_metric',
                                  ['metrics', 'dataitem_name', 'label'],
                                  'value',
                                  ['company_id'],
                                  ['period_end_date', 'qtr', 'filing_date'],
                                  other_field_name_cols=['line_id', 'dataitem_id'],
                                  other_field_value_cols=['dataitem_value'],
                                  field_rename_map=sql_const.REVERSE_CAPIQ_FINDOX_MAP)
        df = reverse_map_columns(df, metric_map=sql_const.CAPIQ_FINDOX_MAP, metric_col1='capiq_id',
                                 metric_col2='findox_label')
        data_cols = [c for c in df.columns if c.startswith('data_')]
        custom_mask = df['line_id'].str.startswith('CUSTOM_', na=False)
        for dc in data_cols:
            df.loc[custom_mask, dc] = df.loc[custom_mask, 'value']

    df['capiq_id'] = df['dataitem_id']
    return df


def get_company_financials(company_id_or_ticker, from_dt, to_dt, period_type, dataitems=None):
    from_dt = dtutil.todate(from_dt)
    to_dt = dtutil.todate(to_dt)
    dataitems = tolist(dataitems or 'NULL', replacenone=True)

    company_id_or_ticker = tosqllist(company_id_or_ticker)
    period_type = sql_const.PERIOD_TYPE_MAP.get(period_type[0], list(sql_const.PERIOD_TYPE_MAP.values())[0])

    metric_clause = ''
    custom_ff_fields = sql_const.CUSTOM_FINDOX_FIELDS.copy()
    if not ((len(dataitems) == 0) or (dataitems[0].upper() == 'NULL')):
        all_metrics = [x for values in sql_const.CAPIQ_FINDOX_MAP.values() for x in dfutil.tolist(values)]
        dataitems = get_metric_cols(dataitems, metric_map=sql_const.CAPIQ_FINDOX_MAP,
                                    all_metrics=all_metrics, use_unmapped=True)

        custom_ff_fields = {k: v for k, v in custom_ff_fields.items() if k in dataitems}
        metric_cols = [x for x in dataitems if x not in custom_ff_fields]
        metrics_reqd_by_custom_ff = get_metrics_reqd_by_custom_ff([v[0] for _, v in custom_ff_fields.items()],
                                                                  sql_const.CUSTOM_FINDOX_FIELDS)

        metric_cols = get_metric_cols_csv(metric_cols + metrics_reqd_by_custom_ff,
                                          metric_map=sql_const.CAPIQ_FINDOX_MAP,
                                          all_metrics=all_metrics,
                                          start_char='',
                                          use_unmapped=True) \
            .split(',')

        metric_clause = sql_replace_escape_char("and ( sdl.[label] in {0} or\n\t\t\tarrays_overlap( from_json(sdlm.other_labels, 'array<string>'), array{0} ) )").format(tosqllist(metric_cols))

    sql = sql_replace_escape_char(sql_const.COMPANY_FINANCIALS_SQL).format(period_type, metric_clause,
                                                                           company_id_or_ticker, from_dt,
                                                                           to_dt)
    df = sqlutil.readdfsql(sql, app=FINDOX_APP)
    df = df[df['rk'] == 1]
    df = normalize_df(df, custom_ff_fields)
    return df


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()

    from apo_config.config import config

    config.init('prod')

    df = get_company_financials(['ADBE'], '20250901', '20260209', 'ltm', dataitems=['ENTERP_VAL'])
    # df = get_company_financials(['13969'], '20220101', '20261231', 'ltm', dataitems=None)

    # df = get_latest_financial_models(['5402'])

    # df = get_latest_period_financials('EAF')

    logging.info(df.to_string())
