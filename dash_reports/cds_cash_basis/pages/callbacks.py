import pandas as pd
from dash import Input, Output, State, callback
from dash.exceptions import PreventUpdate

from dash_reports.cds_cash_basis.constants import DEFAULT_LOOKBACK
from dash_reports.cds_cash_basis.pages import cds_cash_basis_page as page


@callback(
    Output(page.OUTPUT_ID, 'children'),
    Input(page.SUBMIT_ID, 'n_clicks'),
    State(page.DATE_ID, 'date'),
    State(page.LOOKBACK_ID, 'value'),
    prevent_initial_call=True,
)
def on_submit(n_clicks, asof_date, lookback):
    if not n_clicks:
        raise PreventUpdate
    asof_date = pd.to_datetime(asof_date).date()
    return [page.build_results(asof_date, lookback or DEFAULT_LOOKBACK)]
