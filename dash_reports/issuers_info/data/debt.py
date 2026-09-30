import datetime
import pandas as pd
from dash_reports.report_common.elastic_search.api import default_es
from dash_reports.issuers_info.data.caching import ES_BBG_BONDS_INDEX, ES_WSO_LOANS_INDEX, ES_MARKIT_CDS_INDEX

from common.utils import dtutil


def get_bonds(bbg_ids):
    df = default_es.search_exact(ES_BBG_BONDS_INDEX, 'id_bb_company', bbg_ids)
    if len(df) > 0:
        df = df[df['id_bb_company'].isin(bbg_ids)]
        tday = datetime.datetime.now()
        df['maturity'] = pd.to_datetime(df['maturity'], errors='coerce')
        df['called_dt'] = pd.to_datetime(df['called_dt'], errors='coerce')
        active_bonds = (df['called'] != 1) & (df['defaulted'] != 1) & (df['update_type'] != -1) \
                       & (df['maturity'].fillna(tday) >= tday) \
                       & (df['called_dt'].fillna(tday) >= tday)
        df = df.loc[active_bonds, :]

        rank_dict = {'gmtn': 1, 'emtn': 2, 'mtn': 3, 'regs': 4, '144a': 5}
        non_map_rank = max(rank_dict.values()) + 1
        df['rank'] = df['series'].map(lambda x: rank_dict.get(str(x).lower(), non_map_rank))
        df = df.sort_values(['security_des', 'announce_dt', 'rank', 'knowledge_date'])

        amt_df = df[['security_des', 'amt_issued', 'amt_outstanding']] \
            .groupby('security_des') \
            .apply(consolidate_bonds)
        df = df.drop_duplicates(subset=['security_des'], keep='last')
        if len(amt_df) > 0:
            amt_df = pd.DataFrame(amt_df.tolist(), columns=['amt_issued', 'amt_outstanding'],
                                  index=amt_df.index).reset_index()
            df = df.drop(columns=['amt_issued', 'amt_outstanding'])
            df = pd.merge(df, amt_df, how='left', on='security_des')

    return df


def get_loans(bbg_ids):
    df = default_es.search_exact(ES_WSO_LOANS_INDEX, 'id_bb_company', bbg_ids)
    df.drop_duplicates(subset=['wso_data_facility_id'])
    if len(df) > 0:
        tday = datetime.datetime.now()
        active_loans = pd.to_datetime(df['maturity_date'], errors='coerce').fillna(tday) >= tday
        df = df.loc[active_loans, :]
    return df


def get_cds(bbg_ids):
    df = default_es.search_exact(ES_MARKIT_CDS_INDEX, 'id_bb_company', bbg_ids)
    return df


def consolidate_bonds(bond_sub_df: pd.DataFrame) -> pd.DataFrame:
    amt_issued = bond_sub_df['amt_issued'].max()
    amt_outstanding = bond_sub_df['amt_outstanding'].unique().sum()
    return (amt_issued, amt_outstanding)


def get_sec_count_df(debt_df):
    if len(debt_df) > 0:
        return debt_df.groupby('id_bb_company')['instrument_id'].count().reset_index().rename(
            columns={'instrument_id': 'security_count'})
    return pd.DataFrame()


def bond_rename_cols(df):
    cols = ['id_bb_company', 'id_bb_global_company_name', 'business_structure', 'id_isin', 'ticker', 'cpn', 'maturity',
            'collat_typ', 'crncy', 'amt_issued', 'amt_outstanding', ]
    if len(df) > 0:
        df['maturity'] = pd.to_datetime(df['maturity'], errors='coerce').dt.date
        df['cpn'] = df['cpn'].round(4)
        df = df[[c for c in cols if c in df.columns]].sort_values('maturity').assign(debt_type='Bond')

    return df


def loan_rename_cols(df):
    cols = ['id_bb_company', 'id_bb_global_company_name', 'instrument_id', 'facility_type', 'facility_name',
            'markit_ticker',
            'cpn', 'maturity_date',
            'seniority', 'lien_type', 'facility_curr', 'original_commitment', 'current_outstanding']
    if len(df) > 0:
        df['stated_spread'] = df['stated_spread'].mul(10000).round(4)
        df['cpn'] = df['stated_spread_type'] + '+' + df['stated_spread'].astype(str)
        df['instrument_id'] = df['loanx_id'].combine_first(df['isin']).combine_first(df['cusip'])
        df['maturity_date'] = pd.to_datetime(df['maturity_date'], errors='coerce').dt.date
        df = df[cols].sort_values('maturity_date').assign(debt_type='Loan')

    return df


def cds_rename_cols(df):
    cols = ['id_bb_company', 'id_bb_global_company_name', 'instrument_id', 'tier',
            'ticker', 'ccy',
            'cpn', 'maturity_date',
            'wkly_net_notional', 'wkly_gross_notional']
    if len(df) > 0:
        df['cpn'] = df['running_coupon'].mul(100).round(4)
        df['instrument_id'] = df['red_code']
        df['maturity_date'] = df[['asof_date', 'tenor']].apply(lambda x: dtutil.cdsdate(x['asof_date'], x['tenor']),
                                                               axis=1)
        df = df[cols].sort_values('maturity_date').assign(debt_type='CDS')

    return df


def get_debt_df(sibling_ids):
    bonds_df = get_bonds(sibling_ids)
    loans_df = get_loans(sibling_ids)
    cds_df = get_cds(sibling_ids)

    bonds_df = bond_rename_cols(bonds_df)
    loans_df = loan_rename_cols(loans_df)
    cds_df = cds_rename_cols(cds_df)

    col_map = {'markit_ticker': 'ticker', 'maturity_date': 'maturity', 'id_isin': 'instrument_id',
               'collat_typ': 'seniority', 'crncy': 'currency', 'facility_curr': 'currency', 'ccy': 'currency',
               'tier': 'seniority',
               'original_commitment': 'amt_issued',
               'current_outstanding': 'amt_outstanding',
               'wkly_net_notional': 'amt_outstanding',
               'wkly_gross_notional': 'amt_issued',
               }
    debt_df = pd.DataFrame()
    if len(bonds_df) > 0 or len(loans_df) > 0:
        debt_df = pd.concat([bonds_df.rename(columns=col_map),
                             loans_df.rename(columns=col_map),
                             cds_df.rename(columns=col_map),
                             ]).sort_values(
            'maturity')
    cols = ['debt_type']
    if len(debt_df) > 0:
        debt_df = debt_df[cols + [x for x in debt_df.columns if x not in cols]]
    return debt_df
