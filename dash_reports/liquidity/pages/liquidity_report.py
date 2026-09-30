import pandas as pd
import datetime

import dash_core_components as dcc
import dash_html_components as html
import logging

from dash_reports.report_common.pages.link_and_headers import get_page_title
from dash_reports.report_common.utils.components import create_link
from dash_reports.liquidity.pages.fund_info_limits import fund_info_layout
from dash_reports.liquidity.pages.leverage_analysis import leverage_analysis_layout
from dash_reports.liquidity.pages.fund_commit import fund_commitment_layout
from dash_reports.liquidity.pages.cash_details import cash_summary_layout


def get_liquidity_headers(app=None):
    if app is not None:
        app.title = 'Liquidity Report'
    layout = html.Div([get_page_title("Liquidity Report"),
                       html.Br(),
                       ])
    return layout


def get_section_links():
    menu = html.Div([create_link('liquidity-report', 'cash-summary', 'Cash Summary'),
                     create_link('liquidity-report', 'leverage-facility', 'Leverage Facility'),
                     create_link('liquidity-report', 'fund-commitments', 'Fund Commitments'),
                     create_link('liquidity-report', 'fund-info', 'Fund Information')],
                    className="row all-tabs", )
    return menu


def get_section_specific_layout(path):
    div_kids = [get_section_links(), html.Br(), get_section_output(path)]
    return html.Div(id='liquidity-result-container', children=div_kids)


def get_section_output(path):
    logging.info(f'Liquidity Report - getting output for {path}')
    if path == 'cash-summary':
        return cash_summary_layout()
    elif path == 'leverage-facility':
        return leverage_analysis_layout()
    elif path == 'fund-commitments':
        return fund_commitment_layout()
    elif path == 'fund-info':
        return fund_info_layout()
    else:
        return []
