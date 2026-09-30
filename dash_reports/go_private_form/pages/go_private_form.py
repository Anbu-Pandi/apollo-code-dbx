import dash_html_components as html

from dash_reports.go_private_form.pages.generic_info import generic_info_section
from dash_reports.report_common.pages.link_and_headers import get_page_title
from dash_reports.go_private_form.pages.entity_request_type import entity_name_section, request_type_section
from dash_reports.go_private_form.pages.deal_information_section import deal_info_section
from dash_reports.go_private_form.pages.other_information_section import other_info_section
import dash_reports.go_private_form.pages.utils as go_pvt_utils


def get_headers(app=None):
    if app is not None:
        app.title = 'Go Private Form'
    layout = html.Div([get_page_title("Go Private Form"),
                       ])
    return layout


def create_analyst_form():
    layout = [html.Br()]
    entity_name_layout = entity_name_section()
    generic_info_layout = generic_info_section()
    request_type_layout = request_type_section()
    deal_info_layout = deal_info_section()
    other_info_layout = other_info_section()

    layout.extend(entity_name_layout)
    layout.extend(generic_info_layout)
    layout.extend(request_type_layout)
    layout.extend(deal_info_layout)
    layout.extend(other_info_layout)
    return layout


def create_go_private_form_layout(app, page):
    app.title = 'Go Private Form'

    div_kids = (create_analyst_form())
    dynamic_content = html.Div(
        children=go_pvt_utils.get_submit_button(id={'type': go_pvt_utils.ANALYST_SUBMIT,
                                                    'index': 'go_pvt_submit'}
                                                ))

    return html.Div(children=div_kids, className="page"), dynamic_content
