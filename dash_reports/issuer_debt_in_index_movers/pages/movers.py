from dash import Input, Output, State, callback, dcc, html
import numpy as np
import pandas as pd

from common.utils import dtutil
from dash_reports.issuer_debt_in_index_movers.data.loan_index import LOAN_INDEX_TICKERS
from dash_reports.issuer_debt_in_index_movers.data.benchmark_info import get_index_desc
from dash_reports.report_common.data.data_model import datastore
from dash_reports.report_common.pages.link_and_headers import get_page_title
from dash_reports.report_common.utils.components import make_dash_table
from dash_reports.risk_report.pages.utils import get_datepicker_control
from dash_reports.report_common.pages.pivot_page_helpers import create_dropdown
from dash_reports.report_common.utils import combos
from dash_reports.report_common.pages import ui_config
from dash_reports.report_common.utils import dfhelpers

INDEX_RESULT_CONTROL_ID = 'movers-result-control'
INDEX_DT_CONTROL_ID = 'movers-dt-control'
INDEX_VALUE_CONTROL_ID = 'movers-value-control'
INDEX_SUBMIT_CONTROL_ID = 'movers-submit-control'

INDEX_LIST = ['H0A0', 'C0A0', 'SPBDAL',
              'C0A1', 'C0A2', 'C0A3', 'C0A4', 'C0AC', 'C0J0', 'C0J1', 'C0J2', 'C0J3', 'C0J4',
              'CB10', 'CB20', 'CB30', 'CB40', 'CF00', 'CFFS', 'CFIE', 'CMBS', 'COCO', 'DGOV', 'DXEM', 'EB00', 'EB20',
              'EB30', 'EB40', 'EBSL', 'EBSS', 'EJ00', 'EMCB', 'EMCL', 'EMHB', 'EMIB', 'EMIC', 'ER00', 'ER20', 'ER30',
              'ER40', 'EV00', 'H0A1', 'H0A2', 'H0A3', 'H0A4', 'H42C', 'HE00', 'HEC0', 'HUC0', 'UF00', 'UI00', 'UR00',
              'GBMI', 'G0BN']

DISPLAY_COLUMNS_MAP = {
    'ticker': 'Ticker', 'issuer': 'Issuer', 'coupon': 'Coupon', 'maturity': 'Maturity',
    'industry': 'Industry', 'instrument_id': 'Instrument Id',
    'bbg_id': 'Bbg Id',
    'rating': 'Rating',
    'rating_bucket': 'Rating Bucket',
    'tenor': 'Maturity Years',
    'maturity_bucket': 'Maturity Bucket',
    'price': 'Price',
    'yield_to_worst_sa': 'Yield to Worst (%)',
    'yield 3y w curve': 'Yield 3y w Curve',
    'yield 3y': 'Yield 3y',
    'yield_to_maturity w curve': 'Yield to Maturity w Curve',
    'yield_to_maturity_sa': 'Yield to Maturity (%)',
    'oas 3y w curve': 'OAS 3y w Curve',
    'oas 3y': 'OAS 3y',
    'oas w curve': 'OAS w Curve',
    'oas': 'OAS bps',
    'oas_min': 'OAS 1y min', 'oas_max': 'OAS 1y max', 'oas_pctile': 'OAS 1y %-ile',
    'oas_industry': 'OAS Industry',
    'oas_maturity_bucket': 'OAS Maturity',
    'oas_rating_bucket': 'OAS Rating',
    'oas_industry_rating_bucket': 'OAS Industry / Rating',
    'oas_maturity_bucket_rating_bucket': 'OAS Maturity / Rating',
    'effective_duration': 'Effective Duration',
    'weights': 'Weights (%)',
    'amt_outstanding': 'Amount Outstanding (MM)',
    'volume_mtd': 'Volume MTD (MM)', 'gross_volume_mtd': 'Gross Volume MTD (MM)', 'etf_change': 'ETF MTD Change (MM)',
    'beta': 'Beta',
    'bond_point_return_dtd': 'Bond Points Return 1 day', 'bond_point_beta_return_dtd': 'Bond Points Beta Return 1 day',
    'bond_point_alpha_return_dtd': 'Bond Points Alpha Return 1 day',
    'bond_point_return_wtd': 'Bond Points Return WTD', 'bond_point_beta_return_wtd': 'Bond Points Beta Return WTD',
    'bond_point_alpha_return_wtd': 'Bond Points Alpha Return WTD',
    'bond_point_return_mtd': 'Bond Points Return MTD', 'bond_point_beta_return_mtd': 'Bond Points Beta Return MTD',
    'bond_point_alpha_return_mtd': 'Bond Points Alpha Return MTD',
    'bond_point_return_ytd': 'Bond Points Return YTD', 'bond_point_beta_return_ytd': 'Bond Points Beta Return YTD',
    'bond_point_alpha_return_ytd': 'Bond Points Alpha Return YTD',
    'oas_1_day_chg': 'OAS Change 1 day', 'oas_1_week_chg': 'OAS Change 1 week', 'oas_1_mo_chg': 'OAS Change 1 month',
    'oas_3_mo_chg': 'OAS Change 3 month',
    'oas_12_mo_chg': 'OAS Change 1 year', 'oas_mtd_change': 'OAS Change MTD', 'oas_qtd_chg': 'OAS Change QTD',
    'oas_ytd_chg': 'OAS Change YTD',
}


def _format_index(df):
    df = df[[k for k in DISPLAY_COLUMNS_MAP if k in df.columns]] \
        .rename(columns={k: v for k, v in DISPLAY_COLUMNS_MAP.items() if k in df.columns})

    df['Weights (%)'] = df['Weights (%)'] * 100
    if 'ETF MTD Change (MM)' in df.columns:
        df['ETF MTD Change (MM)'] = df['ETF MTD Change (MM)'] / 1e6
    if 'Maturity' in df.columns:
        df['Maturity'] = pd.to_datetime(df['Maturity']).dt.date
    if 'Maturity (Years)' in df.columns:
        df['Maturity (Years)'] = np.round(df['Maturity (Years)'], 0)
        df.drop(columns=['Maturity'], inplace=True)

    def _multi_index_column(x):
        retval = ('', x)
        tup = x.split('Return')
        if len(tup) > 1:
            retval = (f"Return {tup[1]}", tup[0])
        tup = x.split('OAS')
        if len(tup) > 1:
            retval = ('OAS', tup[1])
        tup = x.split('OAS Change')
        if len(tup) > 1:
            retval = ('OAS Change', tup[1])
        return retval

    df.rename(columns={x: _multi_index_column(x) for x in df.columns}, inplace=True)
    return df


def get_index_header():
    return html.Div([get_page_title("Credit Index Movers"),
                     html.Br(), ])


def _get_index_desc(index):
    key = 'credit_movers_index_desc'
    df = datastore.get_app_key(key)
    if df is None:
        df = get_index_desc()
        datastore.set_app_key(key, df)

    if index in df.index:
        return df.loc[index]

    return index


def create_layout(app, user_settings, dt=None):
    app.title = 'Credit Movers'
    dt = dt or dtutil.dateadd(dtutil.today(), '-1b')
    dd_values = {k: _get_index_desc(k) for k in INDEX_LIST}

    page_elements = []
    page_elements += [get_index_header()]
    index = ui_config.get_control_value(INDEX_VALUE_CONTROL_ID, user_settings, INDEX_LIST[0])
    issuer = ui_config.get_control_value('issuer', user_settings)
    industry = ui_config.get_control_value('industry', user_settings)
    instrument_id = ui_config.get_control_value('instrument_id', user_settings)
    page_elements += [html.Div(id="movers-input-controls",
                               children=[combos.get_labelled_component("Date: ",
                                                                       get_datepicker_control(INDEX_DT_CONTROL_ID, dt,
                                                                                              disabled=False)),
                                         combos.get_labelled_component("Index: ",
                                                                       create_dropdown(
                                                                           {'type': ui_config.SAVE_CONFIG_INPUT_TYPE,
                                                                            'index': INDEX_VALUE_CONTROL_ID},
                                                                           dd_values,
                                                                           index,
                                                                           multi=False, style=dict(width='40%'))),
                                         combos.get_labelled_component("Issuer: ",
                                                                       dcc.Input(
                                                                           id={'type': ui_config.SAVE_CONFIG_INPUT_TYPE,
                                                                               'index': 'issuer'},
                                                                           className='input', value=issuer)),
                                         combos.get_labelled_component("Industry: ",
                                                                       dcc.Input(
                                                                           id={'type': ui_config.SAVE_CONFIG_INPUT_TYPE,
                                                                               'index': 'industry'},
                                                                           className='input', value=industry)),
                                         combos.get_labelled_component("Instrument Id: ",
                                                                       dcc.Input(
                                                                           id={'type': ui_config.SAVE_CONFIG_INPUT_TYPE,
                                                                               'index': 'instrument_id'},
                                                                           className='input', value=instrument_id)),
                                         ],
                               className='fund_filter')]

    page_elements += [html.Div([html.Button('Refresh', id=INDEX_SUBMIT_CONTROL_ID, className='submit-button')],
                               className='fund_filter')]

    result_elements = create_result_layout_for_index_dt(dt, index, issuer, industry, instrument_id)
    page_elements += [html.Div(id=INDEX_RESULT_CONTROL_ID, children=result_elements, className='page')]
    return html.Div(children=page_elements)


def _get_additional_data(dt, index):
    trades = None
    missing_msg = ''
    etf_change = None
    if not index.lower() in LOAN_INDEX_TICKERS:
        missing_data = ''
        trades = datastore.get_trace_volume(dt)
        etf_change = datastore.get_etf_change(dt)
        if trades is None:
            missing_data += 'Trace Volume/'
        if etf_change is None:
            missing_data += 'ETF/'
        if len(missing_data) > 0:
            missing_msg = f'{missing_data[:-1]} data request is in progress for the date: {dt} and is estimated to finish in 2-5 mins. Please refresh later.'

    return trades, etf_change, missing_msg


def create_result_layout_for_index_dt(dt, index, issuer, industry, instrument_id):
    dt = dtutil.adjustdate(dt, direction='backward')
    ref_index_returns = datastore.get_index_reference_returns('H0A0', dt)
    ref_index_returns.rename(
        columns={'return_ytd': 'Return YTD', 'return_wtd': 'Return WTD', 'return_mtd': 'Return MTD',
                 'return_dtd': 'Return 1 day'}, inplace=True)
    ref_index = ref_index_returns.index.values[0]
    trades, etf_change, missing_msg = _get_additional_data(dt, index)
    df = datastore.get_index_returns(index, dt, trades, etf_change).sort_values('bond_point_return_dtd')

    if issuer:
        df = df[df['ticker'].isin(dfhelpers.listify(issuer))]
    if industry:
        df = df[df['industry'].isin(dfhelpers.listify(industry))]
    if instrument_id:
        df = df[df['instrument_id'].isin(dfhelpers.listify(instrument_id))]
    df = _format_index(df)

    result_elements = [html.Br()]
    for col in reversed(ref_index_returns.columns):
        idx_ret_string = '{:.2f}%'.format((ref_index_returns[col].iloc[0] * 100))
        result_elements += [
            html.Label(f"{ref_index}: {col}: {idx_ret_string}", className='subtitle', style={'font-weight': 'bold'})]

    result_elements = [html.Div(children=result_elements, className='fund_filter')]
    result_elements += [
        html.Br(),
        html.Div(children=[html.Label(f"Results for index: {index} and date: {dt}. {missing_msg}",
                                      className='subtitle', ),
                           make_dash_table(df, page_size=10, fixed_columns=3)],
                 className='fund_filter')]

    return result_elements


@callback([Output(INDEX_RESULT_CONTROL_ID, "children"), ],
          [Input(INDEX_SUBMIT_CONTROL_ID, 'n_clicks'), ],
          [State(INDEX_DT_CONTROL_ID, 'date'),
           State({'type': ui_config.SAVE_CONFIG_INPUT_TYPE, 'index': INDEX_VALUE_CONTROL_ID}, 'value'),
           State({'type': ui_config.SAVE_CONFIG_INPUT_TYPE, 'index': 'issuer'}, 'value'),
           State({'type': ui_config.SAVE_CONFIG_INPUT_TYPE, 'index': 'industry'}, 'value'),
           State({'type': ui_config.SAVE_CONFIG_INPUT_TYPE, 'index': 'instrument_id'}, 'value'),
           ])
def on_index_change_submit(n_clicks, dt, index, issuer, industry, instrument_id):
    return [create_result_layout_for_index_dt(dt, index, issuer, industry, instrument_id)]
