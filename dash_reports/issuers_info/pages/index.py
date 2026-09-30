from dash_reports.issuers_info.pages import capital_structure, fundamental_estimate, issuer_financials, \
    issuer_current_financials

import dash_html_components as html

from dash_reports.report_common.utils.components import create_link
from dash_reports.report_common.pages.link_and_headers import get_page_title


def get_issuer_info_header(page):
    return html.Div([get_page_title("Issuer Capital Structure & Financials"),
                     get_debt_view_index_menu(page),
                     html.Br(), ])


def get_debt_view_index_menu(page):
    if page == '':
        page = 'cap-struct'
    menu = html.Div([
        create_link('issuer-info', 'cap-struct', 'Capital Structure', selected_page=page),
        create_link('issuer-info', 'fund-est', 'Fundamental Estimates',
                    selected_page=page),
        create_link('issuer-info', 'fin', 'Financials Summary',
                    selected_page=page),
        create_link('issuer-info', 'curr-fin', 'Current Financials', selected_page=page),
    ], className="row all-tabs", )
    return menu


def create_layout(page, issuer_ids, user_settings):
    if page == 'cap-struct':
        return capital_structure.create_layout(issuer_ids)
    elif page == 'fund-est':
        return fundamental_estimate.create_layout(issuer_ids, user_settings)
    elif page == 'fin':
        return issuer_financials.create_layout(issuer_ids)
    elif page == 'curr-fin':
        return issuer_current_financials.create_layout()
    else:
        return capital_structure.create_layout(issuer_ids)
