import dash_html_components as html
import pandas as pd

from dash_reports.analyst_questionnaire.data.issuers import get_issuer_coverage_with_questionnaire
from dash_reports.report_common.utils import components, dfhelpers

ISSUERS_TABLE_ID = 'issuer-table-id'

DISPLAY_COLS = ['Id Bb Company', 'Company Corp Ticker', 'Id Bb Global Company Name', 'Link',
                'Mapped Id Bb Global Company Name', 'Mapped Company Status', 'Business Structure', 'Region',
                'Ice Index Ticker', 'Isin List', 'Amt Outstanding Usd', 'Gross Volume 3M',
                'Industry Lvl2 Desc', 'Industry Lvl3 Desc', 'Industry Lvl4 Desc', 'Apo Industry', 'Apo Lead', 'Rating',
                'Last Updated By', 'Last Updated', '% Complete',
                'Acquisition Side', 'Acquisition Type', 'Gross Debt Chg Est', 'Shares Out Chg Est',
                'Div Paid Chg Est', 'Div Paid Chg Est Million', 'Ig Target Lev Ratio Yn', 'Ig Target Lev Ratio',
                'Ownership Technicals', 'Ratings Mom Score', 'Ratings Convinction']

SORT_COLS = ['Mapped Id Bb Global Company Name']

RENAME_COLS = {
    'Id Bb Company': 'Issuer Id Bb Company',
    'Id Bb Global Company Name': 'Issuer Bb Global Company Name',
    'Mapped Id Bb Global Company Name': 'Bb Global Company Name',
    'Mapped Company Status': 'Company Status'}


def create_issuers_layout():
    return html.Div(className='page',
                    children=[
                        html.Div(id=ISSUERS_TABLE_ID,
                                 children=[html.Div(children=[html.H6("Issuer Coverage:", className='subtitle'),
                                                              html.Div(children=[create_issuers_table()],
                                                                       )],
                                                    className='fund_filter')]
                                 )])


def get_issuers_df():
    df = get_issuer_coverage_with_questionnaire()
    df['last_updated'].fillna(pd.to_datetime('1900-01-01'), inplace=True)
    df.sort_values(['last_updated', 'id_bb_global_company_name'], inplace=True)
    df['link'] = df['id_bb_company'].map(
        lambda x: f"[{int(x)}](./company-info?id_bb_company={int(x)})" if not (pd.isnull(x) or x == '') else '')
    df.columns = [dfhelpers.ui_column_name(x) for x in df.columns]
    df = df[[x for x in DISPLAY_COLS if x in df.columns]].sort_values(SORT_COLS).rename(columns=RENAME_COLS)
    return df


def create_issuers_table():
    df = get_issuers_df()
    return components.make_dash_table(df, link_columns=[dfhelpers.ui_column_name('link')],
                                      fixed_columns=3,
                                      page_size=10)
