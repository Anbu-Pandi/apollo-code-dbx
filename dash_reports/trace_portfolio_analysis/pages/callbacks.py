import pandas as pd
from dash.exceptions import PreventUpdate
import dash
from dash import Input, Output, State, ALL, MATCH, callback, dcc, ctx

import dash_reports.trace_portfolio_analysis.pages.trace_trade_summary as trade_summary


@callback([Output(component_id=trade_summary.TRD_SUMMARY_OUTPUT_DIV, component_property='children')],
          [Input(trade_summary.TRD_SUMMARY_SUBMIT_ID, 'n_clicks')],
          [State(trade_summary.TRD_SUMMARY_FROM_DT, 'date'),
           State(trade_summary.TRD_SUMMARY_TO_DT, 'date')],
          prevent_initial_call=True
          )
def on_trade_summary_data_submit(n_clicks, from_dt, to_dt):
    from_dt = pd.to_datetime(from_dt).date()
    to_dt = pd.to_datetime(to_dt).date()

    kids = trade_summary.generate_header_tabs(from_dt, to_dt)

    return [kids]


@callback(
    [Output(component_id={'type': 'apo_dv01_tbl', 'index': MATCH}, component_property='children')],
    [Input({'type': 'apo_dv01_chart', 'index': MATCH}, 'clickData')],
    prevent_initial_call=True)
def on_apo_dv01_chart_click(click_data):
    index = ctx.triggered_id['index']
    asof_date = pd.to_datetime(click_data['points'][0]['x']).date()
    return trade_summary.get_apo_dv01_tbl(index, asof_date)
