import dash_core_components as dcc
import dash_html_components as html
import pandas as pd

from common.utils.dtutil import today, dateadd
from dash_reports.cds_cash_basis.constants import DEFAULT_LOOKBACK, INDEX_KEYS, PREFIX
from dash_reports.cds_cash_basis.data import caching, data_loader
from dash_reports.cds_cash_basis.pages import charts
from dash_reports.report_common.pages.link_and_headers import get_page_title
from dash_reports.report_common.utils import dfhelpers
from dash_reports.report_common.utils.components import get_datepicker_control, make_dash_table, make_loading

OUTPUT_ID = f'{PREFIX}_output'
SUBMIT_ID = f'{PREFIX}_submit'
DATE_ID = f'{PREFIX}_asof_date'
LOOKBACK_ID = f'{PREFIX}_lookback'

LOOKBACK_OPTS = [
    {'label': '6m', 'value': '6m'}, {'label': '1y', 'value': '1y'},
    {'label': '2y', 'value': '2y'}, {'label': '5y', 'value': '5y'},
]

NAME_COLS = ['name', 'sector', 'basis_bps', 'cds_spread_bps', 'bond_z_5y_bps', 'constituent_weight']
SECTOR_COLS = ['sector', 'basis_bps', 'cds_spread_bps', 'n']
SUMMARY_COLS = ['label', 'region', 'ew_basis_bps', 'coverage_pct', 'names']


def get_cds_cash_basis_headers(app=None):
    if app is not None:
        app.title = 'CDS-Cash Basis'
    return html.Div([get_page_title('CDS-Cash Basis (US & EU)'), html.Br()])


def create_cds_cash_basis_layout():
    dt = dateadd(today(), '-1b')
    static = html.Div([
        html.Br(),
        html.Div([
            html.Label('As Of', className='subtitle'),
            get_datepicker_control(dt=dt, id=DATE_ID, disabled=False, style={'marginRight': '12px'}),
            html.Label('Lookback', className='subtitle'),
            dcc.Dropdown(id=LOOKBACK_ID, options=LOOKBACK_OPTS, value=DEFAULT_LOOKBACK,
                         clearable=False, style={'width': '90px', 'display': 'inline-block'}),
            html.Button('Submit', id=SUBMIT_ID, className='submit-button', style={'marginLeft': '16px'}),
        ], className='fund_filter', style={'display': 'flex', 'alignItems': 'center', 'flexWrap': 'wrap'}),
        html.Br(),
    ])
    dynamic = make_loading(html.Div(className='page', id=OUTPUT_ID))
    return static, dynamic


def _fmt_table(df, cols):
    if df is None or df.empty:
        return html.Div('No data', style={'color': '#888'})
    d = df[[c for c in cols if c in df.columns]].copy()
    d.columns = [dfhelpers.ui_column_name(c) for c in d.columns]
    return make_dash_table(d, num_col_width='100px', txt_col_width='140px')


def _metric_row(label, val, unit='bps', fmt='.2f'):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        txt = '—'
    elif fmt:
        txt = f'{val:{fmt}} {unit}'.strip()
    else:
        txt = str(val)
    return html.Div([html.Span(label + ': ', style={'fontWeight': '600'}), html.Span(txt)])


def _index_tab(pack):
    meta, label = pack['meta'], pack['meta']['label']
    hist = pack['history']
    cov = pack.get('coverage_pct')
    cov_txt = f'{cov * 100:.0f}%' if cov is not None else '—'
    sub = html.Div([
        html.Div([
            _metric_row('EW constituent basis', pack.get('ew_basis_bps')),
            html.Span(' · ', style={'margin': '0 8px'}),
            _metric_row('Names with basis', len(pack['names']) if pack['names'] is not None else 0, '', fmt=''),
            html.Span(' · ', style={'margin': '0 8px'}),
            _metric_row('Bond coverage', cov_txt, '', fmt=''),
        ], style={'marginBottom': '8px'}),
        dcc.Graph(figure=charts.index_basis_ts(hist, label)),
        dcc.Tabs([
            dcc.Tab(label='Sector', children=[
                html.Br(),
                dcc.Graph(figure=charts.sector_bar(pack['sectors'], label)),
                _fmt_table(pack['sectors'], SECTOR_COLS),
            ]),
            dcc.Tab(label='Single names', children=[
                html.Br(),
                dcc.Graph(figure=charts.names_scatter(pack['names'], label)),
                _fmt_table(_names_ranked(pack['names']), NAME_COLS),
            ]),
        ]),
    ])
    return dcc.Tab(label=label, children=[html.Br(), sub])


def _names_ranked(df):
    if df is None or df.empty:
        return df
    return df.sort_values('basis_bps', ascending=False, na_position='last')


def build_results(asof_date, lookback):
    bundle = data_loader.build_index_summary(asof_date, lookback)
    overview = html.Div([
        dcc.Graph(figure=charts.overview_basis_ts(bundle['history'], bundle['summary'])),
        html.H6('Snapshot'),
        _fmt_table(bundle['summary'], SUMMARY_COLS),
    ])
    index_tabs = [_index_tab(caching.get_cached_or_load(k, asof_date, lookback)) for k in INDEX_KEYS]
    return dcc.Tabs([
        dcc.Tab(label='Overview', children=[html.Br(), overview]),
        dcc.Tab(label='US', children=[html.Br(), dcc.Tabs(index_tabs[:2])]),
        dcc.Tab(label='EU', children=[html.Br(), dcc.Tabs(index_tabs[2:])]),
        dcc.Tab(label='All indices', children=[html.Br(), dcc.Tabs(index_tabs)]),
    ])
