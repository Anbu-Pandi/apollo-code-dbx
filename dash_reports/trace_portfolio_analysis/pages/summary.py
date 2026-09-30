import html.parser

import dash_html_components as html
import dash_core_components as dcc
from dash_reports.report_common.utils import dfhelpers
from dash_reports.report_common.utils.components import make_dash_table
import dash_reports.trace_portfolio_analysis.pages.charts as charts
import dash_reports.trace_portfolio_analysis.data.portfolio_trades as data
import dash_reports.trace_portfolio_analysis.pages.utils as page_util
import common.utils.dtutil as dtutil

summary_categories = ['all']


def get_ice_hy_ig_oas_yield_chart(df):
    ice_index = ['C0A0']

    index_fig = []

    for index in ice_index:
        df_filter = df[df['ice_index_ticker'] == index]
        df_filter = df_filter[['asof_date', 'oas', 'yield_to_maturity_sa']]
        fig = charts.get_ice_index_oas_yield_chart(df_filter, title=f'{index} OAS / Yield', width=1800)

        index_fig.append(dcc.Graph(figure=fig, style={'display': 'inline-block'})
                         )
    chart_div = html.Div(index_fig, style={'display': 'inline-block'})
    return chart_div


def get_hy_ig_dv01_chart(df):
    df = df[df['aggregate_level'].isin(['HY', 'IG', 'other'])]
    fig = charts.get_net_dv01_chart(df)
    chart_div = html.Div([dcc.Graph(figure=fig, style={'display': 'inline-block'},
                                    )
                          ])
    return chart_div


def get_all_trades_heatmaps_div(df):
    div_kids = []
    for metric in ['apo_dv01', 'dirty_mv', 'apo_tcost']:
        metric_label = data.col_rename_dict[metric]

        pivot_by_sprd = data.get_pivot_by_spd_dur(df, metric)
        pivot_by_rating = data.get_pivot_by_rating_dur(df, metric)

        heatmap_spread = page_util.get_heatmap_div(pivot_by_sprd,
                                                   title=f'{metric_label} (in millions) by Spread & Duration')
        heatmap_spread_chg_div = get_spread_chg_df_heatmap_div(df, metric)

        div_kids.append(html.Div([heatmap_spread, heatmap_spread_chg_div], style={'display': 'flex',
                                                                                  'justify-content': 'space-between',
                                                                                  'gap': '10px',
                                                                                  'width': '100%',
                                                                                  'box-sizing': 'border-box'}))

        heatmap_rating = page_util.get_heatmap_div(pivot_by_rating,
                                                   title=f'{metric_label} (in millions) by Ratings & Duration')

        heatmap_rating_chg_div = get_rating_chg_df_heatmap_div(df, metric)

        div_kids.append(html.Div([heatmap_rating, heatmap_rating_chg_div], style={'display': 'flex',
                                                                                  'justify-content': 'space-between',
                                                                                  'gap': '10px',
                                                                                  'width': '100%',
                                                                                  'box-sizing': 'border-box'}))

    return html.Div(div_kids)


def get_trxn_cost_to_gross_notional_div(df):
    today = df['effective_date'].max()
    prev_dt = dtutil.dateadd(today, '-1b')
    prev_week_dt = dtutil.dateadd(today, '-5b')

    tcost_by_spread, tcost_by_rating = data.get_trxn_cost_by_gross_notional(df)

    heatmap_spread = page_util.get_heatmap_div(tcost_by_spread,
                                               title=f'Ratio of Transaction Cost to Gross Notional (in %) '
                                                     f'by Spread & Duration')

    heatmap_rating = page_util.get_heatmap_div(tcost_by_rating,
                                               title=f'Ratio of Transaction Cost to Gross Notional (in %) '
                                                     f'by Ratings & Duration')

    dod_chg_sprd, dod_chg_rating = data.get_trxn_cost_by_gross_notional_chg(df, today, prev_dt)
    wow_chg_sprd, wow_chg_rating = data.get_trxn_cost_by_gross_notional_chg(df, today, prev_week_dt)

    heatmap_dod_spd = page_util.get_heatmap_div(dod_chg_sprd, title=f'DOD Change: {today} - {prev_dt}')
    heatmap_wow_spd = page_util.get_heatmap_div(wow_chg_sprd, title=f'Weekly Change: {today} - {prev_week_dt}')

    txn_cost_by_spread_div = html.Div([heatmap_spread, heatmap_dod_spd, heatmap_wow_spd], style={'display': 'flex',
                                                                                                 'justify-content': 'space-between',
                                                                                                 'gap': '10px',
                                                                                                 'width': '100%',
                                                                                                 'box-sizing': 'border-box'})

    heatmap_dod_rating = page_util.get_heatmap_div(dod_chg_rating, title=f'DOD Change: {today} - {prev_dt}')
    heatmap_wow_rating = page_util.get_heatmap_div(wow_chg_rating, title=f'Weekly Change: {today} - {prev_week_dt}')

    txn_cost_by_rating_div = html.Div([heatmap_rating, heatmap_dod_rating, heatmap_wow_rating],
                                      style={'display': 'flex',
                                             'justify-content': 'space-between',
                                             'gap': '10px',
                                             'width': '100%',
                                             'box-sizing': 'border-box'})

    return html.Div([txn_cost_by_spread_div, txn_cost_by_rating_div])


def get_spread_chg_df_heatmap_div(df, agg_col):
    today = df['effective_date'].max()
    prev_dt = dtutil.dateadd(today, '-1b')
    prev_week_dt = dtutil.dateadd(today, '-5b')

    sprd_dod_chg = data.get_spd_dur_chg_df(df, agg_col, today, prev_dt)
    sprd_wow_chg = data.get_spd_dur_chg_df(df, agg_col, today, prev_week_dt)

    heatmap_dod = page_util.get_heatmap_div(sprd_dod_chg, title=f'DOD Change: {today} - {prev_dt}')
    heatmap_wow = page_util.get_heatmap_div(sprd_wow_chg, title=f'Weekly Change: {today} - {prev_week_dt}')

    return html.Div([heatmap_dod, heatmap_wow], style={'display': 'flex',
                                                       'justify-content': 'space-between',
                                                       'gap': '10px',
                                                       'width': '100%',
                                                       'box-sizing': 'border-box'})


def get_rating_chg_df_heatmap_div(df, agg_col):
    today = df['effective_date'].max()
    prev_dt = dtutil.dateadd(today, '-1b')
    prev_week_dt = dtutil.dateadd(today, '-5b')

    sprd_dod_chg = data.get_rating_dur_chg_df(df, agg_col, today, prev_dt)
    sprd_wow_chg = data.get_rating_dur_chg_df(df, agg_col, today, prev_week_dt)

    heatmap_dod = page_util.get_heatmap_div(sprd_dod_chg, title=f'DOD Change: {today} - {prev_dt}')
    heatmap_wow = page_util.get_heatmap_div(sprd_wow_chg, title=f'Weekly Change: {today} - {prev_week_dt}')

    return html.Div([heatmap_dod, heatmap_wow], style={'display': 'flex',
                                                       'justify-content': 'space-between',
                                                       'gap': '10px',
                                                       'width': '100%',
                                                       'box-sizing': 'border-box'})


def get_page_content(df, ice_index_summary_df, from_dt):
    h0a0_c0a0_risk_chart_div = get_ice_hy_ig_oas_yield_chart(ice_index_summary_df)
    hy_ig_dv01_chart_div = get_hy_ig_dv01_chart(df)

    df_all = df.loc[(df['aggregate_level'] == 'all') & (df['effective_date'] >= from_dt), :]

    all_heatmaps_df_div = get_all_trades_heatmaps_div(df_all)
    trxn_cost_to_notional_heatmap = get_trxn_cost_to_gross_notional_div(df_all)

    page_content_kids = [h0a0_c0a0_risk_chart_div, hy_ig_dv01_chart_div, all_heatmaps_df_div,
                         trxn_cost_to_notional_heatmap]
    return page_content_kids


def create_summary_category_layout(df, ice_index_summary_df, from_dt):
    page_content_div_kids = get_page_content(df, ice_index_summary_df, from_dt)

    layout = [html.Br(),
              html.Div([page_util.get_section_title('Trades Summary'),
                        html.Br(),
                        html.Div(page_content_div_kids),
                        html.Br(),
                        ]
                       ),
              html.Br(),
              ]
    return layout
