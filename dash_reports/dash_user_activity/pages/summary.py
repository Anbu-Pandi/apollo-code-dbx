from dash_reports.dash_user_activity.data.access_report import get_dash_access_summary
from dash_reports.dash_user_activity.pages.charts import chart_app_usage

import dash_html_components as html
import dash_core_components as dcc
from dash_reports.report_common.pages.link_and_headers import get_page_title
from dash_reports.report_common.utils.components import make_dash_table
from dash_reports.report_common.utils.dfhelpers import ui_column_name


def get_dash_usage_headers(app=None):
    if app is not None:
        app.title = 'Dash Usage Report'
    layout = html.Div([get_page_title("Dash Usage Report"),
                       html.Br(),
                       ])
    return layout


def create_dash_usage_layout():
    df_user_activity, df_leaderboard = get_dash_access_summary()

    df_user_activity.columns = [ui_column_name(x) for x in df_user_activity.columns]
    df_leaderboard.columns = [ui_column_name(x) for x in df_leaderboard.columns]

    fig = chart_app_usage(df_user_activity, ['Page Views', 'Rolling 5 Day Avg'], 'View Date')

    static_layout = html.Div([html.Br(),
                              html.Div(
                                  children=[
                                      html.H6(['User Activity Trend'], className="subtitle padded"),
                                      html.Br(),
                                      dcc.Graph(figure=fig),
                                  ]
                              ),
                              html.Div(
                                  children=[
                                      html.H6(['Leaderboard'], className="subtitle padded"),
                                      html.Br(),
                                      make_dash_table(df_leaderboard)
                                  ])
                              ], className="sub_page")
    return static_layout
