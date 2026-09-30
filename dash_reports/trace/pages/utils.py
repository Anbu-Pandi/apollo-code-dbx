import dash_html_components as html

from dash_reports.report_common.pages.link_and_headers import get_page_title
from dash_reports.report_common.utils.components import create_link
from dash_reports.trace.pages import trace_flow, trades_by_date


def get_trace_header():
    return html.Div([get_page_title("TRACE Trades"),
                     html.Br(), ])


def get_trace_menu(page):
    menu = html.Div([
        create_link('trace', 'flow-summary', 'Flow Summary', selected_page=page),
        create_link('trace', 'trades-by-date', 'Trades By Date', selected_page=page)],
        className="row all-tabs", )
    return menu


def create_traceflow_layout(app, tracepage):
    app.title = 'TRACE'
    div_kids = [get_trace_menu(tracepage)]
    if tracepage == 'flow-summary':
        div_kids.append(trace_flow.create_layout())
    elif tracepage == 'trades-by-date':
        div_kids.append(trades_by_date.create_layout())
    return html.Div(children=div_kids)
