import pandas as pd

from common.utils import dtutil
from common.utils import sqlutil
from dash_reports.issuer_debt_in_index_movers.data import utils
from dash_reports.issuer_debt_in_index_movers.data.sql_const import LOAN_OAS_PCTILE_SQL, LOAN_RISK_SQL
from data_access.beta_attribution import security_ts_hist
from data_access.beta_attribution.utils import tosqllist
from data_access.dbtables import JPM_LL_APP, MARKIT_LOAN_PRICING_APP, LOAN_REFERENCEDATA_APP
from data_access.dbtables.index_data import sp_loan_index
import logging

LOAN_INDEX_TICKERS = ['spbdal']

LOAN_REF_INDEX = 'iboxx_usloan_trs'


def get_loanx_from_pricingids(pricing_ids):
    wso_loans_sql = f"""
    select 
    wli.name as issuer_name, wli3.value as loanx, wli4.value as bbg_id,wli2.value as lin, wlf.wso_data_facility_id as facility_id, wlf.name as facility_name,
    wlf.maturity_date, wlf.original_commitment, wlf.current_commitment, wlf.current_outstanding
    from wso_loan_issuers wli 
    join wso_loan_bank_deal wlbd on wli.wso_data_issuer_id = wlbd.wso_data_issuer_id 
    join wso_loan_facility wlf on wlbd.wso_data_bank_deal_id = wlf.wso_data_bank_deal_id 
    join wso_loan_identifiers wli2 on wlf.wso_data_facility_id = wli2.wso_data_facility_id and wli2.type='LIN'
    join wso_loan_identifiers wli3 on wlf.wso_data_facility_id = wli3.wso_data_facility_id and wli3.type='LoanX'
    left join wso_loan_identifiers wli4 on wlf.wso_data_facility_id = wli4.wso_data_facility_id and wli4.type='FIGI'
    where 
    wli2.value in {tosqllist(pricing_ids)}
    """
    wso_loans = sqlutil.readdfsql(wso_loans_sql, app=LOAN_REFERENCEDATA_APP)
    wso_loans = wso_loans.loc[:, ~wso_loans.columns.duplicated()]
    wso_loans = wso_loans.drop_duplicates()
    return wso_loans[['loanx', 'lin', 'bbg_id']].drop_duplicates(subset=['lin']).set_index('lin')


def get_price_from_loanx(dt, loanx_ids):
    markit_pricing_sql = '''
    select mark_date,loanx_id, evaluated_price  
    from dbo.markit_pricing_data_loan
    where mark_date between '{0}' and '{1}'
    and loanx_id in {2}
    '''
    markit_pricing_sql = markit_pricing_sql.format(dtutil.dateadd(dt, '-1m'), dtutil.todate(dt), tosqllist(loanx_ids))
    loan_prices = sqlutil.readdfsql(markit_pricing_sql, app=MARKIT_LOAN_PRICING_APP)

    loan_prices = pd.merge(loan_prices,
                           loan_prices.groupby('loanx_id')['mark_date'].max().reset_index(),
                           on=['loanx_id', 'mark_date'], how='inner')
    return loan_prices.set_index('loanx_id')['evaluated_price']


def get_loanindex_data(refdt, history_tenor):
    dates = {'from_yyyymmdd': dtutil.todatestr(dtutil.dateadd(refdt, f"-{history_tenor}")),
             'to_yyyymmdd': dtutil.todatestr(refdt)}
    index_df = sp_loan_index.get_constituents(dates).sort_values('asof_date')

    index_df['industry'] = index_df['narrow_industry']
    index_df['maturity'] = index_df['maturity_date']
    index_df['amt_outstanding'] = index_df['current_outstanding'] / 1e6
    index_df['coupon'] = index_df['libor_spread'].map(lambda x: f'L+{(x * 100):.2f}')
    index_df['total_return'] = index_df['total_return'] / index_df['weights']

    loanxids = get_loanx_from_pricingids(index_df['pricing_id'].unique().tolist())
    index_df['instrument_id'] = index_df['pricing_id'].map(loanxids['loanx'])
    index_df['bbg_id'] = index_df['pricing_id'].map(loanxids['bbg_id'])
    index_df['rating'] = index_df['current_rating']

    prices = get_price_from_loanx(refdt, index_df['instrument_id'].unique().tolist())
    index_df['price'] = index_df['instrument_id'].map(prices)

    index_df['tenor'] = utils.vectorized_calc_tenor(pd.to_datetime(index_df['maturity']).dt.date,
                                                    pd.to_datetime(index_df['asof_date']).dt.date)

    index_df = utils.update_df_with_wts_rets(index_df, 'asof_date', 'pricing_id', 'price', 'weights', 'total_return')

    return index_df


def get_reference_index_returns(dt, history_tenor):
    loantrs_level = utils.get_idx_level(LOAN_REF_INDEX,
                                        dtutil.dateadd(dt, f'-{history_tenor}-1b'), dt)
    loantrs_return_series = loantrs_level / loantrs_level.shift(-1) - 1
    return loantrs_return_series


def get_loan_beta_multiplier(dt):
    df = security_ts_hist.get_timeseries(dtutil.dateadd(dt, f'-{int(365 * 1.5)}d'),
                                         dt,
                                         LOAN_REF_INDEX,
                                         'BETA_WITH_ALPHA_12M_CDXHY',
                                         'WEEKLY',
                                         'JPM')
    return df['VALUE'].mean()


def add_loan_betas(index_df, loan_history, loantrs_return_series):
    if 'beta' in index_df.columns:
        return index_df
    loan_history['asof_date'] = pd.to_datetime(loan_history['asof_date']).dt.date
    loan_beta_mult = get_loan_beta_multiplier(loan_history['asof_date'].max())
    loan_betas = loan_history.sort_values('asof_date', ascending=False) \
        .groupby('instrument_id')[['asof_date', 'total_return']] \
        .apply(lambda x: utils.beta_with_index(x, loantrs_return_series.dropna()) * loan_beta_mult)
    df = pd.merge(index_df, loan_betas.reset_index()[['instrument_id', 'beta']].dropna(), on='instrument_id',
                  how='left')
    df = df[index_df.columns.tolist() + ['beta']]

    return df


def add_oas_percentiles(df):
    dt = df['asof_date'].max()
    sql = LOAN_OAS_PCTILE_SQL.format(dtutil.dateadd(dt, '-1y'), dt)
    oas_df = sqlutil.readdfsql(sql, app=JPM_LL_APP)
    df = pd.merge(df, oas_df.drop(columns=['oas', 'asof_date']), left_on='instrument_id', right_on='loanx', how='left') \
        .drop(columns=['loanx'])
    return df


def add_loan_risk(df):
    dt = df['asof_date'].max()
    sql = LOAN_RISK_SQL.format(dt)
    risk_df = sqlutil.readdfsql(sql, app=JPM_LL_APP)
    risk_df.rename(columns={'fl_mat_dm': 'oas',
                            'fw_mat_dm': 'oas w curve',
                            'fl_3y_dm': 'oas 3y',
                            'fw_3y_dm': 'oas 3y w curve',
                            'fl_mat_yld': 'yield_to_maturity_sa',
                            'fw_mat_yld': 'yield_to_maturity w curve',
                            'fl_3y_yld': 'yield 3y',
                            'fw_3y_yld': 'yield 3y w curve', }, inplace=True)
    df = pd.merge(df.drop(columns=['price']), risk_df, left_on='instrument_id', right_on='loanx', how='left') \
        .drop(columns=['loanx'])
    return df


def get_index_df_with_betas(dt):
    loanhistory = get_loanindex_data(dt, '1y')
    referenceindexdf = get_reference_index_returns(dt, '1y')

    indexdf = loanhistory[pd.to_datetime(loanhistory['asof_date']).dt.date == dtutil.todate(dt)]
    indexdf = add_loan_betas(indexdf, loanhistory, referenceindexdf)
    indexdf = utils.add_estimated_beta(indexdf, "beta ~ industry + rating + tenor")
    indexdf = add_oas_percentiles(indexdf)
    indexdf = add_loan_risk(indexdf)
    return indexdf


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()

    from apo_config.config import config

    config.init('prod')