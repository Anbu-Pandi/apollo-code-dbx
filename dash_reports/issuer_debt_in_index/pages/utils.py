from dash_reports.issuer_debt_in_index.pages import bonds_rv_ts, bonds_rv_list, issuer_debt_pages

import dash_html_components as html

from dash_reports.report_common.utils.components import create_link
from dash_reports.report_common.pages.link_and_headers import get_page_title

DATE_CONTROL_ID = 'debt-view-date-control'
INPUT_CONTROL_ID = 'debt-view-input-control'
DEBT_VIEW_SUBMIT_ID = 'debt-view-submit'
DEBT_VIEW_RESULT_ID = 'debt-view-results'


def get_issuer_debt_index_header():
    return html.Div([get_page_title("Issuer Debt in Index"),
                     html.Br(), ])


def get_debt_view_index_menu(page):
    menu = html.Div([
        create_link('issuer-debt-index', 'movers', 'Movers', selected_page=page),
        create_link('issuer-debt-index', 'secured-unsecured-issuers', 'Issuers - Secured / Unsecured Summary',
                    selected_page=page),
        create_link('issuer-debt-index', 'secured-unsecured-bonds', 'Bonds - Secured / Unsecured Spread',
                    selected_page=page),
        create_link('issuer-debt-index', 'issuer-spread-curve', 'Issuer Spread Curve', selected_page=page),
        create_link('issuer-debt-index', 'bonds-rv-list', 'Bonds RV', selected_page=page),
        create_link('issuer-debt-index', 'bonds-rv-ts', 'Bonds', selected_page=page),
    ], className="row all-tabs", )
    return menu


def create_layout(page, bonds, user_settings=None):
    if page == 'bonds-rv-list':
        return bonds_rv_list.create_layout(page)
    elif page == 'bonds-rv-ts':
        return bonds_rv_ts.create_layout(page, bonds)
    else:
        return issuer_debt_pages.create_layout_issuer_debt(page,user_settings=user_settings)
