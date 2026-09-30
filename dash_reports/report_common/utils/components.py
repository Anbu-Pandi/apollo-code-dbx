import datetime
import logging
import traceback
import uuid

import dash_tabulator
import dash_core_components as dcc
import dash_table
from dash import html
import numpy as np
import pandas as pd
from dash_table.Format import Format, Symbol, Scheme, Sign
from pandas.api.types import is_string_dtype

from dash_reports.report_common.data.config import FUND_DISPLAY_ORDER, PIVOT_AGGFUNCS
from dash_reports.report_common.data.config import MAX_ROWS_DISPLAY


def create_link(base_link, page, link_des, query=None, selected_page=None):
    selected_page = selected_page or '.'
    query = f'?{query}' if not query is None else ''
    return dcc.Link(link_des, href=f"/{base_link}/{page}{query}",
                    className="tabselected" if page.split('/')[0].startswith(selected_page.split('/')[0]) else 'tab')


def get_datepicker_control(id, dt, disabled=True, style={"margin-right": "15px"}, max_date=None):
    max_date = max_date or datetime.date.today()
    return dcc.DatePickerSingle(id=id, month_format='MMM Do YYYY', placeholder='MMM Do YYYY', date=dt,
                                max_date_allowed=max_date,
                                disabled=disabled, style=style)


def _get_dfcol(df, col):
    return df[col].dropna().iloc[:MAX_ROWS_DISPLAY]


def map_dash_type(df, col):
    logging.info(f'mapping dashtype for col: {col}')
    DASHTYPES = ['datetime', 'text', 'numeric', 'any']
    dfcol = _get_dfcol(df, col)
    col_type = 'any'
    if dfcol.dtype in (int, np.int64, np.int32) or \
            dfcol.dtype in (float, np.float64, np.float32) or \
            (dfcol.dtype in (object,) and pd.to_numeric(dfcol, errors='coerce').notnull().all()):
        col_type = 'numeric'
    elif pd.to_datetime(dfcol, errors='coerce').notnull().all():
        col_type = 'datetime'
    elif is_string_dtype(dfcol):
        col_type = 'text'
    logging.info(f'mapped col: {col} to col_type: {col_type}')
    return col_type


def _dash_col_name(col):
    return " ".join([str(x) for x in col]) if isinstance(col, (list, tuple)) else str(col)


def is_dfcol_int(df, col):
    dfcol = pd.to_numeric(_get_dfcol(df, col), errors='coerce')
    is_greater_than_limit = (dfcol.dropna().abs().quantile(q=0.75) > 250)
    return is_greater_than_limit or dfcol.astype(float).apply(float.is_integer).all()


def _get_dash_col_format(df, type, col, precision_2_format=False, dynamic_num_format=False):
    numeric_args = dict(scheme=Scheme.default if dynamic_num_format else Scheme.fixed,
                        sign=Sign.negative, symbol=Symbol.no, symbol_suffix='', group=',')
    if precision_2_format:
        return Format(precision=2, **numeric_args)
    if dynamic_num_format:
        return Format(**numeric_args)
    dashcolname = _dash_col_name(col)
    if type == 'numeric' and (dashcolname[0] == '%' or ' % ' in dashcolname):
        return Format(precision=2, **numeric_args)
    elif type == 'numeric' and \
            not is_dfcol_int(df, col):
        return Format(precision=2, **numeric_args)
    elif type == 'numeric':
        return Format(precision=0, **numeric_args)


def _get_dash_column(df, col, col_type, editable=False, is_link=False, precision_2_format=False,
                     dynamic_num_format=False):
    coldict = {
        'name': col,
        'id': _dash_col_name(col),
        'type': col_type,
        'editable': editable,
        'hideable': True,
        'selectable': True,
    }
    format = _get_dash_col_format(df, coldict['type'], col, precision_2_format, dynamic_num_format)
    if format is not None:
        coldict.update({'format': format})
    if is_link:
        coldict.update({'presentation': 'markdown'})
    if col_type == 'text':
        coldict.update({'filter_options': {"case": "insensitive"}})
    return coldict


def _get_dash_data(df, col_types, dynamic_num_format):
    df.columns = [_dash_col_name(col) for col in df.columns]
    col_types.update({_dash_col_name(col): t for col, t in col_types.items()})
    for c in df.columns:
        if col_types[c] == 'numeric':
            df[c] = pd.to_numeric(df[c])
            if dynamic_num_format and (df[c].dtype != bool):
                logging.info(f'rounding column to two digits: {c}')
                df[c] = df[c].round(2)
    return df.to_dict('records')


def _get_num_col_style(df, col_types, num_col_width, txt_col_width,
                       iscolheader=True,
                       precision_2_format=False,
                       light_headers=[]):
    logging.info(f'creating numcol style for df: {df.shape}')
    increase_pct = 0.50

    col_styles = []
    dash_cols = {c: _get_dash_column(df, c, col_types[c], precision_2_format=precision_2_format,
                                     dynamic_num_format=False)
                 for c in df.columns}
    for c in df.columns:
        dashcol = dash_cols[c]
        if dashcol['type'] == 'numeric' and df[c].abs().max() > 1e6:
            colwidth = str(int(num_col_width.replace('px', '')) * (1 + increase_pct)) + 'px'
        elif dashcol['type'] == 'numeric':
            colwidth = num_col_width
        else:
            colwidth = txt_col_width

        col_styles += [
            {
                'if': {'column_id': dashcol['id']},
                'minWidth': colwidth,
                'width': colwidth,
                'maxWidth': colwidth,
                'textAlign': 'left' if iscolheader or dashcol['type'] != 'numeric' else 'right',
            }
        ]
        if iscolheader and (c in [_dash_col_name(x) for x in light_headers]):
            col_styles[-1].update({'backgroundColor': '#62c096', })

    return col_styles


def _get_total_style(df):
    total_styles = []
    if len(df) >= MAX_ROWS_DISPLAY:
        logging.info(f"df: {df.shape} exceeds {MAX_ROWS_DISPLAY}; skipping total formatting.")
        return total_styles

    for i, row in df.iterrows():
        if np.any([np.any([f"{aggfunc}_" in str(x) for aggfunc in PIVOT_AGGFUNCS]) for x in row]):
            total_styles += [
                {
                    'if': {
                        'row_index': i,
                    },
                    'fontWeight': 'bold',
                }
            ]
    return total_styles


def _get_negative_style(df, col_types):
    logging.info(f'creating negative style for df: {df.shape}')
    neg_styles = []
    for col in df.columns:
        col_type = col_types[col]
        if col_type == 'numeric':
            neg_styles += [
                {
                    'if': {
                        'filter_query': '{' + col + '} < 0',
                        'column_id': f'{col}'
                    },
                    'color': 'tomato',
                },
            ]
    return neg_styles


def get_ordered_df(df,
                   ordered_row_values=None,
                   sortby_cols=None,
                   orderd_column_values=None,
                   sortby_column_levels=None):
    logging.info(f'ordering df: {df.shape}')
    try:
        if sortby_cols is not None and len(sortby_cols) != 0:
            df = df_sort_rows_by_order(df, ordered_row_values or [], sortby_cols)

        if not orderd_column_values is None:
            df = df_sort_columns_by_order(df, orderd_column_values or [], sortby_column_levels)

        if sortby_cols is None or not np.any(['Fund Entity Code' in x for x in sortby_cols]):
            if isinstance(df.columns, pd.MultiIndex) \
                    and 'Fund Entity Code' in df.columns.names:
                df = df_sort_columns_by_order(df, [FUND_DISPLAY_ORDER], ['Fund Entity Code'])

            if np.any(['Fund Entity Code' in str(x) for x in df.columns.values]):
                fund_entity_col = list(df.columns.values[['Fund Entity Code' in str(x) for x in df.columns.values]])
                df = df_sort_rows_by_order(df, [FUND_DISPLAY_ORDER], fund_entity_col)

    except:
        logging.error(traceback.format_exc())
        return df

    return df


def _get_tooltip_data(df):
    logging.info(f'creating tooltip for df: {df.shape}')
    return [
        {
            column: {'value': str(value), 'type': 'markdown'}
            for column, value in row.items()
        } for row in df.head(MAX_ROWS_DISPLAY).to_dict('records')
    ]


def _get_oddrow_style():
    return [
        {
            'if': {'row_index': 'odd'},
            'backgroundColor': '#E9F4E9',
            'color': 'black'
        }
    ]


def get_header_style(width):
    return {
        'minWidth': width,
        'width': width,
        'maxWidth': width,
        'textAlign': 'left',
        'backgroundColor': '#3D9970',
        'fontWeight': 'bold',
        'whiteSpace': 'normal',
        'height': '50px',
        'textDecoration': 'underline',
        'textDecorationStyle': 'dotted',
    }


def _get_data_style(width):
    return {
        'minWidth': width,
        'width': width,
        'maxWidth': width,
        'overflow': 'hidden',
        'textOverflow': 'ellipsis',
        'textAlign': 'left',
    }


def add_total_to_df(df,
                    aggfuncs,
                    row_total_columns,
                    add_total_by_columns):
    logging.info(f'add totals for df: {df.shape}')
    try:
        if row_total_columns is not None:
            dftr = df_calc_row_totals(df, row_total_columns, aggfuncs)
            df = merge_row_totals(df, dftr)

        if add_total_by_columns:
            dftc = df_calc_column_totals(df)
            df = merge_column_total(df, dftc)
    except:
        logging.error(traceback.format_exc())
        return df

    return df


def _set_edit_flag(data, col):
    if isinstance(data, bool):
        edit = data
    elif isinstance(data, dict):
        if data['editable']:
            if col in data['non_editable_cols']:
                edit = False
            else:
                edit = True
        elif not data['editable']:
            edit = False
        else:
            edit = False

    return edit


def get_cell_style(custom_style):
    cell_style = {}
    cell_style.update(custom_style)
    return cell_style


_AG_GRID_AVAILABLE = None


def _ag_grid_available():
    """True if dash-ag-grid is installed. Cached; lazy so this module still
    imports fine when the package isn't present (nothing changes until it is)."""
    global _AG_GRID_AVAILABLE
    if _AG_GRID_AVAILABLE is None:
        try:
            import dash_ag_grid  # noqa: F401
            _AG_GRID_AVAILABLE = True
        except Exception:
            _AG_GRID_AVAILABLE = False
    return _AG_GRID_AVAILABLE


def _is_editable(editable):
    if isinstance(editable, dict):
        return bool(editable.get('editable'))
    return bool(editable)


def _use_ag_grid(editable):
    """Central switch: read-only tables render as themed AG Grid (fast + clean);
    editable grids stay on DataTable. Kill switch: DASH_USE_AG_GRID=0."""
    import os
    if os.environ.get('DASH_USE_AG_GRID', '1').strip().lower() in ('0', 'false', 'no', 'off'):
        return False
    if _is_editable(editable):
        return False
    return _ag_grid_available()


def _json_safe_records(df):
    """rowData that always serializes: NaN/NaT -> None, Timestamps -> str, numpy
    scalars -> python. Prevents 500s from non-JSON values (the old DataTable path
    coerced these; raw df.to_dict can emit NaN/Timestamp)."""
    import math
    out = df.copy()
    out.columns = [_dash_col_name(c) for c in out.columns]
    for c in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[c].dtype):
            out[c] = out[c].dt.strftime("%Y-%m-%d %H:%M").where(out[c].notna(), None)
    records = out.to_dict("records")
    for row in records:
        for k, v in row.items():
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                row[k] = None
            elif v is pd.NaT:
                row[k] = None
            elif isinstance(v, np.generic):
                row[k] = v.item()
    return records


def _ag_num_decimals(df, col, precision_2_format=False):
    """Decimals for a numeric column — reuses the existing value-based rule
    (is_dfcol_int): 0 dp for large-magnitude/integer columns, 2 dp otherwise;
    percentage-named columns always 2."""
    if precision_2_format:
        return 2
    name = _dash_col_name(col)
    if name[:1] == '%' or ' % ' in name:
        return 2
    try:
        return 0 if is_dfcol_int(df, col) else 2
    except Exception:
        return 2


_AG_MAX_ROWS = 50000   # client-side row-model guard; beyond this we cap + notify


def _ag_stable_id(id, df):
    """Stable grid id so sort/filter/scroll survive callback re-renders. Uses the
    caller id when given, else a hash of the column set (same table -> same id)."""
    if id:
        return str(id)
    import hashlib
    key = "|".join(str(c) for c in df.columns)
    return "genieag_" + hashlib.md5(key.encode()).hexdigest()[:10]


def _ag_totals_row(df, aggfuncs=None, row_total_columns=None, add_total_by_columns=False):
    """Clean bottom totals row for the pinned area (the old add_total_to_df only
    works on the pivoted multi-col structure we dropped). Aggregates numeric
    columns — 'sum' by default, per-column override via aggfuncs; row_total_columns
    limits which columns are totaled. Returns a JSON-safe [dict], or None."""
    if not (add_total_by_columns or row_total_columns or aggfuncs) or len(df) == 0:
        return None
    funcs = {_dash_col_name(k): v for k, v in (aggfuncs or {}).items()}
    numeric_cols = {c for c in df.columns if pd.api.types.is_numeric_dtype(df[c].dtype)}
    want = None
    if row_total_columns:
        rtc = row_total_columns if isinstance(row_total_columns, (list, tuple, set)) else [row_total_columns]
        want = {_dash_col_name(x) for x in rtc}

    row, labeled = {}, False
    for col in df.columns:
        name = _dash_col_name(col)
        if col in numeric_cols and (want is None or name in want):
            fn = funcs.get(name, 'sum')
            try:
                row[name] = float(getattr(df[col], fn)())
            except Exception:
                try:
                    row[name] = float(df[col].sum())
                except Exception:
                    row[name] = None
        elif not labeled:
            row[name] = 'Total'
            labeled = True
        else:
            row[name] = ''
    return _json_safe_records(pd.DataFrame([row]))


_AG_DEF_COL = {"sortable": True, "filter": True, "resizable": True, "floatingFilter": True,
               "minWidth": 110,
               "tooltipValueGetter": {"function": "String(params.value == null ? '' : params.value)"}}


def _ag_column_defs(df, link_columns=None, hidden_columns=None, precision_2_format=False):
    """Shared columnDefs (numbers formatted via d3, negatives red, links markdown)
    used by both the client-side and infinite (server-paged) grids."""
    link_cols = set(link_columns or [])
    hidden = set(hidden_columns or [])
    # d3 is bundled; only a `== null` guard (isNaN() isn't in the eval scope).
    def num_fmt(fd):
        return {"function": f"params.value == null ? '' : d3.format(',.{fd}f')(params.value)"}
    neg_red = {"function": "params.value < 0 ? {'color': '#C0392B'} : null"}
    defs = []
    for col in df.columns:
        name = _dash_col_name(col)
        cd = {"field": name, "headerName": name}
        if col in hidden:
            cd["hide"] = True
        if col in link_cols:
            cd["cellRenderer"] = "markdown"
        elif pd.api.types.is_numeric_dtype(df[col].dtype):
            cd["type"] = "numericColumn"
            cd["valueFormatter"] = num_fmt(_ag_num_decimals(df, col, precision_2_format))
            cd["cellStyle"] = neg_red
        defs.append(cd)
    return defs


def _ag_grid_table(df, page_size=15, link_columns=None, hidden_columns=None, id='',
                   precision_2_format=False, pinned_bottom=None):
    """Client-side AG Grid (for tables up to _AG_MAX_ROWS). Native sort/filter/
    resize/pin, Apollo theme, CSV export, pinned totals, virtualized rendering."""
    import dash_ag_grid as dag

    gid = _ag_stable_id(id, df)
    column_defs = _ag_column_defs(df, link_columns, hidden_columns, precision_2_format)
    n = len(df)
    ps = page_size or 15
    grid_options = {
        "pagination": True, "paginationPageSize": ps, "animateRows": True,
        "enableCellTextSelection": True, "ensureDomOrder": True,
        "enableBrowserTooltips": True, "tooltipShowDelay": 400,
    }
    style = {"width": "100%"}
    if n <= ps:
        grid_options["domLayout"] = "autoHeight"   # small: no empty space
    else:
        style["height"] = "620px"                   # else bounded -> virtualized
    if pinned_bottom:
        grid_options["pinnedBottomRowData"] = pinned_bottom

    grid = dag.AgGrid(
        id={"type": "genie-ag", "index": gid},
        rowData=_json_safe_records(df),
        columnDefs=column_defs,
        className="ag-theme-quartz ag-theme-genie",
        columnSize="responsiveSizeToFit", defaultColDef=_AG_DEF_COL,
        dashGridOptions=grid_options, csvExportParams={"fileName": f"{gid}.csv"},
        dangerously_allow_code=True, style=style,
    )
    toolbar = html.Div(
        html.Button("⭳ CSV", id={"type": "genie-ag-export", "index": gid}, n_clicks=0,
                    className="genie-grid-export"),
        className="genie-grid-toolbar")
    return html.Div([toolbar, grid], className="genie-grid-wrap")


def _ag_infinite_table(df, page_size=15, link_columns=None, hidden_columns=None, id='',
                       precision_2_format=False):
    """Server-paged AG Grid for very large data: materialize df to a temp Delta
    table once, then the grid pulls ~page blocks on scroll via the getRows
    callback (sort/filter pushed to DuckDB). Browser holds one block at a time."""
    import dash_ag_grid as dag
    from dash import dcc
    from dash_reports.report_common.utils import grid_cache

    gid = f"{_ag_stable_id(id, df)}_{uuid.uuid4().hex}"
    total = len(df)
    tbl, col_map, token = grid_cache.write_grid_cache(df, gid)
    column_defs = _ag_column_defs(df, link_columns, hidden_columns, precision_2_format)
    ps = page_size or 50

    grid = dag.AgGrid(
        id={"type": "genie-ag-inf", "index": gid},
        columnDefs=column_defs,
        rowModelType="infinite",
        className="ag-theme-quartz ag-theme-genie",
        columnSize="responsiveSizeToFit", defaultColDef=_AG_DEF_COL,
        dashGridOptions={"pagination": True, "paginationPageSize": ps,
                         "cacheBlockSize": ps, "maxBlocksInCache": 12, "rowBuffer": 0,
                         "enableCellTextSelection": True, "enableBrowserTooltips": True,
                         "tooltipShowDelay": 400},
        dangerously_allow_code=True, style={"height": "640px", "width": "100%"},
    )
    meta = dcc.Store(id={"type": "genie-ag-meta", "index": gid},
                     data={"tbl": tbl, "cols": col_map, "token": token})
    note = html.Div(f"{total:,} rows · server-paged (sort/filter run on the server)",
                    className="genie-note", style={"textAlign": "right"})
    return html.Div([note, grid, meta], className="genie-grid-wrap")


def make_dash_table(df,
                    ordered_row_values=None,
                    sortby_cols=None,
                    ordered_column_values=None,
                    sortby_column_levels=None,
                    aggfuncs=None,
                    row_total_columns=None,
                    add_total_by_columns=False,
                    page_size=10,
                    editable={'editable': False, 'non_editable_cols': []},
                    id='',
                    num_col_width='65px',
                    txt_col_width='130px',
                    link_columns=None,
                    data_styling=[],
                    conditional_tooltip=[],
                    precision_2_format=False,
                    dynamic_num_format=False,
                    fixed_columns=0,
                    ignore_sort=False,
                    filter_action='native',
                    cell_styling={},
                    light_headers=[],
                    hidden_columns=[]
                    ):
    logging.info(f'creating dash table for df: {df.shape}')
    if len(df) == 0:
        return html.Div(['No data to create table!'])

    # Central switch: read-only tables render as a fast, themed AG Grid (skips the
    # ordering / total / multi-column machinery below). Editable grids and the
    # no-package / kill-switch cases fall through to the legacy DataTable.
    if _use_ag_grid(editable):
        # AG Grid addresses cells by string field names, so MultiIndex/tuple columns
        # (pivots) would reach rowData as non-string dict keys and fail orjson.
        df = df.copy()
        df.columns = [_dash_col_name(c) for c in df.columns]
        # very large -> server-paged infinite grid (temp Delta table + block fetch);
        # otherwise client-side. No cropping either way.
        if len(df) > _AG_MAX_ROWS:
            try:
                return _ag_infinite_table(df, page_size=page_size, link_columns=link_columns,
                                          hidden_columns=hidden_columns, id=id,
                                          precision_2_format=precision_2_format)
            except Exception as exc:
                logging.warning("ag infinite grid failed, falling back to client-side head: %s", exc)
                df = df.head(_AG_MAX_ROWS)
        pinned = _ag_totals_row(df, aggfuncs, row_total_columns, add_total_by_columns)
        return _ag_grid_table(df, page_size=page_size, link_columns=link_columns,
                              hidden_columns=hidden_columns, id=id,
                              precision_2_format=precision_2_format, pinned_bottom=pinned)

    df = df.copy()
    if not ignore_sort:
        df = get_ordered_df(df, ordered_row_values, sortby_cols, ordered_column_values, sortby_column_levels)
    if len(df) > 100:
        add_total_by_columns = False
        row_total_columns = None
    df = add_total_to_df(df, aggfuncs, row_total_columns, add_total_by_columns).reset_index(drop=True)

    col_types = {col: map_dash_type(df, col) for col in df.columns}

    is_link = lambda x: (x in (link_columns or []))
    dash_columns = [_get_dash_column(df, col, col_types[col],
                                     _set_edit_flag(editable, col),
                                     is_link(col),
                                     precision_2_format=precision_2_format,
                                     dynamic_num_format=dynamic_num_format)
                    for col in df.columns.values]
    dash_data = _get_dash_data(df, col_types, dynamic_num_format)

    table_html = dash_table.DataTable(
        id=id,
        data=dash_data,
        export_format="xlsx",
        sort_action='native',
        filter_action=filter_action,
        page_action='native',
        page_size=page_size,
        merge_duplicate_headers=True,
        columns=dash_columns,
        tooltip_header={i: i for i in df.columns},
        tooltip_data=_get_tooltip_data(df),
        tooltip_duration=None,
        tooltip_conditional=conditional_tooltip,
        style_header=get_header_style(txt_col_width),
        style_header_conditional=_get_num_col_style(df, col_types, num_col_width, txt_col_width,
                                                    iscolheader=True,
                                                    light_headers=light_headers),
        style_data=_get_data_style(txt_col_width),
        style_data_conditional=_get_oddrow_style() +
                               _get_total_style(df) +
                               _get_negative_style(df, col_types) +
                               _get_num_col_style(df, col_types, num_col_width, txt_col_width,
                                                  iscolheader=False,
                                                  precision_2_format=precision_2_format) +
                               data_styling,
        style_cell=get_cell_style(cell_styling),
        style_table={'overflowX': 'auto'},
        style_filter={'backgroundColor': '#ffffe5'},
        editable=editable,
        markdown_options={"html": True},
        virtualization=len(df) > MAX_ROWS_DISPLAY,
        fill_width=False,
        fixed_rows={'headers': True, 'data': 0},
        fixed_columns={'headers': True, 'data': fixed_columns},
        hidden_columns=hidden_columns,
    )

    logging.info(f'created dash datatable for df: {df.shape} with columns: {col_types}')
    return table_html


def df_col(df, col):
    if isinstance(df.columns, pd.MultiIndex):
        if isinstance(col, tuple):
            return df[pd.MultiIndex.from_tuples([col])]
        else:
            return df[pd.MultiIndex.from_tuples(col)]
    else:
        return df[col]


def df_sort_rows_by_order(df, ordered_values, sortby_cols):
    if len(ordered_values) == 0:
        ordered_values = [[]] * len(sortby_cols)

    assert (len(sortby_cols) == len(ordered_values) or len(ordered_values) == 0), \
        f"lengths donot match: {len(ordered_values)} and {len(sortby_cols)}"

    for i, col in enumerate(sortby_cols):
        if (len(ordered_values[i]) == 0 and 'Fund Entity Code' in col):
            ordered_values[i] = FUND_DISPLAY_ORDER

        if len(ordered_values[i]) > 0:
            all_values = list(dict.fromkeys(df_col(df, col)))
            non_ordered_values = [x for x in all_values if x not in ordered_values[i]]
            all_ordered_values = ordered_values[i] + non_ordered_values
            df[f'_ordered_{col}'] = df[col].map(all_ordered_values.index)
        else:
            if df[col].dtype in (object,):
                df[f'_ordered_{col}'] = df[col].astype(str)
            else:
                df[f'_ordered_{col}'] = df[col]

    _ordered_cols = [f'_ordered_{col}' for col in sortby_cols]
    _ordered_cols = [x for x in df.columns if np.any([y in x for y in _ordered_cols])]
    df = df.sort_values(_ordered_cols).drop(_ordered_cols, axis=1)
    return df


def df_sort_columns_by_order(df, ordered_values, sortby_col_levels=None):
    if sortby_col_levels is None:
        all_cols = list(df.columns)
        ordered_values = [col for col in ordered_values if col in all_cols]
        non_ordered_cols = [col for col in all_cols if col not in ordered_values]
        all_ordered_cols = ordered_values + non_ordered_cols
        return df[all_ordered_cols]
    else:
        assert len(sortby_col_levels) == len(ordered_values), \
            f"lengths donot match: {len(ordered_values)} and {len(ordered_values)}"
        for i, col in enumerate(sortby_col_levels):
            all_cols = list(df.columns.get_level_values(col).unique())
            ordered_values[i] = [col for col in ordered_values[i] if col in all_cols]
            non_ordered_cols = [col for col in all_cols if col not in ordered_values[i]]
            all_ordered_cols = ordered_values[i] + non_ordered_cols
            df = df.reindex(all_ordered_cols, axis=1, level=col)
        return df


def df_calc_row_totals(df, total_columns, aggfuncs):
    total_columns = list(total_columns)
    dftotal = pd.DataFrame()
    for aggfunc in aggfuncs:
        groupby_cols = total_columns
        nongroupby_cols = []
        while (len(groupby_cols) > 0):
            nongroupby_cols = groupby_cols[-1:] + nongroupby_cols
            groupby_cols = groupby_cols[:-1]
            if len(groupby_cols) == 0:
                dfg = pd.DataFrame(df.agg(aggfunc)).T
            else:
                dfg = df.groupby(by=groupby_cols, as_index=False).agg(aggfunc)
            for ngc in nongroupby_cols:
                dfg[ngc] = f'{aggfunc}_{get_str_columnname(ngc)}'
            dftotal = pd.concat([dftotal, dfg[df.columns].reset_index(drop=True)])

    if len(dftotal) > 0:
        return dftotal


def _get_multiindex_columndetails(df, aggfuncset):
    groupby_columns_levels = [x for x in df.columns.names if x is not None]

    check_list_contains = lambda l, inlist: np.all([x in inlist for x in l if x != ''])
    aggfuncs_level = [i for i, x in enumerate(df.columns.names) if
                      (x is None and check_list_contains(df.columns.get_level_values(i).unique(), aggfuncset))]
    assert len(aggfuncs_level) == 1, "need aggfunc level to get columns total"
    aggfuncs_level = aggfuncs_level[0]
    aggfuncs = [x for x in df.columns.get_level_values(aggfuncs_level).unique() if x != '']

    values_level = [i for i, x in enumerate(df.columns.names)
                    if (x is None and i != aggfuncs_level)]
    assert len(values_level) == 1, "need value level to get columns total"
    values_level = values_level[0]
    from collections import OrderedDict
    values = list(OrderedDict.fromkeys([v for a, v in
                                        zip(df.columns.get_level_values(aggfuncs_level),
                                            df.columns.get_level_values(values_level))
                                        if a != '']))

    return groupby_columns_levels, values_level, aggfuncs_level, aggfuncs, values


def df_calc_column_totals(df):
    if not isinstance(df.columns, pd.MultiIndex):
        return None

    aggfuncset = PIVOT_AGGFUNCS.copy()
    groupby_columns_levels, values_level, aggfuncs_level, aggfuncs, values = \
        _get_multiindex_columndetails(df, aggfuncset)

    dftotal = pd.DataFrame()
    for value in values:
        dfv = df[df.columns[(df.columns.get_level_values(values_level) == value) | \
                            (df.columns.get_level_values(aggfuncs_level) != '')]]
        for aggfunc in aggfuncs:
            groupby_cols = groupby_columns_levels
            nongroupby_cols = []
            while (len(groupby_cols) > 0):
                nongroupby_cols = groupby_cols[-1:] + nongroupby_cols
                groupby_cols = groupby_cols[:-1]

                if len(groupby_cols) == 0:
                    dfg = pd.DataFrame(dfv.agg(aggfunc, axis=1))
                else:
                    dfg = dfv.groupby(by=groupby_cols, axis=1).agg(aggfunc)

                total_column_names = []
                for col in dfg.columns:
                    idx = [x for _, x in sorted([(aggfuncs_level, aggfunc), (values_level, value)])]
                    if len(groupby_cols) != 0:
                        idx += [str(col)] if isinstance(col, str) else list(col)
                    for ngc in nongroupby_cols:
                        idx += [f'{aggfunc}_ALL']
                    total_column_names += [tuple(idx)]

                dfg.columns = pd.MultiIndex.from_tuples(total_column_names)

                dftotal = pd.concat([dftotal, dfg], axis=1)

    if len(dftotal) > 0:
        dftotal.columns.names = df.columns.names
        return dftotal
    else:
        return None

    # Function to insert row in the dataframe


def insert_row(row_number, df, row_value):
    start_upper = 0
    end_upper = row_number
    start_lower = row_number
    end_lower = df.shape[0]
    upper_half = [*range(start_upper, end_upper, 1)]
    lower_half = [*range(start_lower, end_lower, 1)]
    lower_half = [x.__add__(1) for x in lower_half]
    index_ = upper_half + lower_half
    df.index = index_
    df.loc[row_number] = row_value
    df = df.sort_index()
    return df


def get_str_columnname(col):
    if type(col) == tuple:
        return ''.join([x.lower() for x in col])
    return col.lower()


def merge_row_totals(df, dfrow_total):
    if dfrow_total is None:
        return df

    total_columns = list(df.columns)[:2]
    for i in range(len(total_columns)):
        total_column = total_columns[i]
        column_to_left = total_columns[:i]
        flags = dfrow_total[total_column].astype(str).str.contains(get_str_columnname(total_column)).fillna(False)
        rows_to_insert = dfrow_total[flags]
        dfrow_total = dfrow_total[~flags]
        for (i, row_to_insert) in rows_to_insert.iterrows():
            flags = [True] * len(df)
            for c in column_to_left:
                flags &= df[c] == row_to_insert[c]
            insert_row_number = df[flags].index.max() + 1
            df = insert_row(insert_row_number, df, row_to_insert)
        if len(dfrow_total) == 0:
            break
    return df


def merge_column_total(df, dfcol_total):
    if dfcol_total is None:
        return df

    columns = list(df.columns)
    aggfuncset = PIVOT_AGGFUNCS.copy()
    groupby_columns_levels, values_level, aggfuncs_level, aggfuncs, values = \
        _get_multiindex_columndetails(df, aggfuncset)

    for value in values:
        dfv = dfcol_total[dfcol_total.columns[dfcol_total.columns.get_level_values(values_level) == value]]
        for aggfunc in aggfuncs:
            dfaf = dfv[dfv.columns[dfv.columns.get_level_values(aggfuncs_level) == aggfunc]]
            nongroupby_columns_levels = []
            for groupby_column in groupby_columns_levels:
                dfsubset = \
                    dfaf[
                        dfaf.columns[dfaf.columns.get_level_values(groupby_column).map(lambda x: x[-4:] == '_ALL')]]
                for idx in dfsubset.columns:
                    insertat = max([-1] + [i for (i, col) in enumerate(df.columns)
                                           if
                                           np.all(
                                               [(_i == _j or _i[-4:] == '_ALL') for _i, _j in zip(idx, col)])]) + 1
                    if insertat >= 0:
                        cols_in_order = list(df.columns)
                        logging.debug(f'before insert: {len(cols_in_order)}')
                        df.loc[:, idx] = dfsubset[idx]
                        cols_in_order.insert(insertat, idx)
                        logging.debug(f'after insert: {len(cols_in_order)}')
                        df = df[cols_in_order]
                nongroupby_columns_levels += [groupby_column]
                dfaf = dfaf[[c for c in dfaf.columns if c not in dfsubset]]

    return df[columns + [x for x in df.columns if x not in columns]]


def _get_tabulator_tbl_cols(column_list, gby):
    cols_format = []
    for col in column_list:
        if col not in gby:
            cols_format.append({'title': col, "field": col, "resizeable": True, 'width': '10px', 'topCalc': 'sum',
                                'topCalcParams': {'precision': 2}})
        else:
            cols_format.append({'title': col, "field": col, "resizeable": True, 'width': '20px'})

    return cols_format


def _get_tabulator_data_(df):
    data = df.to_dict(orient='records')
    return data


def make_tabulator_table(df, grpby=[]):
    columns = _get_tabulator_tbl_cols(df.columns.values, grpby)
    data = _get_tabulator_data_(df)

    tbl = dash_tabulator.DashTabulator(
        id='tabulator',
        columns=columns,
        data=data,
        options={"groupBy": grpby, "selectable": 1, "groupStartOpen": False},
        downloadButtonType={"css": "btn btn-primary", "text": "Export", "type": "xlsx"},
    )
    return tbl


def make_loading(children, type='default'):
    return dcc.Loading(
        type=type, 
        color='#3D9970', 
        children=children,
        parent_className='loading-wrapper',
        overlay_style={"visibility": "visible", "opacity": 1}
    )


def get_data_list_component(id, list_items):
    return html.Datalist(
        id=id,
        children=[html.Option(value=word) for word in list_items])


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger('test', loglevel=logging.DEBUG)
    pd.set_option('display.max_columns', None)
    pd.set_option('display.max_rows', None)
    df = pd.read_pickle('c:/temp/df.pkl')

    dftr = df_calc_row_totals(df, df.columns[:2], PIVOT_AGGFUNCS)
    df = merge_row_totals(df, dftr)

    dftc = df_calc_column_totals(df)
    df = merge_column_total(df, dftc)
