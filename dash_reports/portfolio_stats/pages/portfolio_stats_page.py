import logging

import plotly.graph_objs as go
from dash import callback, Input, Output, State, MATCH, dcc, html

from common.utils import dtutil
from common.utils.dfutil import tolist
from dash_reports.report_common.pages.link_and_headers import get_page_title
from dash_reports.report_common.utils import combos
from dash_reports.report_common.utils.components import make_dash_table
from dash_reports.risk_report.pages.utils import get_datepicker_control
from ..data.portfolio_dfs import get_dfs_dicts

PF_STATS_DATE_CONTROL_ID = 'pf-stats-date-control'
PF_STATS_SUBMIT_ID = 'pf-stats-submit'
PF_STATS_RESULT_ID = 'pf-stats-results'

PF_STATS_METRICS_DD_ID = 'pf-stats-metrics-dropdown-control'
PF_STATS_METRICS_CHART_ID = 'pf-stats-metrics-chart-control'


def get_pf_stats_header():
    return html.Div([get_page_title("Portfolio Statistics"),
                     html.Br(), ])


def create_layout():
    dt = dtutil.dateadd(dtutil.today(), '0b')
    dates = \
        html.Div(style=dict(display='flex'),
                 children=[html.Div([html.Label("{:<25}".format("Date:"), className='subtitle')],
                                    style=dict(width='12.5%')),
                           html.Div([get_datepicker_control(PF_STATS_DATE_CONTROL_ID, dt=dt,
                                                            disabled=False)],
                                    style=dict(width='50%', height='35px'))])

    submit_btn = html.Div([html.Button('Load', id=PF_STATS_SUBMIT_ID, className='submit-button')])

    search_results = html.Div(id=PF_STATS_RESULT_ID, children=[], className='page')

    return html.Div([html.Div([dates,
                               submit_btn,
                               ], className='fund_filter'),
                     search_results])


def create_metric_chart(df, title):
    col_plots = []
    for c in df.columns:
        col_plots.append(go.Bar(x=df.index.to_numpy(), y=df[c].to_numpy(), name=str(c)))
    fig = go.Figure(data=col_plots)
    fig.update_layout(title_text=f'<span style="font-size: 12px;font-weight: bold;">{title}</span>', title_x=0.5,)
    div_out_kids = []
    chart = dcc.Graph(figure=fig)
    dash_table_html = combos.get_dash_table_with_header(title, df.reset_index())
    div_out_kids.extend([chart, dash_table_html])

    return html.Div(div_out_kids)


def create_metric_layout(df_dict, index):
    if isinstance(df_dict, dict):
        metrics = list(df_dict.keys())
    else:
        metrics = list(df_dict.columns.unique(level=0))
    selected_metric = metrics[0]
    label = html.H6("Select Metric:", style=dict(width='10%', display='inline-block'), className='subtitle')
    dd = dcc.Dropdown(id={'type': PF_STATS_METRICS_DD_ID, 'index': index}, options={x: x for x in metrics},
                      value=selected_metric, multi=True, style=dict(width='50%', display='inline-block'))
    label_dd = html.Div([label, dd])
    chart = html.Div(children=[], id={'type': PF_STATS_METRICS_CHART_ID, 'index': index}, style=dict(width='100%'))
    return html.Div(children=[label_dd, chart], className='fund_filter', style={'width': '100%', })


def create_fund_tabs(pfolios_positions_dict, pfolios_stats_dict, pfolios_dist_dict_dict):
    funds = sorted(
        set(list(pfolios_positions_dict)) & set(list(pfolios_stats_dict)) & set(list(pfolios_dist_dict_dict)))
    tabs = []
    for fund in funds:
        tab_kids = []
        stats = pfolios_stats_dict[fund]
        pos = pfolios_positions_dict[fund]
        metrics_dict = pfolios_dist_dict_dict[fund]

        t1 = html.Div(children=[html.H6('Summary Statistics:', className='subtitle'),
                                make_dash_table(stats.reset_index())],
                      style={'width': '25%', 'display': 'inline-block'})
        t2 = html.Div(children=[html.H6('Positions:', className='subtitle'),
                                make_dash_table(pos)],
                      style={'width': '70%', 'display': 'inline-block', 'float': 'right'})
        tab_kids.append(html.Div(children=[html.Br(), t1, t2, ], ))
        tab_kids.append(create_metric_layout(metrics_dict, fund))
        tabs.append(dcc.Tab(label=fund, children=tab_kids, className='fund_filter'))

    return dcc.Tabs(tabs)


def create_result_layout(dt):
    logging.info(f"portfolio stats getting results for dt: {dt}")

    pfolios_positions_dict, bmarks_positions_dict, pfolios_stats_dict, pfolios_dist_dict_dict, \
    master_df_unstacked, master_dist_df_dict_unstacked, master_df_output = get_dfs_dicts(dt)

    output_table = html.Div(children=[html.H6('Summary Statistics:', className='subtitle'),
                                      make_dash_table(master_df_output.reset_index())], className="six columns")
    metric_layout = create_metric_layout(master_df_output, 'OVERALL')
    fund_tab_layout = create_fund_tabs(pfolios_positions_dict, pfolios_stats_dict, pfolios_dist_dict_dict)

    return html.Div(children=[output_table, metric_layout, fund_tab_layout], className='fund_filter')


@callback([Output(PF_STATS_RESULT_ID, "children"), ],
          [Input(PF_STATS_SUBMIT_ID, "n_clicks")],
          [State(PF_STATS_DATE_CONTROL_ID, 'date'),
           ])
def on_pf_stat_submit(n_clicks, dt):
    if n_clicks:
        logging.info(f'on pf stats submit: {dt}')
        return [create_result_layout(dt)]
    return [html.Div()]


@callback([Output({'type': PF_STATS_METRICS_CHART_ID, 'index': MATCH}, "children"), ],
          [Input({'type': PF_STATS_METRICS_DD_ID, 'index': MATCH}, 'value'),
           State({'type': PF_STATS_METRICS_DD_ID, 'index': MATCH}, 'id'),
           State(PF_STATS_DATE_CONTROL_ID, 'date')])
def on_dd_chg(metric, metric_id, dt):
    pfolios_positions_dict, bmarks_positions_dict, pfolios_stats_dict, pfolios_dist_dict_dict, \
    master_df_unstacked, master_dist_df_dict_unstacked, master_df_output = get_dfs_dicts(dt)
    div_kids = []
    if metric_id['index'] == 'OVERALL':
        for m in tolist(metric):
            div_kids.append(create_metric_chart(master_df_output[m], m))
    else:
        fund = metric_id['index']
        for m in tolist(metric):
            div_kids.append(create_metric_chart(pfolios_dist_dict_dict[fund][m], m))
    return [html.Div(div_kids)]
