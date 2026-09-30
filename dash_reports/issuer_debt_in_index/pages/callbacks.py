import logging

from dash import callback, Input, Output, State, MATCH
from dash.exceptions import PreventUpdate

from dash_reports.report_common.pages import ui_config


@callback([Output('debt-view-results', "children"), ],
          [Input('debt-view-submit', "n_clicks"), ],
          [State("url", "pathname"),
           State('debt-view-date-control', 'date'),
           State({'type': ui_config.SAVE_CONFIG_INPUT_TYPE, 'index': 'debt-view-input-control'}, 'value'),
           State({'type': ui_config.SAVE_CONFIG_INPUT_TYPE, 'index': 'industry'}, 'value'),
           State({'type': ui_config.SAVE_CONFIG_INPUT_TYPE, 'index': 'instrument_id'}, 'value'),
           ])
def on_issuer_debt_index_submit(n_clicks, page, dt, issuer, industry, instrument_id):
    if not n_clicks:
        raise PreventUpdate
    logging.info(
        f'on debt view submit for dt: {dt}, issuer: {issuer} page: {page}')
    page = page.split('/')[-1]
    if page == 'issuer-spread-curve':
        from . import issuer_spread_curve_page

        return [issuer_spread_curve_page.create_result_layout(issuer)]
    from . import issuer_debt_in_index_page

    return [issuer_debt_in_index_page.create_result_layout(page, dt, issuer, industry, instrument_id)]


@callback([Output('history-bond-chart-id', "children"),
           Output('history-bond-grid-id', "children"), ],
          [Input('dd1-bond-id', "value"),
           Input('dd2-bond-id', 'value')],
          [State({'type': ui_config.SAVE_CONFIG_INPUT_TYPE, 'index': 'debt-view-input-control'}, 'value')])
def on_historical_bond_change(bond1, bond2, issuer):
    import pandas as pd
    from . import issuer_spread_curve_page

    logging.info(f'on historical bond change callback: {bond1} {bond2} {issuer}')
    issuer = issuer.upper()
    df = issuer_spread_curve_page.get_historical_df(issuer)
    logging.info(f'retrieved issuer spread cache on callback:  {issuer} {df.shape}')
    df = df[df['security_des'].isin([bond1, bond2])]

    chart_cols = ['oas_usd', 'yield_to_worst_conv_usd', 'price', 'xccy', 'curve', 'spread_diff_bond_v_curve',
                  'spread_diff_bond_v_other_curve', 'secured_curve', 'unsecured_curve', 'amt_outstanding_usd', ]
    pivot_df = pd.pivot_table(df, values=chart_cols,
                              index=['asof_date'],
                              columns=['security_des'],
                              dropna=False)
    pivot_df = pivot_df.sort_index(ascending=False)

    chart_div = issuer_spread_curve_page.get_history_chart_div(issuer, pivot_df, chart_cols)
    grid_div = issuer_spread_curve_page.get_history_grid_div(bond1, bond2, pivot_df)

    return [chart_div, grid_div]


@callback([Output({'type': 'chart-id', 'index': MATCH}, "children"),
           Output({'type': 'grid-id', 'index': MATCH}, "children"), ],
          [Input({'type': 'date-type-id', 'index': MATCH}, "date")],
          [State({'type': ui_config.SAVE_CONFIG_INPUT_TYPE, 'index': 'debt-view-input-control'}, 'value')])
def on_historical_dt_change(dt, issuer):
    from common.utils import dtutil
    from . import issuer_spread_curve_page

    logging.info(f'on historical dt change callback: {dt} {issuer}')
    issuer = issuer.upper()
    dt = dtutil.todate(dt)
    df = issuer_spread_curve_page.get_historical_df(issuer)
    if df is None:
        return [None, None]
    logging.info(f'retrieved issuer spread cache on callback: {dt} {issuer} {df.shape}')
    chart_div = issuer_spread_curve_page.get_chart_div_kids(issuer, dt, df)
    grid_div = issuer_spread_curve_page.get_grid_div_kids(issuer, dt, df)
    return [chart_div, grid_div]


@callback([Output('bonds-rv-result', "children"), ],
          [Input('bonds-rv-submit', "n_clicks"), ],
          [State('bonds-rv-list-issuer-input', 'value'),
           State('bonds-rv-list-industry-input', 'value'),
           State('bonds-rv-list-maturity-input', 'value'),
           State('bonds-rv-list-check', 'value')])
def on_bond_rv_list_submit(n_clicks, issuer, industry, maturity, checked_constraints):
    if not n_clicks:
        raise PreventUpdate
    from . import bonds_rv_list

    return [bonds_rv_list.create_result_layout(issuer, industry, maturity, checked_constraints)]


@callback([Output('bonds-rv-ts-result', "children"), ],
          [Input('bonds-rv-ts-submit', "n_clicks"), ],
          [State('bonds-rv-ts-input', 'value'), ])
def on_bond_rv_ts_submit(n_clicks, bonds):
    if not n_clicks:
        raise PreventUpdate
    from . import bonds_rv_ts

    return [bonds_rv_ts.create_result_layout(bonds)]
