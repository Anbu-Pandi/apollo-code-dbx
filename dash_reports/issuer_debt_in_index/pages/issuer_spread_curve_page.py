import logging
import math
import numbers

import pandas as pd
import plotly.express as px
import plotly.graph_objs as go
from dash import dcc, html
from plotly.subplots import make_subplots

from common.utils import dtutil, creditutil
from dash_reports.issuer_debt_in_index.data import bond_rv
from dash_reports.issuer_debt_in_index.data.debt import get_issuer_curves, add_wts
from dash_reports.report_common.pages.pivot_page_helpers import create_dropdown
from dash_reports.report_common.utils import dfhelpers
from dash_reports.report_common.utils.combos import get_labelled_component, get_dash_table_with_header
from dash_reports.risk_report.pages.utils import get_datepicker_control

CHART_TYPE_ID = 'chart-id'
GRID_TYPE_ID = 'grid-id'
DATE_TYPE_ID = 'date-type-id'

BOND_DD1_ID = 'dd1-bond-id'
BOND_DD2_ID = 'dd2-bond-id'
HISTORY_BOND_GRID_ID = 'history-bond-grid-id'
HISTORY_BOND_CHART_ID = 'history-bond-chart-id'


def adjust_fig(fig, title, w=700, h=400):
    fig.update_layout(title_text=f'<span style="font-size: 12px;font-weight: bold;">{title}</span>',
                      title_x=0.35,
                      autosize=False,
                      width=w,
                      height=h,
                      )


def get_issuer_fig(issuer, dt, issuer_df, fit_type='finpy', spread_col='pecs_rr=0.4_usd', spread_col_factor=10_000):
    issuer_df = add_wts(issuer_df)
    issuer_curves = get_issuer_curves(issuer_df, spread_col=spread_col, spread_col_factor=spread_col_factor,
                                      fit_type=fit_type)

    def _get_implied_value(sec_unsec, mty, dt):
        key = (dtutil.todate(dt), issuer, sec_unsec)
        bc = issuer_curves.get(key, None)
        if not bc is None:
            if fit_type == 'finpy':
                return bc.yield_to_maturity(mty) * spread_col_factor
            else:
                return bc(creditutil.calc_tenor(dtutil.todate(mty), dtutil.todate(dt))) * spread_col_factor

    logging.info(f'creating line chart for issuer: {issuer} {dt} {issuer_df.shape} {spread_col}')

    color_map = {'SEC': 'green', 'SUB': 'orange', 'UNSEC': 'red', }

    max_mty = math.ceil(issuer_df['maturity_years'].dropna().max())
    mty_ds = [dtutil.dateadd(dt, f'{x}y') for x in range(max_mty + 1)]
    mty_ds = pd.Series(mty_ds)
    label_cols = {'secured_unsecured': 'Secured Indicator',
                  'weight': 'Relative Weight',
                  'maturity': 'Maturity',
                  'coupon': 'Coupon',
                  'instrument_type': 'Instrument Type',
                  'yield_to_worst_conv_usd': 'Yield (USD) to Worst',
                  'oas_usd': 'Spread (USD) in bps',
                  'pecs_rr=0.4_usd': 'Par Equiv CDS Spread (USD) in bps',
                  'price': 'Price',
                  'amt_outstanding_usd': 'Amt Outstanding USD mm',
                  'years_since_issue': 'Age in years',
                  'gross_volume_1m': 'Gross Traded 1m Volume mm',
                  }
    label_cols = {k: v for k, v in label_cols.items() if k in issuer_df.columns}
    fig = px.scatter(issuer_df, x="maturity", y=spread_col,
                     size="weight", color='secured_unsecured',
                     color_discrete_map=color_map,
                     custom_data=list(label_cols.keys()),
                     labels=label_cols,
                     hover_name='instrument_id',
                     size_max=20,
                     hover_data=list(label_cols.keys()))

    for sec_unsec in sorted(issuer_df['secured_unsecured'].unique()):
        ds = mty_ds.map(lambda x: _get_implied_value(sec_unsec, x, dt))
        fig.add_trace(go.Scatter(x=mty_ds.to_numpy(), y=ds,
                                 name=f'{sec_unsec} spread curve',
                                 mode='lines',
                                 line=dict(width=1, color=color_map.get(sec_unsec, None)),
                                 ))

    adjust_fig(fig, f'{issuer} {label_cols[spread_col]}')
    return fig


def plot_issuer_curve(issuer, dt, issuer_df):
    issuer_df_dt = issuer_df[pd.to_datetime(issuer_df['asof_date']).dt.date == dtutil.todate(dt)]
    div_kids = []
    title = html.H6(f'Issuer Curve Chart as of date: {dt}', className='subtitle')
    div_kids.append(title)
    if 'pecs_rr=0.4_usd' in issuer_df_dt.columns:
        issuer_df_dt['pecs_rr=0.4_usd'] = issuer_df_dt['pecs_rr=0.4_usd'].combine_first(issuer_df_dt['oas_usd'])
        fig = get_issuer_fig(issuer, dt, issuer_df_dt, spread_col='pecs_rr=0.4_usd', spread_col_factor=10_000,
                             fit_type='finpy')
        div_kids.append(dcc.Graph(figure=fig))

    fig_oas = get_issuer_fig(issuer, dt, issuer_df_dt, spread_col='oas_usd', spread_col_factor=10_000, fit_type='finpy')
    div_kids.append(dcc.Graph(figure=fig_oas))
    fig_yld = get_issuer_fig(issuer, dt, issuer_df_dt,
                             spread_col='yield_to_worst_conv_usd', spread_col_factor=100, fit_type='finpy')
    div_kids.append(dcc.Graph(figure=fig_yld))

    return html.Div(div_kids)


def get_chart_div_kids(issuer, dt, df):
    chart = html.Div(plot_issuer_curve(issuer, dt, df), className='fund_filter')
    return chart


def get_grid_div_kids(issuer, dt, df):
    label_grid = []
    for sec_unsec in sorted(df['secured_unsecured'].unique()):
        sdf = df[df['secured_unsecured'] == sec_unsec].sort_values(['ticker', 'maturity', 'coupon'])
        sdf = sdf[pd.to_datetime(sdf['asof_date']).dt.date == dtutil.todate(dt)]
        sdf = sdf.rename(columns={x: dfhelpers.ui_column_name(x) for x in sdf.columns})
        show_columns = ['instrument_id', 'coupon', 'maturity', 'currency', 'instrument_type',
                        'maturity_bucket', 'rating', 'secured_unsecured',
                        'oas', 'oas_usd', 'pecs_rr=0.4', 'pecs_rr=0.4_usd',
                        'yield_to_worst_conv', 'yield_to_worst_conv_usd', 'price']
        show_columns = [dfhelpers.ui_column_name(x) for x in show_columns]
        show_columns = [x for x in show_columns if x in sdf.columns]
        hidden_columns = [x for x in sdf.columns if x not in show_columns]
        sdf = sdf[show_columns + hidden_columns]
        label_grid.append(get_dash_table_with_header(f'Issuer:{issuer} {sec_unsec} spread curve as of date: {dt}',
                                                     sdf,
                                                     div_class_name='fund_filter',
                                                     hidden_columns=hidden_columns, fixed_columns=3, page_size=10))
    return label_grid


def get_curve_tab_kids(issuer, df):
    curve_tab_kids = []
    curve_tab_kids.append(html.Br())

    df['asof_date'] = pd.to_datetime(df['asof_date']).dt.date
    max_dt = df['asof_date'].dropna().max()
    min_dt = df['asof_date'].dropna().min()
    dts = {'t1': max_dt}
    if min_dt != max_dt:
        dts['t2'] = min_dt

    dt_keys = sorted(list(dts.keys()))
    dts_control = []
    if len(dt_keys) > 1:
        for k in dt_keys:
            dt_div = html.Div(style=dict(display='flex'),
                              children=[html.Div([html.Div(html.Label("{:<25}".format(f"{k.upper()} date:"),
                                                                      className='subtitle'),
                                                           className='fund_filter')]),
                                        html.Div([get_datepicker_control({'type': DATE_TYPE_ID, 'index': k},
                                                                         dt=dts[k], disabled=False,
                                                                         style={})])])
            dts_control.append(html.Div(dt_div, style=dict(width='50%')))
    curve_tab_kids.append(html.Div(style=dict(display='flex'), children=dts_control, ))

    chart_control = []
    for k in dt_keys:
        dt_div = html.Div(get_chart_div_kids(issuer, dts[k], df),
                          className='row',
                          id={'type': CHART_TYPE_ID, 'index': k})
        chart_control.append(html.Div(dt_div, style=dict(width='50%')))
    curve_tab_kids.append(html.Div(style=dict(display='flex'), children=chart_control, ))

    grid_control = []
    for k in dt_keys:
        dt_div = html.Div(get_grid_div_kids(issuer, dts[k], df),
                          className='row', id={'type': GRID_TYPE_ID, 'index': k})
        grid_control.append(html.Div(dt_div, style=dict(width='50%')))
    curve_tab_kids.append(html.Div(style=dict(display='flex'), children=grid_control, ))

    return curve_tab_kids


def get_history_tab_kids(issuer, df):
    holiday_tab_kids = []
    bonds = sorted(df['security_des'].unique().tolist())

    dd1 = get_labelled_component('Bond 1:', create_dropdown(BOND_DD1_ID, bonds, selecteditems=bonds[0], multi=False))
    dd2 = get_labelled_component('Bond 2:', create_dropdown(BOND_DD2_ID, bonds, selecteditems=bonds[-1], multi=False))

    chart_out = html.Div(id=HISTORY_BOND_CHART_ID)
    grid_out = html.Div(id=HISTORY_BOND_GRID_ID)

    holiday_tab_kids.append(html.Br())
    holiday_tab_kids.append(html.Div(dd1, style=dict(width='50%', height='50px'), className='fund_filter'))
    holiday_tab_kids.append(html.Div(dd2, style=dict(width='50%', height='50px'), className='fund_filter'))
    holiday_tab_kids.append(html.Div(chart_out, className='fund_filter'))
    holiday_tab_kids.append(html.Div(grid_out, className='fund_filter'))

    return holiday_tab_kids


def get_history_chart_div(issuer, df, chart_cols):
    attribs = df.columns.levels[0]
    bonds = df.columns.levels[1]
    charts = []
    for attrib in chart_cols:
        if attrib not in attribs:
            continue
        fig = make_subplots(specs=[[{'secondary_y': True}]])
        plot_df = df.loc[:, attrib]
        for col in plot_df.columns:
            fig.add_trace(go.Scatter(x=plot_df.index.to_numpy(), y=plot_df[col],
                                     line=dict(width=2), name=col))
        adjust_fig(fig, f'{issuer} {attrib}', w=900, h=500)
        if (len(bonds) == 2) and (attrib not in ['amt_outstanding_usd']):
            diff_ds = plot_df[bonds[0]] - plot_df[bonds[1]]
            rolling_ds = diff_ds.sort_index().rolling(window=63, min_periods=21)
            z_df = (diff_ds - rolling_ds.mean()) / rolling_ds.std()
            fig.add_trace(go.Scatter(x=z_df.index.to_numpy(), y=z_df,
                                     name=f'3m rolling zscore',
                                     line=dict(width=1, color='gray', dash='solid', ), ),
                          secondary_y=True)

        charts.append(dcc.Graph(figure=fig))

    return charts


def get_history_grid_div(bond1, bond2, df):
    grid_div = [html.Br(),
                get_dash_table_with_header(f'Bonds: {bond1} and {bond2}',
                                           df.reset_index(), page_size=10)]
    return grid_div


def get_historical_df(issuer_ticker):
    df = bond_rv.get_rv_bond_df(tickers="=" + issuer_ticker)

    def format_coupon(x):
        if isinstance(x, numbers.Number):
            return round(x, 4)
        return x

    df['asof_date'] = pd.to_datetime(df['asof_date'], format='mixed').dt.date
    df = df.drop_duplicates(subset=['asof_date', 'instrument_id'])
    df['security_des'] = issuer_ticker + ' ' + df['coupon'].map(format_coupon).astype(str) + \
                         ' ' + df['maturity'].astype(str) + ' ' + df['secured_unsecured'] + \
                         ' ' + df['currency'] + ' ' + df['instrument_id']

    return df


def create_result_layout(issuer):
    logging.info(f"issuer spread curve for issuer: {issuer}")
    issuer = issuer.upper()

    df = get_historical_df(issuer)

    tabkids = []
    tabkids.append(dcc.Tab(label='Issuer Curve', children=get_curve_tab_kids(issuer, df)))
    tabkids.append(dcc.Tab(label='Historical Data', children=get_history_tab_kids(issuer, df)))

    return html.Div(dcc.Tabs(tabkids), className='page')
