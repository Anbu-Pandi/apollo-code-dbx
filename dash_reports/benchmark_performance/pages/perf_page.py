import logging
from dash import html, dcc
from common.utils import dtutil
from ..data.index_data import get_main_indexes, get_sub_indexes
from dash_reports.report_common.data.data_model import datastore
from dash_reports.benchmark_performance.data import index_data

BM_DD_INDEX_ID = 'bm-dd-index-id'
BM_DD_OTHER_INDEX_ID = 'bm-dd-other-index-id'
BM_DD_SUB_INDEX_ID = 'bm-dd-sub-index-id'
BM_OTHER_CHART_ID = 'bm-other-chart-id'
BM_SUB_CHART_ID = 'bm-sub-chart-id'
BM_BETA_TS_CHART_ID = 'bm-beta-ts-chart-id'


def _get_datastore_key(todt, tag):
    return f'BM_{todt}_{tag}'


def _get_cached_data(todt, use_beta_df=False):
    (dfs, index_desc) = datastore.get_app_key(_get_datastore_key(todt, tag='dfs'))
    beta_dfs = None
    if use_beta_df:
        plot_key = _get_datastore_key(todt, tag='beta_dfs')
        ret = datastore.get_app_key(plot_key)
        if not ret:
            beta_dfs = index_data.get_beta_dfs(dfs['excess_return'], index_desc)
            datastore.set_app_key(plot_key, beta_dfs)
        else:
            beta_dfs = datastore.get_app_key(plot_key)

    return {'dfs': dfs, 'index_desc': index_desc, 'beta_dfs': beta_dfs}


def _get_data(todt):
    todt = dtutil.todate(todt)
    key = _get_datastore_key(todt, tag='dfs')
    ret = datastore.get_app_key(key)
    if ret:
        return ret

    index_desc = index_data.get_index_desc()
    dfs = index_data.get_index_data(todt, index_desc)
    ret = (dfs, index_desc)

    datastore.set_app_key(key, ret)
    return ret


def create_tab_layout(index, index_desc, metric):
    label = html.H6("Select Reference Index:", style=dict(width='10%', display='inline-block'), className='subtitle')
    main_indexes = get_main_indexes(index_desc)
    dd = dcc.Dropdown(id={'type': BM_DD_INDEX_ID, 'index': index},
                      options={x: index_desc.loc[x, 'index_desc'] for x in main_indexes},
                      value=index, multi=False, style=dict(width='80%'))
    label_dd = html.Div(style=dict(display='flex'), children=[label, dd])

    label_other = html.H6("Select Other Indexes:", style=dict(width='10%', display='inline-block'),
                          className='subtitle')

    other_indexes = main_indexes
    sub_indexes = get_sub_indexes(index_desc, index)
    dd_other = dcc.Dropdown(id={'type': BM_DD_OTHER_INDEX_ID, 'index': index},
                            options={x: index_desc.loc[x, 'index_desc'] for x in other_indexes + sub_indexes},
                            value=[x for x in main_indexes if (x != index) and (metric == 'excess_return')],
                            multi=True,
                            style=dict(width='80%'))
    label_dd_other = html.Div(style=dict(display='flex'), children=[label_other, dd_other])

    label_sub = html.H6("Select Sub Indexes:", style=dict(width='10%', display='inline-block'),
                        className='subtitle')

    dd_sub = dcc.Dropdown(id={'type': BM_DD_SUB_INDEX_ID, 'index': index},
                          options={x: index_desc.loc[x, 'index_desc'] for x in sub_indexes + other_indexes},
                          value=[x for x in sub_indexes if x != index],
                          multi=True,
                          style=dict(width='80%'))
    label_dd_sub = html.Div(style=dict(display='flex'), children=[label_sub, dd_sub])

    other_chart = html.Div(children=[], id={'type': BM_OTHER_CHART_ID, 'index': index}, style=dict(width='100%'))
    sub_chart = html.Div(children=[], id={'type': BM_SUB_CHART_ID, 'index': index}, style=dict(width='100%'))
    beta_ts_chart = html.Div(children=[], id={'type': BM_BETA_TS_CHART_ID, 'index': index}, style=dict(width='100%'))
    return html.Div(children=[html.Br(), label_dd, label_dd_other, other_chart, label_dd_sub, sub_chart, beta_ts_chart],
                    className='fund_filter',
                    style={'width': '100%'})


def create_tabs(indexes, index_desc, metric):
    tabs = []
    for index in indexes:
        tab_kids = []
        tab_kids.append(create_tab_layout(index, index_desc, metric))
        tabs.append(dcc.Tab(label=index_desc.loc[index, 'main_index_desc'], children=tab_kids, className='fund_filter'))

    return dcc.Tabs(tabs)


def create_result_layout(indexes, index_desc, metric):
    logging.info(f"benchmark performance getting results layout")
    fund_tab_layout = create_tabs(indexes, index_desc, metric)
    return html.Div(children=[html.Br(), fund_tab_layout], className='fund_filter')
