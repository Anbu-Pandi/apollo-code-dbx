import pandas as pd
from dash.exceptions import PreventUpdate
import dash
from dash import Input, Output, State, ALL, MATCH, callback, dcc, ctx

import dash_reports.macro_data.pages.macro_data as macro_data


@callback([Output(component_id=macro_data.MACRO_DATA_OUTPUT_DIV, component_property='children')],
          [Input(macro_data.MACRO_DATA_SUBMIT_ID, 'n_clicks')],
          [State(macro_data.MACRO_DATA_DATE_ID, 'date')],
          prevent_initial_call=True
          )
def on_macro_data_submit(n_clicks, asof_date):
    if n_clicks is None:
        raise PreventUpdate

    asof_date = pd.to_datetime(asof_date).date()
    kids = macro_data.generate_macro_data_result_tabs(asof_date)

    return [kids]


@callback(
    [Output(component_id={'type': 'ex_default_implied_spd_output', 'index': MATCH}, component_property='children')],
    [Input({'type': 'ex_default_implied_spd_chart', 'index': MATCH}, 'clickData')],
    prevent_initial_call=True)
def on_cdx_ex_default_chart_hover(click_data):
    index = ctx.triggered_id['index']
    asof_date = pd.to_datetime(click_data['points'][0]['x']).date()
    return macro_data.get_cdx_ex_default_tbl(index, asof_date)
