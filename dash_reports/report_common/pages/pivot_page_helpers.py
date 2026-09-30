from _collections import OrderedDict

import dash_core_components as dcc
import dash_html_components as html
import pandas as pd
from dash import callback, Input, Output, State, ALL

from common.utils.logutil import *
from dash_reports.report_common.data.config import *
from dash_reports.report_common.data.data_model import datastore
from dash_reports.report_common.utils.combos import get_labelled_component
from dash_reports.report_common.utils.components import make_dash_table
from dash_reports.report_common.utils.dfhelpers import filter_df

PIVOT_AGGFUNCS_CONTROL_ID = 'aggfunc-checklists'
PIVOT_AGGFUNC_CONTROL_ID = 'aggfunc-checklist'
PIVOT_AGGFUNC_COLLAPSE_ID = 'aggfunc-collapse'
PIVOT_AGGFUNC_COLLAPSE_SUBMIT_ID = 'aggfunc-collapse-submit'

PIVOT_PAGEREF_CONTROL_ID = 'pivot-page-ref'
PIVOT_SUBMIT_CONTROL_ID = 'pivot-submit'

PIVOT_TABLE_CONTROL_ID = 'pivot-table'
PIVOT_TABLEROWS_CONTROL_ID = 'pivot-rows'
PIVOT_TABLECOLUMNS_CONTROL_ID = 'pivot-columns'
PIVOT_TABLEVALUES_CONTROL_ID = 'pivot-values'
PIVOT_AGGFUNC_DEFAULT = 'pivot-aggfunc-default'

FILTER_COLUMNS_CONTROL_TYPEID = 'filter-columns-group'
FILTER_VALUES_CONTROL_TYPEID = 'filter-values-group'
FILTER_COLUMN_ID = 'filter-column'
FILTER_VALUE_ID = 'filter-value'


def create_dropdown(ddid, options, selecteditems=None, multi=True, **ddargs):
    if not isinstance(options, dict):
        options = {x: x for x in options}
    ddargs.update({} if selecteditems is None else dict(value=selecteditems))
    dd = dcc.Dropdown(id=ddid, options=[{'value': k, 'label': v} for k, v in options.items()], multi=multi, **ddargs)
    return dd


def _get_drop_down_area(ddid, label, ddvalues, selectedvalues, multi=True):
    return get_labelled_component(label,
                                  create_dropdown(ddid, ddvalues, selectedvalues, multi=multi, ))


def _get_filter_search_box(inputid, value=None):
    return dcc.Input(id=inputid, type='search', placeholder=f"search", className='input', value=value)


def get_filter_area(col_type_id, input_type_id, filtercolumns, filtercount, selectedcolumns=None,
                    selectedcolumnvalues=None):
    filtercontrols = []
    selectedcolumns = selectedcolumns or []
    selectedcolumnvalues = selectedcolumnvalues or []
    selectedcolumns.extend([None] * (filtercount - len(selectedcolumns)))
    selectedcolumnvalues.extend([None] * (filtercount - len(selectedcolumnvalues)))
    for filterid, (selectedcolumn, selectedcolumnvalue) in enumerate(zip(selectedcolumns, selectedcolumnvalues)):
        ddid = {'type': col_type_id, 'index': f"{filterid}-{FILTER_COLUMN_ID}"}
        inputid = {'type': input_type_id, 'index': f"{filterid}-{FILTER_VALUE_ID}"}
        filtercontrols += [html.Div(style=dict(display='flex'),
                                    children=[html.Div(
                                        [create_dropdown(ddid, filtercolumns, selectedcolumn, multi=False,
                                                         className='subtitle-without-padding')],
                                        style=dict(width='25%')),
                                        html.Div([_get_filter_search_box(inputid, value=selectedcolumnvalue)],
                                                 style=dict(width='100%'))])]
    return html.Div(filtercontrols)


def get_pivot_dropdowns(cols, selectedrows=None, selectedcolumns=None, selectedvalues=None, aggfunc_default=None):
    return html.Div(id='pivot-inputs',
                    children=[_get_drop_down_area(PIVOT_TABLEROWS_CONTROL_ID, "Select Pivot Rows:",
                                                  cols, selectedrows),
                              _get_drop_down_area(PIVOT_TABLECOLUMNS_CONTROL_ID, "Select Pivot Columns:",
                                                  cols, selectedcolumns),
                              _get_drop_down_area(PIVOT_TABLEVALUES_CONTROL_ID, "Select Pivot Values:",
                                                  cols, selectedvalues),
                              dcc.Store(PIVOT_AGGFUNC_DEFAULT, data=aggfunc_default or 'sum')
                              ])


def create_aggfunc_checkboxes(value_columns, default_value):
    aggfuncs = PIVOT_AGGFUNCS.copy()
    if len(value_columns or []) == 0:
        return html.Div()

    checklists = {}
    divchildren = []
    for val in value_columns:
        logging.debug(f"adding checklist for {val}")
        checklists[val] = dcc.Checklist(id={'type': PIVOT_AGGFUNC_CONTROL_ID, 'index': f"{val}-checklist"},
                                        options=[{'label': x, 'value': x} for x in aggfuncs],
                                        value=[default_value],
                                        className='checkbox',
                                        labelStyle={'display': 'inline'})
        divchildren += [html.Div(style=dict(display='flex'),
                                 children=[html.Div([html.Label("{:<30}:".format(val), className='subtitle')],
                                                    style=dict(width='300%')),
                                           html.Div([checklists[val]]), ])]

    return html.Div(children=divchildren, style={'display': 'inline-block'})


def create_pivot_table_control(pageref, rownames=None, colnames=None, valuenames=None, aggfunc=None, filters=None,
                               fixed_columns=None):
    rownames = rownames or []
    colnames = colnames or []
    valuenames = valuenames or []
    aggfunc = aggfunc or []
    aggfunclist = []
    logging.info(f"creating pivot for: {pageref}, {rownames}, {colnames}, {valuenames}, {aggfunc}")
    getdf_func = datastore.get_app_key(pageref + '_func')
    kwargs = datastore.get_app_key(pageref + '_params')
    page_size = datastore.get_app_key(pageref + '_page_size')
    df = getdf_func(**kwargs)
    logging.info(f"pivot df shape: {df.shape}")
    df = filter_df(df, filters)
    if not (np.any([len(valuenames) == 0, len(aggfunc) == 0]) or np.all([len(rownames) == 0, len(colnames) == 0])):
        if len(rownames) == 0:
            rownames = colnames
            colnames = []

        for vc in valuenames:
            df.loc[:, vc] = pd.to_numeric(df[vc], errors='coerce')
        df = df.groupby(rownames + colnames, dropna=False)[valuenames].agg(aggfunc).unstack(colnames)
        sorted_columns = [x for x in df.columns]
        sorted_columns = sorted(sorted_columns,
                                key=lambda tup: (PIVOT_AGGFUNCS.index(tup[1]) if tup[1] in PIVOT_AGGFUNCS else -1,
                                                 valuenames.index(tup[0]) if tup[0] in valuenames else -1)
                                )
        df = df[sorted_columns].reset_index()
        aggfunclist = list(OrderedDict.fromkeys([tup[1] for tup in sorted_columns]))
        if len(valuenames) > 1 or len(rownames) > 1:
            df.columns = df.columns.swaplevel(0, 1)
    else:
        if len(df) > MAX_ROWS_DISPLAY or len(df.columns) > MAX_COLS_DISPLAY:
            logging.info(f"pivot df: {df.shape}, "
                         f"truncating to max: {(MAX_ROWS_DISPLAY, MAX_COLS_DISPLAY)}; "
                         f"web page performance issue")
            df = df.head(MAX_ROWS_DISPLAY)
            df = df[df.columns[:MAX_COLS_DISPLAY]]

    pivotdiv = html.Div(children=[html.H6(f"{pageref.split('_')[0]}:", className='subtitle'),
                                  make_dash_table(df, aggfuncs=aggfunclist,
                                                  sortby_cols=df.columns[:len(rownames)],
                                                  row_total_columns=df.columns[:len(rownames)],
                                                  add_total_by_columns=False,
                                                  page_size=page_size,
                                                  fixed_columns=min(5, fixed_columns or len(rownames))
                                                  )
                                  ])

    logging.info(f"completed pivot for: {pageref}, {rownames}, {colnames}, {valuenames}, {aggfunc}")
    return pivotdiv


def get_aggfunc(ids, values):
    cols = [x['index'].replace('-checklist', '') for x in ids]
    aggfunc = OrderedDict()
    for k, v in zip(cols, values):
        aggfunc[k] = v
    return aggfunc


def create_pivot_controls(hidecontrols,
                          pageref,
                          getdf_func,
                          rows=None,
                          columns=None,
                          values=None,
                          aggfunc=None,
                          aggfunc_default=None,
                          filtercolumns=None,
                          filtervalues=None,
                          page_size=25,
                          before_pivot_div=None,
                          after_pivot_div=None,
                          fixed_columns=None,
                          **getdf_args):
    pageref = f"{pageref}_{str(getdf_args)}"
    datastore.set_app_key(pageref + '_func', getdf_func)
    datastore.set_app_key(pageref + '_params', getdf_args)
    datastore.set_app_key(pageref + '_page_size', page_size)
    df = getdf_func(**getdf_args)
    cols = list(df.columns)
    filters = {}
    if not ((filtervalues is None) or (filtercolumns is None)):
        filters = {c: f for c, f in zip(filtercolumns, filtervalues) if not ((c is None) or (f is None))}

    hidecontrols_style = {} if not hidecontrols else {'display': 'None'}
    filters_inputs = get_filter_area(FILTER_COLUMNS_CONTROL_TYPEID, FILTER_VALUES_CONTROL_TYPEID,
                                     df.columns, 3, filtercolumns, filtervalues)
    pivot_dropdowns = get_pivot_dropdowns(cols, rows, columns, values, aggfunc_default)
    pivot_table_control = create_pivot_table_control(pageref, rows, columns, values, aggfunc, filters, fixed_columns)
    pivot_select_div_kids = html.Div(children=[dcc.Input(style={'display': 'None'},
                                                         id=PIVOT_PAGEREF_CONTROL_ID,
                                                         value=pageref),
                                               html.Br(),
                                               html.Label('Filter Inputs:',
                                                          className='subtitlebold',
                                                          style=dict(width='500px')),
                                               filters_inputs,
                                               html.Br(),
                                               html.Label('Pivot Inputs:',
                                                          className='subtitlebold',
                                                          style=dict(width='500px')),
                                               pivot_dropdowns,
                                               html.Div([html.Button('Refresh Table',
                                                                     id=PIVOT_SUBMIT_CONTROL_ID,
                                                                     className='submit-button')]),
                                               html.Br(),
                                               html.Div(id=PIVOT_AGGFUNCS_CONTROL_ID)])
    pivot_collapse_control = html.Div(
        [html.Button('Pivot Controls', id=PIVOT_AGGFUNC_COLLAPSE_SUBMIT_ID, className='submit-button', ),
         html.Div(children=pivot_select_div_kids, id=PIVOT_AGGFUNC_COLLAPSE_ID, hidden=True), ])
    return html.Div(id='pivot-table-container',
                    children=html.Div(children=[html.Div(id='pivot-table-controls',
                                                         className='fund_filter',
                                                         style=hidecontrols_style,
                                                         children=[pivot_collapse_control]),

                                                html.Div(id='pivot-table-page',
                                                         children=[html.Br(),
                                                                   html.Div(children=before_pivot_div or []),
                                                                   html.Br(),
                                                                   html.Div(id=PIVOT_TABLE_CONTROL_ID,
                                                                            children=[pivot_table_control],
                                                                            className='fund_filter'),
                                                                   html.Br(),
                                                                   html.Div(children=after_pivot_div or []),
                                                                   ],

                                                         className='page')]))


@callback([Output(PIVOT_TABLE_CONTROL_ID, "children"), ],
          [Input(PIVOT_SUBMIT_CONTROL_ID, 'n_clicks'), ],
          [State(PIVOT_PAGEREF_CONTROL_ID, 'value'),
           State(PIVOT_TABLEROWS_CONTROL_ID, 'value'),
           State(PIVOT_TABLECOLUMNS_CONTROL_ID, 'value'),
           State({'type': PIVOT_AGGFUNC_CONTROL_ID, 'index': ALL}, 'id'),
           State({'type': PIVOT_AGGFUNC_CONTROL_ID, 'index': ALL}, 'value'),
           State({'type': FILTER_COLUMNS_CONTROL_TYPEID, 'index': ALL}, 'value'),
           State({'type': FILTER_VALUES_CONTROL_TYPEID, 'index': ALL}, 'value'),
           State(PIVOT_TABLE_CONTROL_ID, 'children'), ])
def on_pivotsubmit(n_clicks, pageref, rownames, columnnames,
                   checklistids, checklistvalues,
                   filtercolumns, filtervalues,
                   currentpivot):
    if len(checklistvalues) == 0:
        return [currentpivot]
    aggfunc = get_aggfunc(checklistids, checklistvalues)
    filters = {c: f for c, f in zip(filtercolumns, filtervalues) if not ((c is None) or (f is None))}
    valuenames = [k for k, _ in aggfunc.items()]
    return [create_pivot_table_control(pageref, rownames, columnnames, valuenames,
                                       aggfunc, filters)]


@callback([Output(PIVOT_AGGFUNCS_CONTROL_ID, "children"), ],
          [Input(PIVOT_TABLEVALUES_CONTROL_ID, 'value'),
           State(PIVOT_AGGFUNC_DEFAULT, 'data'), ]
          )
def on_pivotvalues_update(valuecolumns, aggfunc_default):
    return [create_aggfunc_checkboxes(valuecolumns, aggfunc_default)]


@callback(
    Output(PIVOT_AGGFUNC_COLLAPSE_ID, "hidden"),
    [Input(PIVOT_AGGFUNC_COLLAPSE_SUBMIT_ID, "n_clicks")],
    [State(PIVOT_AGGFUNC_COLLAPSE_ID, "hidden")],
)
def on_collapse(n, is_open):
    if n:
        return not is_open
    return is_open
