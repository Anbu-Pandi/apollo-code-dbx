import pandas as pd

from data_access.dbtables import MARKIT_BOND_PRICING_APP
from data_access.utils import chunk_query


def get_markit_bond_prices(dt, isins):
    sql = '''
    select 
        asof_date, isin, mid_price, 
		CAST(coalesce(mid_oas, mid_z_spread) AS float) as mid_oas, 
		CAST(coalesce(mid_ytw, mid_ytm) AS float) as mid_ytw, 
		mid_ytm,
        CAST(coalesce(mid_effective_duration, mid_modified_duration) AS float) as mid_effective_duration,
        CAST(coalesce(mid_effective_duration, mid_modified_duration) AS float) as mid_spread_duration,
        CAST(actual_cds_bond_basis_mid AS float) AS actual_cds_bond_basis_mid, 
        liquidity_score
    from 
        dbo.markit_pricing_data
    where asof_date='{0}'
    and isin in {1} 
    '''
    return chunk_query(sql.format(dt, '{}'), isins, app=MARKIT_BOND_PRICING_APP)


def merge_markit_bond_pricing(debt_df):
    isins = debt_df[(debt_df['instrument_type'].str.contains('Bond'))]['instrument_id'].unique().tolist()
    markit_bond_df = get_markit_bond_prices(pd.to_datetime(debt_df['asof_date'],
                                                           format='mixed', errors='coerce').max().date(),
                                            isins)
    markit_bond_df = markit_bond_df.rename(columns={'isin': 'instrument_id',
                                                    'mid_price': 'price', 'mid_oas': 'oas',
                                                    'mid_ytw': 'yield_to_worst_conv',
                                                    'mid_ytm': 'yield_to_maturity_conv',
                                                    'mid_spread_duration': 'spread_duration',
                                                    'mid_effective_duration': 'effective_duration'})

    debt_df = pd.merge(debt_df, markit_bond_df.drop(columns=['asof_date']), how='left', on=['instrument_id'],
                       suffixes=('_ice', '_markit'))

    for col in debt_df.columns:
        if col.endswith('_markit'):
            col = col.replace('_markit', '')
            debt_df[col] = debt_df[f'{col}_markit'].combine_first(debt_df[f'{col}_ice'])
    return debt_df


if __name__ == '__main__':
    import logging
    from common.utils.logutil import setuplogger

    setuplogger()

    from apo_config.config import config

    config.init('prod')

    _df = pd.read_clipboard()
    _df['asof_date'] = pd.to_datetime(_df['asof_date'])
    df = merge_markit_bond_pricing(_df)

    logging.info(df)
