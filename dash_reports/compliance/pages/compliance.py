import dash_html_components as html

from dash_reports.compliance.pages.status import create_stats_layout
from dash_reports.report_common.pages.link_and_headers import get_page_title
from dash_reports.compliance.pages.compliance_info_section import compliance_section_header
from dash_reports.compliance.pages.group_request_id import create_group_request_layout
import dash_reports.compliance.pages.utils as compliance_utils


def get_headers(app=None):
    if app is not None:
        app.title = 'Compliance'
    layout = html.Div([get_page_title("Compliance"),
                       html.Br(),
                       compliance_utils.get_links()
                       ])
    return layout


def create_compliance_layout(page):
    static_content = []
    dynamic_content = []

    if page == 'go-private-request':
        static_content, dynamic_content = compliance_section_header()
    elif page == 'go-private-request-status':
        static_content = create_stats_layout()
    elif page == 'go-private-request-group-request-id':
        static_content, dynamic_content = create_group_request_layout()

    return html.Div(children=static_content, className="page"), dynamic_content
