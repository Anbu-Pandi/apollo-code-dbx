import logging

import numpy as np
import pandas as pd

from common.utils import dtutil, sqlutil
from data_access.dbtables import BOND_REFERENCEDATA_APP, ICE_INDEX_APP
from data_access.utils import tosqllist
from data_access.beta_attribution import security_ts_hist
from data_access.beta_attribution.jpm_dq import get_symbols_by_secid
from data_access.dbtables.security_data.extended.bond_security_master import get_sec_details
from jp_data_api.dqhelper import get_expr_results

from dash_reports.issuer_debt_in_index_movers.data.sql_const import BOND_OAS_PCTILE_SQL
from dash_reports.issuer_debt_in_index_movers.data import utils

BOND_REF_INDEX = 'CDX_HY_OTR'


def get_idx_level(idx_ticker, startdt, enddt):
    jpquery = get_symbols_by_secid(idx_ticker, 'DAILY')['QUERY'].iloc[0]
    df = get_expr_results(jpquery, startdt, enddt, 'd')
    df.index = pd.to_datetime(df.index.map(str)).date
    df = df.sort_index(ascending=False)
    return df


def add_data_by_instrument_id(df, addtnl_df, join_col):
    left_col = join_col if join_col in df.columns else 'instrument_id'
    df = pd.merge(df, addtnl_df, left_on=left_col, right_on=join_col, how='left')
    return df


def get_reference_index_returns(dt, history_tenor):
    cdx_level = utils.get_idx_level(BOND_REF_INDEX,
                                    dtutil.dateadd(dt, f'-{history_tenor}-1b'), dt)
    cdx_return_series = cdx_level / cdx_level.shift(-1) - 1
    return cdx_return_series


def get_bondindex_data(ticker, refdt, history_period):
    fromdt = dtutil.dateadd(refdt, f'-{history_period}')
    todt = dtutil.todate(refdt)
    sel_cols = 'bbg.id_bb_global, ii.asof_date as asof_date, ii.ice_index_ticker as ice_index_ticker, ii.cusip as cusip, ii.isin_number as isin_number, ii.description as description, ' \
               'ii.ticker as ticker, ii.par_wtd_coupon as par_wtd_coupon, ii.maturity_date as maturity_date, ii.rating as rating, ii.iso_currency_code as iso_currency_code, ' \
               'ii.iso_country_code as iso_country_code, ii.industry_lvl1_desc as industry_lvl1_desc, ii.industry_lvl2_desc as industry_lvl2_desc, ' \
               'ii.industry_lvl3_desc as industry_lvl3_desc, ii.industry_lvl4_desc as industry_lvl4_desc, ii.face_value_loc as face_value_loc, ' \
               'ii.price as price, ii.accrued_interest as accrued_interest, ii.percent_mkt_value as percent_mkt_value, ' \
               'ii.percent_mkt_value_prev_mend as percent_mkt_value_prev_mend, ii.cash as cash, ii.oas as oas, ' \
               'ii.asset_swap_spread as asset_swap_spread, ii.asset_swap_mtd_change as asset_swap_mtd_change, ' \
               'ii.effective_yield as effective_yield, ii.yield_to_worst_sa as yield_to_worst_sa, ii.yield_to_maturity_sa as yield_to_maturity_sa, ' \
               'ii.trr_pct_1month_local as total_return_percent_mtd_loc, ii.excess_return_pct_mtd as excess_return_percent_mtd, ' \
               'ii.effective_duration as effective_duration, ii.modified_duration_to_worst_sa as modified_duration_to_worst_sa, ' \
               'ii.modified_duration_sa as modified_duration_sa, ii.effective_convexity as effective_convexity, ' \
               'ii.convexity_to_worst_sa as convexity_to_worst_sa, ii.convexity_sa as convexity_sa, ii.announce_date as announce_date, ' \
               'ii.current_period_coupon as current_period_coupon, ii.maturity_wal as maturity_wal, ii.yield_to_worst_conv as yield_to_worst_conv, ' \
               'ii.subordination_type as subordination_type, ii.full_market_value_loc as full_market_value, ii.face_value_loc as current_face_value, ' \
               'ii.yield_to_maturity_conv as yield_to_maturity_conv, ii.prr_pct_1month_local as price_return_percent_mtd, ii.spread_duration as spread_duration, ' \
               'ii.years_to_worst as years_to_worst, ii.region as region, ii.factor as factor, ii.current_period_coupon / ii.price * 100  as current_yield, ' \
               'ii.trr_pct_1day_loc as total_return_percent_1day_loc, ii.libor_oas as libor_oas, ii.equity_market_cap as equity_mkt_cap, ' \
               'ii.equity_div_yield as equity_div_yield, ii.credit_spread as credit_spread, ' \
               'ii.oas_1_day_chg, ii.oas_1_week_chg, ii.oas_1_mo_chg, ii.oas_3_mo_chg, ii.oas_12_mo_chg, ii.oas_mtd_change, ii.oas_qtd_chg, ii.oas_ytd_chg'

    index_sql = '''
    select {} from dbo.ice_index_all ii
    left join f_get_bond_security_master(null) bbg on ii.isin_number=bbg.id_isin
    where ii.asof_date between '{}' and '{}'
    and ii.frequency = 'DAILY'
    and ii.ice_index_ticker = '{}'
    order by ii.asof_date
    '''
    index_df = sqlutil.readdfsql(index_sql.format(sel_cols, fromdt, todt, ticker), app=BOND_REFERENCEDATA_APP)
    index_df = index_df[index_df['total_return_percent_mtd_loc'] != -999]
    index_df = index_df[index_df['total_return_percent_1day_loc'] != -999]

    index_df['industry'] = index_df['industry_lvl3_desc']
    index_df['maturity'] = index_df['maturity_date']
    index_df['instrument_id'] = index_df['isin_number']
    index_df['bbg_id'] = index_df['id_bb_global']
    index_df['coupon'] = index_df['par_wtd_coupon']
    index_df['weights'] = index_df['percent_mkt_value'] / 100
    index_df['total_return'] = index_df['total_return_percent_1day_loc'] / 100
    index_df['tenor'] = utils.vectorized_calc_tenor(pd.to_datetime(index_df['maturity']).dt.date,
                                                    pd.to_datetime(index_df['asof_date']).dt.date)

    index_df = utils.update_df_with_wts_rets(index_df, 'asof_date', 'cusip', 'price', 'weights', 'total_return')

    return index_df


def get_bond_betas(index_df, refdt):
    df = security_ts_hist.get_timeseries(dtutil.dateadd(refdt, '-3m'),
                                         refdt,
                                         index_df['cusip'].dropna().unique().tolist(),
                                         'BETA_WITH_ALPHA_12M_CDXHY')
    return df


def add_bond_betas(index_df, refdt):
    if 'beta' in index_df.columns:
        return index_df
    df = get_bond_betas(index_df, refdt)
    if len(df) > 0:
        df = pd.merge(df, df.groupby('SEC_ID')[['ASOFDATE']].max().reset_index(), on=['SEC_ID', 'ASOFDATE']) \
            .rename(columns={'VALUE': 'beta'})
        df = pd.merge(index_df, df, left_on='cusip', right_on='SEC_ID', how='left')
    else:
        df = index_df.copy()
        df['beta'] = np.nan
    df = df[index_df.columns.tolist() + ['beta']]

    return df


def add_amt_outstanding(df):
    amtdf = get_sec_details(df['isin_number'].unique().tolist(),
                            id_column='id_isin',
                            select_cols=['id_isin', 'amt_outstanding']) \
        .rename(columns={'id_isin': 'isin_number'})

    df = pd.merge(df, amtdf, on='isin_number', how='left')
    df['amt_outstanding'] = df['amt_outstanding'] / 1e6
    return df


def add_oas_percentiles(df):
    dt = df['asof_date'].max()
    ice_index_tickers = df['ice_index_ticker'].unique().tolist()
    sql = BOND_OAS_PCTILE_SQL.format(dtutil.dateadd(dt, '-1y'), dt, tosqllist(ice_index_tickers))
    oas_df = sqlutil.readdfsql(sql, app=ICE_INDEX_APP)
    df = pd.merge(df, oas_df.drop(columns=['oas']), on='isin_number', how='left')
    return df


def get_index_df_with_betas(index, dt, trades=None):
    bondhistory = get_bondindex_data(index, dt, '1y')
    indexdf = bondhistory[pd.to_datetime(bondhistory['asof_date']).dt.date == dtutil.todate(dt)]

    if trades is not None:
        indexdf = add_data_by_instrument_id(indexdf, trades, join_col='cusip')
    else:
        logging.warning(f"not adding trades to index df: {index}/{dt}")

    indexdf = add_bond_betas(indexdf, dt)
    indexdf_estimated_beta = indexdf[pd.isnull(indexdf['beta'])]
    hyindexdf = get_bondindex_data('H0A0', dt, '0b')
    hyindexdf = add_bond_betas(hyindexdf, dt)

    cols = ['instrument_id', 'beta', 'industry', 'oas', 'tenor']
    modeldata = pd.concat([indexdf_estimated_beta[cols], hyindexdf[cols]]).drop_duplicates()
    modeldata = utils.add_estimated_beta(modeldata, "beta ~ industry + oas + tenor")
    estimated_beta_by_cusip = modeldata.set_index('instrument_id')['estimated_beta']

    indexdf['estimated_beta'] = indexdf['instrument_id'].map(estimated_beta_by_cusip)

    indexdf = add_amt_outstanding(indexdf)
    indexdf = add_oas_percentiles(indexdf)
    return indexdf
