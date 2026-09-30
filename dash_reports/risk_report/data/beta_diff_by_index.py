import pandas as pd

from common.utils.logutil import *
from dash_reports.report_common.data.config import *
from dash_reports.report_common.data.data_model import datastore


def generate_beta_diff():
    raw_df = datastore.get_beta_diff()

    """Pre-processing of data"""
    raw_df = raw_df.loc[raw_df['Fund Entity Code'].isin(BETA_DIFF_RULES['funds'])]
    raw_df = raw_df.loc[~raw_df['Security Type'].isin(BETA_DIFF_RULES['rules']['Security Type'])]

    """Creating Beta Diff for all funds except TRF"""
    oth_funds_beta_diff = raw_df.loc[~raw_df['Fund Entity Code'].isin(['TRF'])]
    oth_funds_beta_diff = pd.pivot_table(oth_funds_beta_diff,
                                         index=BETA_DIFF_RULES['other_funds']['pivot_fields']['idx'],
                                         values=BETA_DIFF_RULES['other_funds']['pivot_fields']['vals'],
                                         aggfunc=BETA_DIFF_RULES['other_funds']['pivot_fields']['aggregation'])

    """Aggregating at fund level"""
    oth_beta_diff_total = oth_funds_beta_diff.groupby('Fund Entity Code').agg(
        BETA_DIFF_RULES['other_funds']['pivot_fields']['aggregation'])

    oth_beta_diff_total.index = pd.MultiIndex.from_product([oth_beta_diff_total.index, ['Total']])
    oth_beta_diff_total.index.names = BETA_DIFF_RULES['other_funds']['pivot_fields']['idx']

    oth_beta_dff = pd.DataFrame()
    idx = pd.IndexSlice

    for fund in BETA_DIFF_RULES['funds']:
        if fund != 'TRF':
            temp_total = oth_beta_diff_total.loc[oth_beta_diff_total.index.isin([fund], level='Fund Entity Code'), :]
            temp_df = oth_funds_beta_diff.loc[oth_funds_beta_diff.index.isin([fund], level='Fund Entity Code'), :]
            oth_beta_dff = pd.concat([oth_beta_dff, temp_df, temp_total], sort=False)
            del temp_total, temp_df

    """Creating Beta Diff for only TRF Funds"""
    trf_funds_beta_diff = raw_df.loc[raw_df['Fund Entity Code'].isin(['TRF'])]
    trf_funds_beta_diff = pd.pivot_table(trf_funds_beta_diff, index=BETA_DIFF_RULES['TRF']['pivot_fields']['idx'],
                                         values=BETA_DIFF_RULES['TRF']['pivot_fields']['vals'],
                                         aggfunc=BETA_DIFF_RULES['TRF']['pivot_fields']['aggregation'])

    """Aggregating at fund level for TRF"""
    trf_beta_diff_total = trf_funds_beta_diff.groupby('Fund Entity Code').agg(
        BETA_DIFF_RULES['other_funds']['pivot_fields']['aggregation'])

    trf_beta_diff_total.index = pd.MultiIndex.from_product([trf_beta_diff_total.index, [''], [''], ['Total']])
    trf_beta_diff_total.index.names = BETA_DIFF_RULES['TRF']['pivot_fields']['idx']
    trf_funds_beta_diff = pd.concat([trf_funds_beta_diff, trf_beta_diff_total], sort=False)

    """Rename Columns"""
    trf_funds_beta_diff = trf_funds_beta_diff.rename(columns=BETA_DIFF_RULES['col_rename'])
    oth_beta_dff = oth_beta_dff.rename(columns=BETA_DIFF_RULES['col_rename'])

    """Formatting for Dash"""
    oth_beta_dff = oth_beta_dff.reset_index()

    trf_funds_beta_diff = trf_funds_beta_diff.reset_index()

    if SAVE_OUTPUT:
        trf_funds_beta_diff.to_csv(os.path.join(OUTPUT_PATH, 'trf_beta_diff.csv'))
        oth_beta_dff.to_csv(os.path.join(OUTPUT_PATH, 'oth_beta_diff.csv'))

    return trf_funds_beta_diff, oth_beta_dff


if __name__ == '__main__':
    generate_beta_diff()
