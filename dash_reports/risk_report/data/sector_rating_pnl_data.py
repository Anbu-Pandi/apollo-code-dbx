import pandas as pd

from common.utils.logutil import *
from dash_reports.report_common.data.config import *
from dash_reports.report_common.data.data_model import datastore


def summarize_attribution(raw_risk_df, rolling_df, rules_dict):
    """Calculating Unlev YTW"""
    risk_unlev_ytw_df = raw_risk_df.loc[:, rules_dict['unlev_ytw_cols']]
    risk_unlev_ytw_df['Unlev YTW'] = risk_unlev_ytw_df['Fund Wght'] * risk_unlev_ytw_df['Yield to Worst']

    rqrd_cols = list(set(risk_unlev_ytw_df.columns) - {'Fund Wght', 'Yield to Worst'})

    risk_unlev_ytw_df = risk_unlev_ytw_df.loc[:, rqrd_cols].groupby(rules_dict['group_by']).sum().T

    risk_unlev_ytw_df = risk_unlev_ytw_df.stack('Fund Entity Code')
    risk_unlev_ytw_df.index = risk_unlev_ytw_df.index.swaplevel()

    risk_unlev_ytw_total = risk_unlev_ytw_df.sum(axis=1)
    risk_unlev_ytw_total.name = 'Total'

    risk_unlev_ytw_df = pd.concat([risk_unlev_ytw_total, risk_unlev_ytw_df], axis=1)
    risk_unlev_ytw_df = risk_unlev_ytw_df.mul(100)

    """Calculating MVE, Gross MVE & % NAV"""
    risk_filtered_df = raw_risk_df.loc[
        ~ raw_risk_df['Issuer Ult'].isin(rules_dict['risk_enhanced_file']['rules']['Issuer Ult'])]

    risk_filtered_df = risk_filtered_df.loc[:, rules_dict['risk_enhanced_file']['cols']]
    risk_filtered_df = risk_filtered_df.groupby(rules_dict['group_by']).sum()

    risk_filtered_df['MV Equivalent USD'] = risk_filtered_df['MV Equivalent USD'].div(1000000)
    risk_filtered_df['Gross MV Equiv USD'] = risk_filtered_df['Gross MV Equiv USD'].div(1000000)
    risk_filtered_df['Fund Wght'] = risk_filtered_df['Fund Wght'].mul(100)

    risk_filtered_df = risk_filtered_df.T
    risk_filtered_df = risk_filtered_df.stack('Fund Entity Code')
    risk_filtered_df.index = risk_filtered_df.index.swaplevel()

    risk_filtered_total = risk_filtered_df.sum(axis=1)
    risk_filtered_total.name = 'Total'
    risk_filtered_df = pd.concat([risk_filtered_total, risk_filtered_df], axis=1)

    """Calculating 1 week Rolling, MTD, QTD & YTD Pnl"""
    roll_filtered_df = rolling_df.loc[:, rules_dict['rolling_data']['cols']]
    roll_filtered_df = roll_filtered_df.groupby(rules_dict['group_by']).sum().T

    roll_filtered_df = roll_filtered_df.stack('Fund Entity Code')
    roll_filtered_df.index = roll_filtered_df.index.swaplevel()

    roll_filtered_total = roll_filtered_df.sum(axis=1)
    roll_filtered_total.name = 'Total'

    roll_filtered_df = pd.concat([roll_filtered_total, roll_filtered_df], axis=1)
    roll_filtered_df = roll_filtered_df.div(1000000).round(1)

    sector_rating_attrib = pd.concat([risk_filtered_df, risk_unlev_ytw_df, roll_filtered_df], sort=False)
    sector_rating_attrib.sort_index(level=['Fund Entity Code'], inplace=True)

    sector_rating_attrib = sector_rating_attrib.reindex(
        ['MV Equivalent USD', 'Gross MV Equiv USD', 'Fund Wght', 'Unlev YTW', '1 Week Rolling PnL', 'MTD PnL',
         'QTD PnL', 'YTD PnL'], level=1)

    return sector_rating_attrib


def generate_sector_attribution():
    raw_risk_df = datastore.get_cur_risk()
    raw_roll_df = datastore.get_roll_sector()

    """Pre-processing of data"""
    raw_risk_df = raw_risk_df.loc[raw_risk_df['Fund Entity Code'].isin(FUND_CODE_LIST)]
    raw_risk_df = raw_risk_df.loc[
        ~ raw_risk_df['Strategy Desc'].isin(SECTOR_ATTRIB_RULES['risk_enhanced_file']['rules']['Strategy Desc'])]
    raw_risk_df = raw_risk_df.loc[
        raw_risk_df['Is Liability'].isin(SECTOR_ATTRIB_RULES['risk_enhanced_file']['rules']['Is Liability'])]

    raw_roll_df = raw_roll_df.loc[raw_roll_df['Fund Entity Code'].isin(FUND_CODE_LIST)]

    attrib_by_sector = summarize_attribution(raw_risk_df, raw_roll_df, SECTOR_ATTRIB_RULES)
    attrib_by_sector = attrib_by_sector.rename(columns=SECTOR_MAP)

    master_df = pd.DataFrame()
    for key, value in SECTOR_CYCLE_MAP.items():
        value = list(filter(lambda x: x in attrib_by_sector.columns, value))
        if len(value) > 0:
            temp_df = attrib_by_sector.loc[:, value]
            temp_df.columns = pd.MultiIndex.from_product([[key], value])
            master_df = pd.concat([master_df, temp_df], axis=1, sort=False)
            del temp_df

    """Formatting for Dash"""
    master_df = master_df.reset_index()

    if SAVE_OUTPUT:
        master_df.to_csv(os.path.join(OUTPUT_PATH, 'sector_by_rating.csv'))

    return master_df


def generate_rating_attribution():
    raw_risk_df = datastore.get_cur_risk()
    raw_roll_df = datastore.get_roll_rating()

    """Pre-processing of data"""
    raw_risk_df = raw_risk_df.loc[raw_risk_df['Fund Entity Code'].isin(FUND_CODE_LIST)]

    raw_risk_df = raw_risk_df.loc[
        ~ raw_risk_df['Strategy Desc'].isin(RATING_ATTRIB_RULES['risk_enhanced_file']['rules']['Strategy Desc'])]

    raw_risk_df = raw_risk_df.loc[
        raw_risk_df['Is Liability'].isin(RATING_ATTRIB_RULES['risk_enhanced_file']['rules']['Is Liability'])]

    raw_risk_df['Rating'] = raw_risk_df['Final Rating'].apply(
        lambda x: RATINGS_MAP[x] if x in RATINGS_MAP.keys() else 'OTH')

    raw_roll_df = raw_roll_df.loc[raw_roll_df['Fund Entity Code'].isin(FUND_CODE_LIST)]
    raw_roll_df['Rating'] = raw_roll_df['Final Rating'].apply(
        lambda x: RATINGS_MAP[x] if x in RATINGS_MAP.keys() else 'OTH')

    attrib_by_rating = summarize_attribution(raw_risk_df, raw_roll_df, RATING_ATTRIB_RULES)

    master_df = pd.DataFrame()
    for key, value in IG_HY_MAP.items():
        value = list(set(value).intersection(set(attrib_by_rating.columns.tolist())))
        temp_df = attrib_by_rating.loc[:, value]
        temp_df.columns = pd.MultiIndex.from_product([[key], value])
        master_df = pd.concat([master_df, temp_df], axis=1, sort=False)
        del temp_df

    """Formatting for Dash"""
    master_df = master_df.reset_index()

    if SAVE_OUTPUT:
        master_df.to_csv(os.path.join(OUTPUT_PATH, 'attrib_by_rating.csv'))

    return master_df


if __name__ == '__main__':
    a = generate_rating_attribution()
