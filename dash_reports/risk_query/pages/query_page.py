import itertools
import logging
import numbers

import dash_html_components as html
import pandas as pd
import plotly.graph_objs as go
from dash import callback, Input, Output, State, ALL
from dash import dcc, html
from plotly.subplots import make_subplots

from common.utils import dtutil
from dash_reports.report_common.pages import pivot_page_helpers
from dash_reports.report_common.pages import ui_config
from dash_reports.report_common.utils import dfhelpers
from dash_reports.report_common.utils.combos import get_labelled_component
from dash_reports.report_common.utils.components import make_dash_table
from dash_reports.risk_query.data.queries import RISKMETRIC_COL_AGG_MAP
from dash_reports.risk_query.data.query_db import get_query_results
from dash_reports.risk_report.pages.utils import get_datepicker_control

MAX_CHART_DIMS = 10
MAX_FILTER_ROWS = 4

FROM_DATE_CONTROL_ID = 'fromdt_riskquery_id'
TO_DATE_CONTROL_ID = 'todt_riskquery_id'
RISKMETRICS_DD_ID = 'riskmetrics_dd_id'
GBY_DD_ID = 'gby_dd_id'
KEEP_VALUES_AS_CONTRIBUTION = 'contribution_column_cb_id'
DIFF_CB_ID = 'diff_cb_id'
FILTER_DD_ID = 'filter_dd_id'
FILTER_INPUT_ID = 'filter_input_id'
RISK_QUERY_SUBMIT_ID = 'submit_riskquery_id'
RISK_QUERY_RESULT_ID = 'result_riskquery_id'
CHG_STORE_ID = 'chg-store-id'

RISMETRIC_FIELDS = list(RISKMETRIC_COL_AGG_MAP.keys()) + [f'cum_{x}' for x in RISKMETRIC_COL_AGG_MAP.keys() if
                                                          x.startswith('daily')]

GBY_FIELDS = ['fund_entity_code', 'fo_strategy_broad_category', 'fo_strategy', 'strategy',
              'issuer_ult', 'issuer_ult_ticker', 'security', 'security_code',
              'red_code', 'cusip', 'isin', 'loanx_id', 'bloomberg_code', 'underlying_bloomberg_code',
              'security_type', 'security_sub_type', 'risk_asset_class_1', 'reference_index',
              'risk_direction', 'position_currency', 'exposure_currency_code',
              'issuer_ult_gics_industry_sector', 'issuer_ult_gics_sub_industry',
              'final_rating', 'lien_type']

GBY_FIELDS_RESTRICTED = ['analyst_gcc_current']

sort_key = lambda x: [abs(x_) if isinstance(x_, numbers.Number) and pd.notnull(x_) else -99999 for x_ in x]


def get_gby_fields(groups):
    groups = groups or ''
    APPROVED_GROUPS = ['app_Dash_Admin', 'app_Dash_Trading']
    if any([x in APPROVED_GROUPS for x in groups]):
        return GBY_FIELDS + GBY_FIELDS_RESTRICTED
    return GBY_FIELDS


def _get_dd_options(fields):
    return {f: dfhelpers.ui_column_name(f) for f in fields}


def create_static_layout(fromdt=None, todt=None, rm=None, gby=None, filtercolumns=None, filtervalues=None,
                         is_diff=None, keep_contribution_column=None, user_settings=None, groups=None):
    fromdt = fromdt or dtutil.dateadd(dtutil.today(), '-3m-1b')
    todt = todt or dtutil.dateadd(dtutil.today(), '-1b')
    dt = get_datepicker_control(FROM_DATE_CONTROL_ID, dt=fromdt, disabled=False)
    fromdt_control = get_labelled_component('From Date:', dt)

    dt = get_datepicker_control(TO_DATE_CONTROL_ID, dt=todt, disabled=False)
    todt_control = get_labelled_component('To Date:', dt)

    rm_dd = get_labelled_component('Risk Metrics:',
                                   pivot_page_helpers.create_dropdown(
                                       {'type': ui_config.SAVE_CONFIG_INPUT_TYPE, 'index': RISKMETRICS_DD_ID},
                                       _get_dd_options(RISMETRIC_FIELDS),
                                       selecteditems=ui_config.get_control_value(RISKMETRICS_DD_ID, user_settings,
                                                                                 default_value=rm),
                                       style=dict(width='80%')))
    gby_dd = get_labelled_component('Grouped By:',
                                    pivot_page_helpers.create_dropdown(
                                        {'type': ui_config.SAVE_CONFIG_INPUT_TYPE, 'index': GBY_DD_ID},
                                        _get_dd_options(get_gby_fields(groups)),
                                        selecteditems=ui_config.get_control_value(GBY_DD_ID,
                                                                                  user_settings,
                                                                                  default_value=gby),
                                        style=dict(width='80%')))
    diff_cb = get_labelled_component('Changes:',
                                     dcc.Checklist(id={'type': ui_config.SAVE_CONFIG_INPUT_TYPE, 'index': DIFF_CB_ID},
                                                   options={'diff': ''},
                                                   value=ui_config.get_control_value(DIFF_CB_ID,
                                                                                     user_settings,
                                                                                     default_value=is_diff or [])))
    normalize_fundwt_cb = get_labelled_component('Aggregate as Contribution:',
                                                 dcc.Checklist(id={'type': ui_config.SAVE_CONFIG_INPUT_TYPE,
                                                                   'index': KEEP_VALUES_AS_CONTRIBUTION},
                                                               options={'keep_contribution_column': ''},
                                                               value=ui_config.get_control_value(
                                                                   KEEP_VALUES_AS_CONTRIBUTION,
                                                                   user_settings,
                                                                   default_value=keep_contribution_column or [])))

    filtercolumns_overriden = []
    filtervalues_overriden = []
    for i, fc, fv in itertools.zip_longest(range(MAX_FILTER_ROWS), filtercolumns or [], filtervalues or [],
                                           fillvalue=None):
        filtercolumns_overriden.append(ui_config.get_control_value(f'{i}-{pivot_page_helpers.FILTER_COLUMN_ID}',
                                                                   user_settings,
                                                                   default_value=fc))
        filtervalues_overriden.append(ui_config.get_control_value(f'{i}-{pivot_page_helpers.FILTER_VALUE_ID}',
                                                                  user_settings,
                                                                  default_value=fv))

    filters_dd = pivot_page_helpers.get_filter_area(ui_config.SAVE_CONFIG_INPUT_TYPE, ui_config.SAVE_CONFIG_INPUT_TYPE,
                                                    _get_dd_options(get_gby_fields(groups)), MAX_FILTER_ROWS,
                                                    selectedcolumns=filtercolumns_overriden,
                                                    selectedcolumnvalues=filtervalues_overriden)

    submit_btn = html.Div([html.Button('Search', id=RISK_QUERY_SUBMIT_ID, className='submit-button')])

    return html.Div([html.Br(), fromdt_control, todt_control,
                     rm_dd,
                     gby_dd,
                     diff_cb,
                     normalize_fundwt_cb,
                     html.H6("Add Filters:", className='subtitle'),
                     filters_dd,
                     html.Br(),
                     submit_btn, ], className='fund_filter')


def create_dynamic_layout():
    risk_query_results = html.Div(id=RISK_QUERY_RESULT_ID, children=[])
    return html.Div([risk_query_results], className='page')


def adjust_fig(fig, title):
    fig.update_layout(title_text=f'<span style="font-size: 12px;font-weight: bold;">{title}</span>',
                      title_x=0.35,
                      autosize=False,
                      width=900,
                      height=500, )


def create_bar_chart(df, title):
    logging.info(f'creating bar chart for: {df.shape} {title}')
    col_plots = []
    for c in df.columns:
        col_plots.append(go.Bar(x=df.index.to_numpy(), y=df[c].to_numpy(), name=str(c)))
    fig = go.Figure(data=col_plots)
    adjust_fig(fig, title)
    return html.Div(dcc.Graph(figure=fig))


def create_line_chart(df, title, first_line_ds=None, base_line_width=3):
    logging.info(f'creating line chart for: {df.shape} {title}')
    fig = make_subplots(specs=[[{'secondary_y': False}]])
    start_i = 0

    if first_line_ds is None:
        start_i = 1
        col = df.columns[0]
        fig.add_trace(go.Scatter(x=df.index.to_numpy(), y=df[col],
                                 line=dict(color="#000000", width=base_line_width),
                                 name=str(col)))
    else:
        fig.add_trace(go.Scatter(x=first_line_ds.index.to_numpy(), y=first_line_ds,
                                 line=dict(color="#000000", width=base_line_width),
                                 name='ALL'))

    for i, col in enumerate(df.columns[start_i:]):
        fig.add_trace(go.Scatter(x=df.index.to_numpy(), y=df[col], line=dict(width=1), name=str(col)))

    adjust_fig(fig, title)
    return html.Div(dcc.Graph(figure=fig))


def create_risk_metric_chart(df, gby, cols, diff_df, dt_df, fromdt, todt):
    logging.info(f'creating charts for: {df.shape} {gby} {cols}')
    num_dts = len(df['asof_date'].unique())
    num_dims = len(df[gby].drop_duplicates())
    title_post = ''
    if num_dims > MAX_CHART_DIMS:
        title_post = f' ( Top {MAX_CHART_DIMS} / {num_dims} {str(gby)} observations )'

    def get_df(df, rm):
        df = df.set_index(gby)
        if num_dims > MAX_CHART_DIMS:
            df_first = df
            if 'asof_date' in df:
                df_first = df_first[df_first['asof_date'] == df_first['asof_date'].max()]
            df_first = df_first.sort_values(rm, key=sort_key, ascending=False)
            df = df.loc[df_first.head(MAX_CHART_DIMS).index, :]

        return df.reset_index()

    df = df.sort_values(['asof_date'] + cols, ascending=[False] + [False] * len(cols), key=sort_key)

    chart_kids_by_rm = {}
    for rm in cols:
        chart_kids_by_rm[rm] = []
    if num_dts == 1:
        for rm in cols:
            chart_df = get_df(df, rm)
            chart_df = chart_df.set_index(gby)[[rm]] \
                .sort_values(rm, key=sort_key, ascending=False)
            chart_kids_by_rm[rm].append(create_bar_chart(chart_df.T, dfhelpers.ui_column_name(rm) + title_post))
    elif num_dts > 1:
        dt_df.set_index('asof_date', inplace=True)
        for rm in cols:
            chart_df = get_df(df, rm)
            chart_df = pd.pivot_table(chart_df, index='asof_date', columns=gby, values=rm, aggfunc='first')
            if len(chart_df) > 0:
                first_line_ds = None
                if len(chart_df.columns) > 1: first_line_ds = dt_df[rm]
                chart_df = chart_df.sort_values(by=chart_df.index.max(),
                                                ascending=False,
                                                axis=1,
                                                key=sort_key)
                chart_kids_by_rm[rm].append(
                    create_line_chart(chart_df, dfhelpers.ui_column_name(rm) + title_post, first_line_ds))

            if len(diff_df) > 0:
                chart_df = get_df(diff_df, rm)
                if len(chart_df) > 0:
                    chart_df = chart_df.set_index(gby)[[rm]].sort_values(rm, key=sort_key, ascending=False)
                    chart_kids_by_rm[rm].append(create_bar_chart(chart_df.T,
                                                                 dfhelpers.ui_column_name(rm) +
                                                                 f' Change between {fromdt} and {todt}' +
                                                                 title_post))

    chart_kids_tabs_list = []
    for rm in cols:
        chart_kids_tabs_list.append(dcc.Tab(label=dfhelpers.ui_column_name(rm), children=chart_kids_by_rm[rm]))

    chart_kids_tabs = dcc.Tabs(chart_kids_tabs_list)
    logging.info(f'completed charts for: {df.shape} {gby} {cols}')

    return html.Div(children=chart_kids_tabs)


def get_diff_df(df, is_diff, gby, cols):
    diff_df = pd.DataFrame()
    if len(is_diff or []) > 0:
        df['asof_date'] = pd.to_datetime(df['asof_date'], errors='coerce', format='mixed')
        t1 = df['asof_date'].min()
        t2 = df['asof_date'].max()
        if (t1 != t2):
            df_t1 = df[df['asof_date'] == t1] \
                .set_index(gby) \
                .assign(change_type=1) \
                .drop(columns='asof_date')
            df_t2 = df[df['asof_date'] == t2] \
                .set_index(gby) \
                .assign(change_type=1) \
                .drop(columns='asof_date')
            all_indices = set(df_t1.index.tolist()) | set(df_t2.index.tolist())
            diff_df = (df_t2.reindex(all_indices).fillna(0) -
                       df_t1.reindex(all_indices).fillna(0)) \
                .reset_index() \
                .sort_values(cols, key=sort_key, ascending=False)
            diff_df = diff_df[['change_type'] + [x for x in diff_df.columns if x not in ('change_type',)]]
    return diff_df


def get_diff_html(diff_df, fromdt, todt):
    div_kids = []
    if len(diff_df) > 0:
        added_rows = len(diff_df[diff_df['change_type'] > 0])
        deleted_rows = len(diff_df[diff_df['change_type'] < 0])
        changed_rows = len(diff_df[diff_df['change_type'] == 0])
        div_kids.append(html.Br())
        div_kids.append(html.Br())
        div_kids.append(html.H6(f"Changes between {fromdt} and {todt}: {changed_rows} changed rows, "
                                f"{added_rows} new rows "
                                f"and {deleted_rows} deleted rows.", className='subtitle'))
        div_kids.append(make_dash_table(diff_df.rename(columns=
                                                       {x: dfhelpers.ui_column_name(x) for x in diff_df.columns}),
                                        ignore_sort=True,
                                        page_size=10))
    diff_html = html.Div(children=div_kids)
    return diff_html


def create_result_layout(fromdt, todt, cols, gby, filters, is_diff, keep_contribution_column):
    logging.info(
        f"query request for: {fromdt}, {todt}, {cols}, {gby}, {filters}, {is_diff}, {keep_contribution_column}")
    if cols is None or gby is None or len(cols) == 0 or len(gby) == 0:
        return html.Div()
    keep_contribution_column = len(keep_contribution_column or []) > 0
    df = get_query_results(fromdt, todt, cols, gby, filters, keep_contribution_column)
    dt_df = get_query_results(fromdt, todt, cols, [], filters, keep_contribution_column)
    summ_df = get_query_results(fromdt, todt, cols, gby, filters, keep_contribution_column, summarize=True)
    for col in df.columns:
        if col not in summ_df.columns:
            if 'asof_date' == col:
                if fromdt == todt:
                    summ_df[col] = fromdt
                else:
                    summ_df[col] = f'{fromdt} to {todt}'
            else:
                summ_df[col] = 'Summary'

    summ_df = summ_df[df.columns]
    diff_df = get_diff_df(df, is_diff, gby, cols)
    result_html = html.Div(children=
                           [html.Br(),
                            html.H6(f"Risk Query Results: {len(df)} rows", className='subtitle'),
                            make_dash_table(df.rename(columns={x: dfhelpers.ui_column_name(x) for x in df.columns}),
                                            ignore_sort=True,
                                            page_size=10),
                            html.Br(),
                            html.H6(f"Summary:", className='subtitle'),
                            make_dash_table(summ_df.rename(
                                columns={x: dfhelpers.ui_column_name(x) for x in summ_df.columns}),
                                filter_action='none'),
                            get_diff_html(diff_df, fromdt, todt),
                            html.Br(),
                            html.H6(f"Charts by risk metrics:", className='subtitle'),
                            create_risk_metric_chart(df, gby, cols, diff_df, dt_df, fromdt, todt)],
                           className='fund_filter')
    logging.info(f"query request completed for: {fromdt}, {todt}, {cols}, {gby}, {filters}, {is_diff}")
    return result_html


def _create_result_html(fromdt, todt, cols, gby, filtercolumns, filtervalues, is_diff, keep_contribution_column):
    filters = {}

    def _update_f(f):
        fl = []
        f = f.strip()

        is_like = True
        if f[:1] == '=':
            f = f[1:]
            is_like = False
        elif f[:2] == '!=':
            f = '!' + f[2:]
            is_like = False

        for x in f.split(','):
            x = x.strip()
            if len(x) > 0:
                if is_like:
                    if x[0] == '!':
                        x = '!%' + x[1:] + '%'
                    else:
                        x = '%' + x + '%'
                fl.append(x)

        return fl

    if filtercolumns is not None and filtervalues is not None:
        filters = {c: _update_f(f) for c, f in zip(filtercolumns, filtervalues) if not ((c is None) or (f is None))}
    return create_result_layout(fromdt, todt, cols, gby, filters, is_diff, keep_contribution_column)


def check_keep_contribution(gby):
    if 'fund_entity_code' in (gby or []):
        return ['keep_contribution_column']
    return []


@callback(Output({'type': ui_config.SAVE_CONFIG_INPUT_TYPE, 'index': KEEP_VALUES_AS_CONTRIBUTION}, "value"),
          [Input({'type': ui_config.SAVE_CONFIG_INPUT_TYPE, 'index': GBY_DD_ID}, 'value')])
def on_groupby_select(gby):
    return check_keep_contribution(gby)


@callback(Output(RISK_QUERY_RESULT_ID, "children"),
          [Input(CHG_STORE_ID, 'modified_timestamp'),
           Input(RISK_QUERY_SUBMIT_ID, "n_clicks"),
           State(FROM_DATE_CONTROL_ID, 'date'),
           State(TO_DATE_CONTROL_ID, 'date'),
           State({'type': ui_config.SAVE_CONFIG_INPUT_TYPE, 'index': RISKMETRICS_DD_ID}, 'value'),
           State({'type': ui_config.SAVE_CONFIG_INPUT_TYPE, 'index': GBY_DD_ID}, 'value'),
           State({'type': ui_config.SAVE_CONFIG_INPUT_TYPE, 'index': ALL}, 'id'),
           State({'type': ui_config.SAVE_CONFIG_INPUT_TYPE, 'index': ALL}, 'value'),
           State({'type': ui_config.SAVE_CONFIG_INPUT_TYPE, 'index': DIFF_CB_ID}, 'value'),
           State({'type': ui_config.SAVE_CONFIG_INPUT_TYPE, 'index': KEEP_VALUES_AS_CONTRIBUTION}, 'value')])
def on_risk_query_submit(ts, n_clicks, fromdt, todt, cols, gby, all_ids, all_values, is_diff,
                         keep_contribution_column):
    if not n_clicks:
        keep_contribution_column = check_keep_contribution(gby)
    val_dict = {}
    for k, v in zip(all_ids, all_values):
        val_dict[k['index']] = v

    filtercolumns = []
    filtervalues = []
    for i in range(MAX_FILTER_ROWS):
        fc = val_dict.get(f'{i}-{pivot_page_helpers.FILTER_COLUMN_ID}', None)
        fv = val_dict.get(f'{i}-{pivot_page_helpers.FILTER_VALUE_ID}', None)
        if not ((fc is None) or (fv is None)):
            filtercolumns.append(fc)
            filtervalues.append(fv)

    logging.info(f'on risk query submit: {fromdt}, {todt}, {cols}, {gby}, {filtercolumns}, {filtervalues}, '
                 f'{is_diff}, {keep_contribution_column}')
    result_html = _create_result_html(fromdt, todt, cols, gby, filtercolumns, filtervalues,
                                      is_diff, keep_contribution_column)
    return [result_html]
