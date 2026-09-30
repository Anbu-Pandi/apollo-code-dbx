import logging

import dash_core_components as dcc
import dash_html_components as html
import pandas as pd
from dash import callback, Input, Output, State, ALL

from common.utils import dtutil
from dash_reports.report_common.utils.components import make_dash_table
from dash_reports.risk_report.pages.utils import get_datepicker_control
from dash_reports.trace.data.trace_flow import get_trace_by_cusip_isin_ticker

DATE_CONTROL_ID = 'trace-date-search-control'
CUSIP_CONTROL_ID = 'trace-cusip-search-control'
SECTOR_CONTROL_ID = 'trace-sector-search-control'
FUND_CONTROL_ID = 'trace-fund-search-control'
TRACE_DATE_SEARCH_SUBMIT_ID = 'trace-date-search-submit'
TRACE_DATE_RESULT_ID = 'trace-date-search-results'


def create_layout():
    dates = \
        html.Div(style=dict(display='flex'),
                 children=[html.Div([html.Label("{:<25}".format("Date:"), className='subtitle')],
                                    style=dict(width='12.5%')),
                           html.Div([get_datepicker_control(DATE_CONTROL_ID, dt=dtutil.dateadd(dtutil.today(), '-1b'),
                                                            disabled=False)],
                                    style=dict(width='50%', height='35px'))])
    cusip_or_isin_search = \
        html.Div(style=dict(display='flex'),
                 children=[html.Div([html.Label("{:<25}".format("Cusip / ISIN / Ticker:"), className='subtitle')],
                                    style=dict(width='12.5%')),
                           html.Div([dcc.Input(id=CUSIP_CONTROL_ID, type='search',
                                               placeholder=f"cusip/isin/ticker, sector or fund is required",
                                               className='input')],
                                    style=dict(width='30%', height='35px'))])

    sector_search = \
        html.Div(style=dict(display='flex'),
                 children=[html.Div([html.Label("{:<25}".format("Sector:"), className='subtitle')],
                                    style=dict(width='12.5%')),
                           html.Div([dcc.Input(id=SECTOR_CONTROL_ID, type='search',
                                               placeholder=f"cusip/isin/ticker, sector or fund is required",
                                               className='input')],
                                    style=dict(width='30%', height='35px'))])
    fund_search = \
        html.Div(style=dict(display='flex'),
                 children=[html.Div([html.Label("{:<25}".format("Fund Entity Code:"), className='subtitle')],
                                    style=dict(width='12.5%')),
                           html.Div([dcc.Input(id=FUND_CONTROL_ID, type='search',
                                               placeholder=f"cusip/isin/ticker, sector or fund is required",
                                               className='input')],
                                    style=dict(width='30%', height='35px'))])

    submit_btn = html.Div([html.Button('Search', id=TRACE_DATE_SEARCH_SUBMIT_ID, className='submit-button')])

    search_results = html.Div(id=TRACE_DATE_RESULT_ID, children=[], className='page')

    return html.Div([html.Div([dates,
                               cusip_or_isin_search,
                               sector_search,
                               fund_search,
                               submit_btn,
                               ], className='fund_filter'),
                     search_results])


def create_result_layout(dt, cusip_or_isin, sector, fund):
    logging.info(f"search request for dt: {dt}, cusip_or_isin: {cusip_or_isin}, sector: {sector}, fund: {fund}")
    if not dt is None:
        dt = pd.to_datetime(dt, errors='coerce').date()
        df = get_trace_by_cusip_isin_ticker(dt, cusip_or_isin=cusip_or_isin, sector=sector, fundentitycode=fund)
        searchdiv = html.Div(children=[
            html.H6(
                f"Found {len(df)} rows for Date: {dt}, Cusip or ISIN or Ticker: {cusip_or_isin}, Sector: {sector}, Fund: {fund}",
                className='subtitle'),
            html.Div(make_dash_table(df),
                     style={'display': 'flex'})],
            className='fund_filter')

        logging.info(
            f"search request completed for dt: {dt}, cusip_or_isin: {cusip_or_isin}, sector: {sector}, fund: {fund}")
        return searchdiv


@callback([Output(TRACE_DATE_RESULT_ID, "children"), ],
          [Input(TRACE_DATE_SEARCH_SUBMIT_ID, "n_clicks")],
          [State(DATE_CONTROL_ID, 'date'),
           State(CUSIP_CONTROL_ID, 'value'),
           State(SECTOR_CONTROL_ID, 'value'),
           State(FUND_CONTROL_ID, 'value'), ])
def on_tracedate_search(n_clicks, dt, cusip_or_isin, sector, fund):
    logging.info(f'on trace date search: {dt}, {cusip_or_isin}, {sector}, {fund}')
    return [create_result_layout(dt, cusip_or_isin, sector, fund)]
