from dash import callback, Input, Output, State, MATCH, html
import logging
from dash_reports.benchmark_performance.pages import perf_page, layout, charters
from dash_reports.benchmark_performance.data import index_data

from common.utils.dfutil import tolist


@callback([Output(layout.BM_RESULT_ID, "children"), ],
          [Input(layout.BM_SUBMIT_ID, "n_clicks")],
          [State(layout.BM_METRIC_DD_CONTROL_ID, 'value'),
           State(layout.BM_FROM_DATE_CONTROL_ID, 'date'),
           State(layout.BM_DATE_CONTROL_ID, 'date'),
           ])
def on_bm_perf_submit(n_clicks, metric, fromdt, todt):
    if n_clicks:
        logging.info(f'on benchmark performance submit: {fromdt}, {todt}')
        (dfs, index_desc) = perf_page._get_data(todt)
        if metric == 'excess_return':
            perf_page._get_cached_data(todt, use_beta_df=True)
        indexes = perf_page.get_main_indexes(index_desc)
        return [perf_page.create_result_layout(indexes, index_desc, metric)]

    return [html.Div()]


@callback([Output({'type': perf_page.BM_OTHER_CHART_ID, 'index': MATCH}, "children"),
           Output({'type': perf_page.BM_SUB_CHART_ID, 'index': MATCH}, "children"),
           Output({'type': perf_page.BM_BETA_TS_CHART_ID, 'index': MATCH}, "children"), ],
          [Input({'type': perf_page.BM_DD_INDEX_ID, 'index': MATCH}, 'value'),
           Input({'type': perf_page.BM_DD_OTHER_INDEX_ID, 'index': MATCH}, 'value'),
           Input({'type': perf_page.BM_DD_SUB_INDEX_ID, 'index': MATCH}, 'value'),
           State(layout.BM_METRIC_DD_CONTROL_ID, 'value'),
           State(layout.BM_FROM_DATE_CONTROL_ID, 'date'),
           State(layout.BM_DATE_CONTROL_ID, 'date')])
def on_dd_chg(index, other_indexes, sub_indexes, metric, fromdt, todt):
    other_div_kids = []
    sub_div_kids = []
    beta_ts_div_kids = []

    use_beta_df = False
    is_index = False
    y_title = layout.BM_METRIC_DD_CONTROL_DICT[metric]

    if metric == 'excess_return':
        use_beta_df = True
        is_index = True
        y_title += ' Index'

    cached_data = perf_page._get_cached_data(todt, use_beta_df)

    dfs = cached_data['dfs']
    index_desc = cached_data['index_desc']
    beta_dfs = None
    if use_beta_df:
        beta_dfs = cached_data['beta_dfs']

    plot_dfs = index_data.get_plot_dfs(dfs[metric], fromdt, index_desc,
                                       beta_df=beta_dfs['df'] if use_beta_df else None,
                                       is_index=is_index)
    plot_df_other = index_data.get_rel_chart_df(plot_dfs, index_desc, index, tolist(other_indexes))
    plot_df_sub = index_data.get_rel_chart_df(plot_dfs, index_desc, index, tolist(sub_indexes))

    other_div_kids.extend([html.Br()] * 2)
    other_div_kids.append(html.H6(f"{index} v/s Other Indexes:", className='subtitle'))
    other_div_kids.append(charters.create_rel_chart(plot_df_other, y_title=y_title))

    sub_div_kids.extend([html.Br()] * 2)

    sub_div_kids.append(html.H6(f"{index} v/s Sub Indexes:", className='subtitle'))
    sub_div_kids.append(charters.create_rel_chart(plot_df_sub, y_title=y_title))

    if use_beta_df:
        beta_ts_div_kids.extend([html.Br()] * 2)
        beta_ts_div_kids.append(html.H6(f"{index} v/s Indexes and Beta:",
                                        className='subtitle'))
        for other_index in tolist(other_indexes):
            df = index_data.get_beta_chart_df(plot_dfs, beta_dfs['ts_df'], (index, other_index))
            beta_ts_div_kids.append(charters.create_beta_chart(df, y_title=y_title))
        for sub_index in tolist(sub_indexes):
            df = index_data.get_beta_chart_df(plot_dfs, beta_dfs['ts_df'], (index, sub_index))
            beta_ts_div_kids.append(charters.create_beta_chart(df, y_title=y_title))

    return [html.Div(other_div_kids), html.Div(sub_div_kids), html.Div(beta_ts_div_kids)]
