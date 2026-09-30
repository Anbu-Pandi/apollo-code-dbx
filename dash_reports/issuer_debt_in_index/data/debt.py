import logging
import pickle
import time
import traceback

import numpy as np
import pandas as pd
import pyspark.sql.functions as sparkfuncs
from pyspark.sql.types import *
from scipy.interpolate import make_lsq_spline

from common.utils import dtutil, creditutil
from dash_reports.issuer_debt_in_index.data.bonds import get_bond_df
from dash_reports.issuer_debt_in_index.data.cds import get_cds_df
from dash_reports.issuer_debt_in_index.data.factset import merge_factset_dcs, \
    merge_factset_financials, merge_factset_equity_returns, merge_factset_equity_tickers, adjust_fx_financials, \
    merge_factset_estimates
from dash_reports.issuer_debt_in_index.data.findox import merge_findox_financials
from dash_reports.issuer_debt_in_index.data.fx import adjust_fx_oas, adjust_fx_amt_fields
from data_access.dbtables.market_data.spot.fx import get_fx
from dash_reports.issuer_debt_in_index.data.issuer_id_data import get_issuer_id_df
from dash_reports.issuer_debt_in_index.data.loans import get_loan_df
from dash_reports.issuer_debt_in_index.data.markit_bond_pricing import merge_markit_bond_pricing
from dash_reports.issuer_debt_in_index.data.restrictions import merge_restriction_df
from dash_reports.issuer_debt_in_index.data.returns_beta_calc.index_ts import get_index_period_returns, \
    BOND_REF_INDEX, LOAN_REF_INDEX
from dash_reports.issuer_debt_in_index.data.returns_beta_calc.utils import add_explain_returns
from data_access.dbtables.factset.georev import get_georev_from_isin
from data_access.spark.api import BlobDownloader
from data_access.spark.api import sparkmanager
from data_access.spark.transforms import df_join
from pricing_wrapper.products.bonds.yield_curve import BondCurve

SELECT_COLUMNS = ['issuer_amt_outstanding_usd', 'amt_issued_usd', 'amt_outstanding_usd',
                  'amt_issued_loc', 'amt_outstanding_loc',
                  'asof_date', 'beta',
                  'sector', 'broad_industry', 'narrow_industry', 'coupon', 'current_coupon',
                  'currency', 'effective_duration', 'estimated_beta', 'etf_change_1m', 'gross_volume_1m', 'index',
                  'industry', 'instrument_id', 'bbg_id', 'instrument_type', 'libor_oas', 'maturity', 'issue_date',
                  'maturity_years',
                  'modified_duration_to_worst_sa', 'loan_only_main_issuer',
                  'oas', 'oas_min', 'oas_max', 'oas_pctile',
                  'maturity_bucket_spread', 'secured_curve_spread', 'unsecured_curve_spread',
                  'secured_curve_spread_1Y', 'secured_curve_spread_2Y', 'secured_curve_spread_3Y',
                  'secured_curve_spread_4Y', 'secured_curve_spread_5Y',
                  'secured_curve_spread_7Y', 'secured_curve_spread_10Y',
                  'unsecured_curve_spread_1Y', 'unsecured_curve_spread_2Y', 'unsecured_curve_spread_3Y',
                  'unsecured_curve_spread_4Y', 'unsecured_curve_spread_5Y',
                  'unsecured_curve_spread_7Y', 'unsecured_curve_spread_10Y',
                  'curve_spread', 'spread_diff_bond_v_curve', 'spread_diff_bond_v_other_curve',
                  'price', 'spread_duration',
                  'rating', 'seniority', 'secured_unsecured_group',
                  'dtd_return', 'mtd_return', '1m_return', 'qtd_return', 'ytd_return', '1w_return',
                  'pecs_rr=0.0', 'pecs_rr=0.4', 'pecs_rr=0.5',
                  'rate_duration_rr=0.0', 'rate_duration_rr=0.4', 'rate_duration_rr=0.5',
                  'spread_duration_rr=0.0', 'spread_duration_rr=0.4', 'spread_duration_rr=0.5',
                  'surv_rr=0.0', 'surv_rr=0.4', 'surv_rr=0.5',
                  'tenor', 'ticker', 'issuer_name',
                  'company_corp_ticker', 'ult_parent_corp_ticker',
                  'id_bb_company', 'id_bb_global_company',
                  'id_bb_parent_co', 'id_bb_global_parent_co',
                  'id_bb_ultimate_parent_co', 'id_bb_global_ultimate_parent_co',
                  'volume_1m', 'years_since_issue', 'yield_to_worst_conv',
                  'yield_to_maturity_conv',
                  'index_ticker', 'maturity_bucket', 'rating_bucket', 'secured_unsecured',
                  'oas_1_day_chg', 'oas_1_week_chg', 'oas_1_mo_chg',
                  'oas_3_mo_chg', 'oas_12_mo_chg', 'oas_mtd_change', 'oas_qtd_chg', 'oas_ytd_chg',
                  'oas_usd', 'pecs_rr=0.4_usd', 'yield_to_worst_conv_usd',
                  'actual_cds_bond_basis_mid', 'liquidity_score', 'default_prob',
                  'cntry_of_domicile', 'cntry_of_incorporation', ]

BOND_COL_MAP = {'maturity_date': 'maturity', 'isin_number': 'instrument_id',
                'id_bb_global': 'bbg_id',
                'iso_currency_code': 'currency',
                'ice_index_ticker': 'index',
                'par_wtd_coupon': 'coupon',
                'industry_lvl2_desc': 'sector',
                'industry_lvl3_desc': 'broad_industry',
                'industry_lvl4_desc': 'narrow_industry',
                'amt_issued': 'amt_issued_loc', 'face_value_loc': 'amt_outstanding_loc',
                'current_period_coupon': 'current_coupon',
                'description': 'issuer_name'}

LOAN_COL_MAP = {'maturity_date': 'maturity',
                'loanx_id': 'instrument_id',
                'launch_date': 'issue_date',
                'facility_curr': 'currency',
                'current_rating': 'rating',
                'lcd_broad_industry': 'broad_industry',
                'original_amount': 'amt_issued_loc', 'current_outstanding': 'amt_outstanding_loc',
                'fw_3y_yld': 'yield_to_worst_conv', 'fw_mat_yld': 'yield_to_maturity_conv',
                'fw_3y_spd': 'oas',
                'fw_3y_moddur': 'spread_duration',
                'issuer_name': 'issuer_name'}

CDS_COL_MAP = {}

AZURE_TEMP_ISS_CURVE_FILE = 'issuer_debt_issuer_curves'
AZURE_TEMP_ISS_CURVE_POST_APPLY_FILE = 'issuer_debt_applied_issuer_curves'
AZURE_TEMP_ISS_CURVE_FOLDER = 'data/_cache'


def clean_azure_temp_files():
    bd = BlobDownloader()
    bd.delete_dir(AZURE_TEMP_ISS_CURVE_FOLDER + '/' + AZURE_TEMP_ISS_CURVE_FILE)
    bd.delete_dir(AZURE_TEMP_ISS_CURVE_FOLDER + '/' + AZURE_TEMP_ISS_CURVE_POST_APPLY_FILE)


def _merge_industry_by_ticker(df):
    index_order = {
        'JPM_': 10000,
        'CDS': 99999,
        'default': 0,
        'null': -1
    }

    conditions = [
        df['index'].isnull(),
        df['index'].fillna('').str.startswith('JPM_'),
        df['index'].fillna('') == 'CDS'
    ]
    choices = [index_order['null'], index_order['JPM_'], index_order['CDS']]
    df['_index_order'] = np.select(conditions, choices, default=index_order['default'])

    ind_cols = ['broad_industry', 'narrow_industry', 'sector']
    df = df.sort_values(['_index_order'] + ind_cols).drop(columns=['_index_order'])

    ticker_industry_map = df.dropna(subset=['ticker'] + ind_cols)
    ticker_industry_map = ticker_industry_map.drop_duplicates(subset='ticker', keep='first').set_index('ticker') \
        [ind_cols]

    df = pd.merge(df, ticker_industry_map, left_on='ticker', right_index=True, how='left', suffixes=('', '_new'))

    for ic in ind_cols:
        df[ic] = df[f'{ic}_new'].fillna(df[ic])
    df = df.drop(columns=[f'{x}_new' for x in ind_cols])

    return df


def map_columns_debt_df(asof_date, df):
    df = df.drop_duplicates(subset=['instrument_id'])

    df['rating_bucket'] = creditutil.map_rating(df['rating'])

    df['maturity_years'] = (pd.to_datetime(df['maturity'], errors='coerce') -
                            pd.to_datetime(asof_date)).dt.days / 365
    df['maturity_years'] = df['maturity_years'].round(2)
    df['maturity_bucket'] = creditutil.map_maturity_bucket(df['maturity_years'])

    def _is_sec_unsec_pair(ds):
        sec_unsec_group = ['SEC', 'UNSEC']
        ds = ds[ds.fillna('').isin(sec_unsec_group)]
        return len(ds.unique()) > 1

    df['secured_unsecured_group'] = df.groupby(['ticker', 'maturity_bucket'], dropna=False)['secured_unsecured'] \
        .transform(_is_sec_unsec_pair)

    return df


def lsq_fit(x, y, k=3):
    t = creditutil.MATURITY_BUCKET[1:]
    t[0] = min(x)
    t[-1] = max(x)
    t = np.r_[[t[0]] * k, t, [t[-1]] * k]
    spl = make_lsq_spline(x, y, t, k)
    return spl


def calc_wts(issuer_df):
    wts = {'years_since_issue': 0.4, 'amt_outstanding_usd': 0.4, 'gross_volume_1m': 0.2}
    wts_df = issuer_df[wts.keys()]
    wts_df['years_since_issue'] = (2 - wts_df['years_since_issue']).clip(lower=0)
    wts_df /= wts_df.std()
    if len(wts_df) != 1:
        wts_df.replace([np.inf, -np.inf, np.nan], 0.10, inplace=True)
    else:
        wts_df.replace([np.inf, -np.inf, np.nan], 1, inplace=True)
    wts_df = (wts_df * wts.values()).sum(axis=1)
    if wts_df.sum() != 0:
        wts_df /= wts_df.sum() / len(wts_df)
        return wts_df


def add_wts(issuer_df):
    if 'weight' not in issuer_df.columns:
        issuer_df['weight'] = 0.10

    wts_list = []
    for i, x in issuer_df.groupby(['ticker', 'secured_unsecured']):
        wts = calc_wts(x)
        if wts is not None:
            wts_list.append(wts)

    wts = pd.concat(wts_list)
    wts = wts.reindex(issuer_df.index)
    issuer_df['weight'] = wts.combine_first(issuer_df['weight'].replace({None: np.nan})).fillna(0.10)
    return issuer_df


def change_tuple(tup, ix, val):
    lst = list(tup)
    lst[ix] = val
    return tuple(lst)


def add_curve_points(dt, issuer_df, spread_col, spread_col_factor=10_000):
    issuer_df = issuer_df.sort_values(by='maturity')
    long_rate = issuer_df.iloc[-1][spread_col] / spread_col_factor
    short_rate = issuer_df.iloc[0][spread_col] / spread_col_factor
    long_mty = issuer_df.iloc[-1]['maturity']
    short_mty = issuer_df.iloc[0]['maturity']
    lower_bound = 0
    if spread_col_factor == 100:
        lower_bound = short_rate
    short_df = pd.DataFrame({'weight': [1] * 2,
                             'maturity': [dtutil.dateadd(dt, '1d'), dtutil.dateadd(dt, '6m')],
                             'current_coupon': [0] * 2,
                             spread_col: [(lower_bound + (short_rate - lower_bound) * 0.2) * spread_col_factor,
                                          (lower_bound + (short_rate - lower_bound) * 2.0 / 3.0) * spread_col_factor]},
                            index=range(2))
    long_df = pd.DataFrame({'weight': [1] * 5,
                            'maturity': [dtutil.dateadd(long_mty, '10y'), dtutil.dateadd(long_mty, '20y'),
                                         dtutil.dateadd(long_mty, '30y'), dtutil.dateadd(long_mty, '40y'),
                                         dtutil.dateadd(long_mty, '50y')],
                            'current_coupon': [0] * 5,
                            spread_col: [long_rate * spread_col_factor * 1.1] * 5},
                           index=range(5))
    issuer_df = pd.concat([short_df[short_df['maturity'] < short_mty], issuer_df, long_df])
    return issuer_df


def get_finpy_curve(issuer_df, spread_col, spread_col_factor=10_000):
    issuer_df[spread_col] = pd.to_numeric(issuer_df[spread_col], errors='coerce')
    dt = issuer_df['asof_date'].dropna().max()
    issuer_df = add_wts(issuer_df)
    issuer_df = add_curve_points(dt, issuer_df, spread_col, spread_col_factor)
    issuer_df = issuer_df[['weight', 'maturity', 'current_coupon', spread_col]]

    k = 3
    fin_fit_types = ['nss', 'ns', 'poly', 'spline', ]
    for fit_type in fin_fit_types:
        fit_params = dict(fit_type=fit_type)
        if fit_type in ['poly', 'spline']:
            fit_params.update(power=k)
        if fit_type == 'spline':
            knots = creditutil.MATURITY_BUCKET
            fit_params.update(knots=sorted(knots))
        if 'weight' in issuer_df.columns:
            fit_params.update(w=issuer_df['weight'].values)

        logging.info(f'issuer curve build started: {fit_params}')
        bc = BondCurve(dt, issuer_df['current_coupon'] / 100,
                       issuer_df['maturity'],
                       issuer_df[spread_col] / spread_col_factor,
                       value_type='yield_to_maturity',
                       clear_vars=True,
                       **fit_params)
        logging.info(f'issuer curve completed: {fit_params}')
        return bc


def get_lsq_curve(issuer_df, spread_col, spread_col_factor=10_000):
    k = 2
    xy = issuer_df.sort_values(['maturity_years']).groupby(['maturity_years'])[spread_col].mean() / spread_col_factor
    x = xy.index.tolist()
    y = xy.values.tolist()
    fit = lsq_fit(x, y, k=k)
    return fit


def _get_curve(issuer_df, fit_type, spread_col_factor):
    try:
        issuer_df['maturity'] = pd.to_datetime(issuer_df['maturity'], errors='coerce').dt.date
        dt = issuer_df.iloc[0]['asof_date']
        ticker = issuer_df.iloc[0]['ticker']
        sec_unsec = issuer_df.iloc[0]['secured_unsecured']
        logging.info(f'issuer curve started: {dt} {ticker} {sec_unsec}')
        func = get_finpy_curve
        if fit_type == 'lsq':
            func = get_lsq_curve
        curve = func(issuer_df, 'oas_usd', spread_col_factor)
        logging.info(f'issuer curve completed: {dt} {ticker} {sec_unsec}')
        curve_pickle = pickle.dumps(curve)
        retdf = pd.DataFrame({'asof_date': dt, 'ticker': ticker, 'secured_unsecured': sec_unsec,
                              'curve': curve_pickle},
                             index=[0])
        return retdf
    except:
        logging.warning(traceback.format_exc())
        return pd.DataFrame()


def process_df_for_issuer_curve(df, spread_col):
    func_cols = ['asof_date', 'ticker', 'secured_unsecured', 'current_coupon', 'maturity', 'maturity_years',
                 spread_col, 'years_since_issue', 'amt_outstanding_usd', 'gross_volume_1m']

    df = df[func_cols].dropna(subset=['asof_date', 'ticker', 'secured_unsecured', 'current_coupon', 'maturity',
                                      'maturity_years', spread_col], how='any')
    df['oas_usd'] = pd.to_numeric(df[spread_col], errors='coerce')
    df['asof_date'] = pd.to_datetime(df['asof_date'])
    if spread_col != 'oas_usd':
        df = df.drop(columns=[spread_col])

    return df


def write_issuer_curves_to_azure(df, post_apply=False):
    f = AZURE_TEMP_ISS_CURVE_POST_APPLY_FILE if post_apply else AZURE_TEMP_ISS_CURVE_FILE
    sparkmanager.writefile(df, f, AZURE_TEMP_ISS_CURVE_FOLDER, overwrite=True, append=False)
    time.sleep(120)


def read_issuer_curves_from_azure(post_apply=False, pandas=False):
    read_func = sparkmanager.readfile_pandas if pandas else sparkmanager.readfile
    f = AZURE_TEMP_ISS_CURVE_POST_APPLY_FILE if post_apply else AZURE_TEMP_ISS_CURVE_FILE
    return read_func(f, AZURE_TEMP_ISS_CURVE_FOLDER)


def to_issuer_curves(retdf):
    issuer_curves = {}
    if not retdf is None:
        retdf['asof_date'] = pd.to_datetime(retdf['asof_date']).dt.date
        for k, dfi in retdf.groupby(['asof_date', 'ticker', 'secured_unsecured']):
            if (len(dfi) > 0) and (not dfi.iloc[0]['curve'] is None):
                issuer_curves[k] = pickle.loads(dfi.iloc[0]['curve'])
            else:
                logging.error(f"issuer curve error: {k}")

    return issuer_curves


def _get_curves_spark_job(df, get_curve_func):
    logging.info(f'starting issuer curves spark job')

    retdf = sparkmanager.pandastospark(df) \
        .groupBy('asof_date', 'ticker', 'secured_unsecured') \
        .applyInPandas(get_curve_func,
                       schema='asof_date timestamp, ticker string, secured_unsecured string, curve binary')

    if len(df['asof_date'].unique()) > 1:
        write_issuer_curves_to_azure(retdf, post_apply=False)
        retdf = None
        logging.warning(f"skipping apply issuer curves for df: {df.shape} with {len(df['asof_date'].unique())} dates")
    else:
        retdf = retdf.toPandas()
        logging.info(f'completed issuer curves spark job: {retdf.shape}')

    return retdf


def get_issuer_curves(df, spread_col='pecs_rr=0.4_usd', spread_col_factor=10_000, fit_type='finpy', use_spark=True):
    logging.info(f'starting get issuer curves: {df.shape} {spread_col} {spread_col_factor} {fit_type}')

    func_df = process_df_for_issuer_curve(df, spread_col)

    def get_curve_func(df):
        return _get_curve(df, spread_col_factor=spread_col_factor, fit_type=fit_type)

    if len(func_df['ticker'].unique()) == 1 or (not use_spark):
        dfs = []
        for i, dfg in func_df.groupby(['asof_date', 'ticker', 'secured_unsecured']):
            dfs.append(get_curve_func(dfg))
        retdf = pd.concat(dfs)
    else:
        retdf = _get_curves_spark_job(func_df, get_curve_func)

    issuer_curves = to_issuer_curves(retdf)
    logging.info(f'completed get issuer curves: {df.shape} {spread_col} {spread_col_factor} {fit_type}\n'
                 f'num issuer curves: {len(issuer_curves)}')

    return issuer_curves


def _calc_rel_spreads(df, spread_col):
    def _curve_spread(sec_unsec, sec_spread, unsec_spread):
        if sec_unsec == 'SEC':
            return sec_spread
        if sec_unsec == 'UNSEC':
            return unsec_spread

    def _other_curve_spread(sec_unsec, sec_spread, unsec_spread):
        if sec_unsec == 'UNSEC':
            return sec_spread
        if sec_unsec == 'SEC':
            return unsec_spread

    df['curve_spread'] = df[['secured_unsecured', 'secured_curve_spread', 'unsecured_curve_spread']] \
        .apply(lambda x: _curve_spread(x['secured_unsecured'], x['secured_curve_spread'], x['unsecured_curve_spread']),
               axis=1)
    df['other_curve_spread'] = df[['secured_unsecured', 'secured_curve_spread', 'unsecured_curve_spread']] \
        .apply(lambda x:
               _other_curve_spread(x['secured_unsecured'], x['secured_curve_spread'], x['unsecured_curve_spread']),
               axis=1)
    df['spread_diff_bond_v_curve'] = pd.to_numeric(df[spread_col], errors='coerce') - pd.to_numeric(df['curve_spread'],
                                                                                                    errors='coerce')
    df['spread_diff_bond_v_other_curve'] = pd.to_numeric(df[spread_col], errors='coerce') - pd.to_numeric(
        df['other_curve_spread'], errors='coerce')
    return df


def apply_issuer_curves_spark(spark_df, spread_col='pecs_rr=0.4_usd', spread_col_factor=10_000):
    logging.info(f'appyling issuer curves to df: {spark_df}')
    spark_df = spark_df.withColumn('asof_date', sparkfuncs.col('asof_date').cast(DateType()))
    issuer_curves_df = read_issuer_curves_from_azure(post_apply=False, pandas=False)
    issuer_curves_df = issuer_curves_df.withColumn('asof_date', sparkfuncs.col('asof_date').cast(DateType()))

    sec_issuer_curves_df = issuer_curves_df.filter(sparkfuncs.col('secured_unsecured') == 'SEC') \
        .withColumnRenamed('curve', 'sec_curve') \
        .drop("secured_unsecured")
    unsec_issuer_curves_df = issuer_curves_df.filter(sparkfuncs.col('secured_unsecured') == 'UNSEC') \
        .withColumnRenamed('curve', 'unsec_curve') \
        .drop("secured_unsecured")
    merged_df = df_join(spark_df, sec_issuer_curves_df, left_on=['asof_date', 'ticker'],
                        right_on=['asof_date', 'ticker'])
    merged_df = df_join(merged_df, unsec_issuer_curves_df, left_on=['asof_date', 'ticker'],
                        right_on=['asof_date', 'ticker'])

    def _calc_spreads(df):
        def _get_implied_value(curve, mty):
            try:
                bc = pickle.loads(curve)
                return spread_col_factor * bc.yield_to_maturity(mty)
            except:
                return np.nan

        df['curve'] = df['sec_curve']
        unsec_flag = (df['secured_unsecured'] == 'UNSEC')
        df.loc[unsec_flag, 'curve'] = df.loc[unsec_flag, 'unsec_curve']

        df['maturity_bucket_spread'] = df[['curve', 'maturity_bucket_mid']] \
            .apply(lambda x: _get_implied_value(x['curve'], x['maturity_bucket_mid']), axis=1)
        df['secured_curve_spread'] = df[['sec_curve', 'maturity']] \
            .apply(lambda x: _get_implied_value(x['sec_curve'], x['maturity']), axis=1)
        df['unsecured_curve_spread'] = df[['unsec_curve', 'maturity']] \
            .apply(lambda x: _get_implied_value(x['unsec_curve'], x['maturity']), axis=1)
        df = _calc_rel_spreads(df, spread_col=spread_col)

        return df.drop(columns=['curve', 'sec_curve', 'unsec_curve'])

    dropped_cols = ['curve', 'sec_curve', 'unsec_curve']
    outschema = StructType([x for x in merged_df.schema if x.name not in dropped_cols] +
                           [StructField('maturity_bucket_spread', DoubleType(), nullable=True),
                            StructField('secured_curve_spread', DoubleType(), nullable=True),
                            StructField('unsecured_curve_spread', DoubleType(), nullable=True),
                            StructField('curve_spread', DoubleType(), nullable=True),
                            StructField('other_curve_spread', DoubleType(), nullable=True),
                            StructField('spread_diff_bond_v_curve', DoubleType(), nullable=True),
                            StructField('spread_diff_bond_v_other_curve', DoubleType(), nullable=True), ])

    dfwrite = merged_df.groupBy('asof_date', 'ticker').applyInPandas(_calc_spreads, schema=outschema)

    return dfwrite


def apply_issuer_curves(df, issuer_curves, spread_col='pecs_rr=0.4_usd', spread_col_factor=10_000):
    logging.info(f'appyling issuer curves to df: {df.shape}')

    if len(issuer_curves) == 0:
        logging.warning(f'not applying issuer curves to df shape: {df.shape} {spread_col} {spread_col_factor}')
        return df

    df['maturity_bucket_mid'] = df['maturity_bucket'].map(creditutil.calc_mid_maturity_bucket)

    def _get_implied_value(issuer, sec_unsec, dt, mty):
        key = (dtutil.todate(dt), issuer, sec_unsec)
        bc = issuer_curves.get(key, None)
        if not ((bc is None) or (pd.isnull(mty))):
            return spread_col_factor * bc.yield_to_maturity(mty)

    logging.info(f'applying issuer curves by maturity bucket')
    df['maturity_bucket_spread'] = df[['asof_date', 'ticker', 'secured_unsecured', 'maturity_bucket_mid']] \
        .apply(
        lambda x: _get_implied_value(x['ticker'], x['secured_unsecured'], x['asof_date'], x['maturity_bucket_mid']),
        axis=1)

    logging.info(f'applying issuer curves by secured curve')
    df['secured_curve_spread'] = df[['asof_date', 'ticker', 'maturity']] \
        .apply(lambda x: _get_implied_value(x['ticker'], 'SEC', x['asof_date'], x['maturity']), axis=1)

    logging.info(f'applying issuer curves by unsecured curve')
    df['unsecured_curve_spread'] = df[['asof_date', 'ticker', 'maturity']] \
        .apply(lambda x: _get_implied_value(x['ticker'], 'UNSEC', x['asof_date'], x['maturity']), axis=1)

    df = _calc_rel_spreads(df, spread_col)

    logging.info(f'applying issuer curves by tenor')
    issuer_df = df[['asof_date', 'ticker']].drop_duplicates()
    for cds_tenor in ['1Y', '2Y', '3Y', '4Y', '5Y', '7Y', '10Y']:
        issuer_df[f'secured_curve_spread_{cds_tenor}'] = issuer_df \
            .apply(lambda x: _get_implied_value(x['ticker'], 'SEC', x['asof_date'],
                                                dtutil.cdsdate(x['asof_date'], cds_tenor)),
                   axis=1)
        issuer_df[f'unsecured_curve_spread_{cds_tenor}'] = issuer_df \
            .apply(lambda x: _get_implied_value(x['ticker'], 'UNSEC', x['asof_date'],
                                                dtutil.cdsdate(x['asof_date'], cds_tenor)),
                   axis=1)

    df = pd.merge(df, issuer_df, on=['asof_date', 'ticker'], how='left')
    logging.info(f'completed appyling issuer curves to df: {df.shape}')
    return df


def add_issuer_curve_implied_spreads(df, spread_col='pecs_rr=0.4_usd', spread_col_factor=10_000, use_spark=True):
    logging.info(f'starting add issuer curve implied spreads df shape: {df.shape} {spread_col} {spread_col_factor}')
    issuer_curves = get_issuer_curves(df, spread_col=spread_col, spread_col_factor=spread_col_factor,
                                      use_spark=use_spark)
    df = apply_issuer_curves(df, issuer_curves, spread_col, spread_col_factor)
    logging.info(f'completed add issuer curve implied spreads df shape: {df.shape} {spread_col} {spread_col_factor}')
    return df


def merge_georev(df):
    dt = pd.to_datetime(df['asof_date'], errors='coerce').max()
    geo_rev_df = get_georev_from_isin(dt, df[df['instrument_type'] == 'Bond']['instrument_id'].unique().tolist())
    geo_rev_df.columns = [f'{x}_geo' for x in geo_rev_df.columns]
    df = pd.merge(df, geo_rev_df, how='left', left_on='instrument_id', right_on='isin_geo', suffixes=('', '_geo'))
    return df


def fill_ticker_level_details(debt_df):
    ticker_level_cols = ['ticker_region', 'bbg_ticker', 'currency_financial', 'loan_only_main_issuer',
                         'eq_mkt_cap_mm', 'three_month_volatility',
                         'ytd_pct', 'mtd_pct', 'one_month_pct', 'one_week_pct',
                         'secured_curve_spread_5Y', 'secured_curve_spread_2Y', 'secured_curve_spread_10Y',
                         'unsecured_curve_spread_5Y', 'unsecured_curve_spread_2Y', 'unsecured_curve_spread_10Y',
                         'cntry_of_domicile', 'cntry_of_incorporation',
                         'id_bb_company', 'id_bb_global_company',
                         'id_bb_parent_co', 'id_bb_global_parent_co', 'id_bb_ultimate_parent_co',
                         'id_bb_global_ultimate_parent_co'] + [x for x in debt_df.columns if x.endswith('_geo')]

    ticker_level_cols = [col for col in ticker_level_cols if col in debt_df.columns]
    grouped = debt_df.groupby('ticker')[ticker_level_cols].apply(lambda x: x.dropna().min())
    debt_df = debt_df.merge(grouped, on='ticker', how='left', suffixes=('', '_filled'))

    for col in ticker_level_cols:
        debt_df[col] = debt_df[col].fillna(debt_df[f'{col}_filled'])
        debt_df.drop(f'{col}_filled', axis=1, inplace=True)

    return debt_df


def get_debt_df(asof_date, calc_pecs_flag=True, build_issuer_curves=True, use_spark=True,
                merge_dcs_fs=True,
                merge_restrictions=True,
                tickers=None):
    asof_date = dtutil.todate(asof_date)
    
    logging.info(f'fetching spot fx data')
    spot_fx_df = get_fx(dtutil.dateadd(asof_date, '-2y'), asof_date)

    bonds_df = get_bond_df(asof_date, calc_pecs_flag=calc_pecs_flag, tickers=tickers)
    loans_df = get_loan_df(asof_date, tickers=tickers)
    cds_df = get_cds_df(asof_date)
    issuer_df = get_issuer_id_df(bonds_df['id_bb_company'].unique().tolist())

    tickers = tickers or list(set(bonds_df['ticker'].values) | set(loans_df['ticker'].values))
    isins = set(bonds_df['isin_number'].values)
    cds_df = cds_df[cds_df['ticker'].isin(tickers) | cds_df['isin'].isin(isins)]
    cds_df = pd.merge(cds_df,
                      bonds_df[['isin_number', 'ticker']].drop_duplicates().rename(columns={'ticker': 'ticker2'}),
                      left_on='isin', right_on='isin_number', how='left')
    cds_df['ticker'] = cds_df['ticker2'].combine_first(cds_df['ticker'])
    cds_df = cds_df.drop(columns=['isin', 'isin_number', 'ticker2']).drop_duplicates()

    debt_df = pd.concat([bonds_df.rename(columns=BOND_COL_MAP),
                         loans_df.rename(columns=LOAN_COL_MAP),
                         cds_df.rename(columns=CDS_COL_MAP)], ignore_index=True)

    debt_df = debt_df.dropna(subset=['asof_date'])
    debt_df = pd.merge(debt_df, issuer_df.drop_duplicates(subset=['company_corp_ticker']),
                       how='left', left_on='ticker', right_on='company_corp_ticker', suffixes=('', '_issuer'))

    debt_df['maturity'] = pd.to_datetime(debt_df['maturity'], format='mixed')
    debt_df = debt_df.sort_values('maturity').reset_index(drop=True)
    debt_df['maturity'] = pd.to_datetime(debt_df['maturity'], errors='coerce').dt.date

    debt_df = debt_df[[x for x in SELECT_COLUMNS if x in debt_df.columns]]

    if merge_dcs_fs:
        debt_df = merge_factset_equity_tickers(debt_df)
        debt_df = merge_factset_equity_returns(debt_df)

    debt_df = adjust_fx_amt_fields(debt_df, spot_fx_df)
    debt_df = adjust_fx_oas(asof_date, asof_date, debt_df)
    debt_df['current_coupon'] = pd.to_numeric(debt_df['current_coupon'], errors='coerce').round(2)
    debt_df['asof_date'] = asof_date

    debt_df = map_columns_debt_df(asof_date, debt_df)

    if build_issuer_curves:
        spread_col = 'pecs_rr=0.4_usd' if calc_pecs_flag else 'oas'
        debt_df = add_issuer_curve_implied_spreads(debt_df, spread_col=spread_col, use_spark=use_spark)

    ref_returns = get_index_period_returns([BOND_REF_INDEX, LOAN_REF_INDEX], asof_date)
    instrument_type_ref_index_map = {'Bond': BOND_REF_INDEX, 'Loan': LOAN_REF_INDEX}
    debt_df['index_ticker'] = debt_df['instrument_type'].map(lambda x: instrument_type_ref_index_map.get(x, None))

    if merge_dcs_fs:
        debt_df = merge_factset_dcs(debt_df, spot_fx_df)
        debt_df = merge_factset_financials(debt_df)
        debt_df = merge_findox_financials(debt_df)
        debt_df = adjust_fx_financials(debt_df, spot_fx_df)
        debt_df = merge_factset_estimates(debt_df)

    non_cds_rows = (debt_df['instrument_type'] != 'CDS')
    debt_df.loc[non_cds_rows, 'issuer_amt_outstanding_usd'] = \
        debt_df[non_cds_rows].groupby('ticker')['amt_outstanding_usd'].transform(sum)

    debt_df['issuer_amt_outstanding_usd'] = debt_df.groupby('ticker')['issuer_amt_outstanding_usd'].transform(min)

    debt_df = add_explain_returns(debt_df, ref_returns,
                                  ['dtd_return', '1w_return', 'mtd_return', '1m_return', 'qtd_return', 'ytd_return', ])

    if merge_restrictions:
        debt_df = merge_restriction_df(debt_df)

    debt_df['maturity_years'] = (pd.to_datetime(debt_df['maturity'], errors='coerce') -
                                 pd.to_datetime(asof_date)).dt.days / 365
    debt_df['maturity_years'] = debt_df['maturity_years'].round(2)
    debt_df['maturity_bucket'] = creditutil.map_maturity_bucket(debt_df['maturity_years'])

    debt_df['secured_unsecured'] = debt_df['secured_unsecured'].fillna('UNSEC')
    debt_df = merge_georev(debt_df)
    debt_df = merge_markit_bond_pricing(debt_df)
    debt_df = _merge_industry_by_ticker(debt_df)
    debt_df = fill_ticker_level_details(debt_df)
    return debt_df


if __name__ == '__main__':
    from common.utils import envsetup

    envsetup.init_app_using_parser()
    df = get_debt_df('2025-05-28', calc_pecs_flag=False, build_issuer_curves=True, use_spark=False, merge_dcs_fs=True)
    # df = get_debt_df('2024-06-21', calc_pecs_flag=False, build_issuer_curves=True, use_spark=False, merge_dcs_fs=True)
    logging.info(df)
