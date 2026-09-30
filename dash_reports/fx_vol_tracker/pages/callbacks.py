from dash import Input, Output, State, callback, ctx
from dash.exceptions import PreventUpdate

from dash_reports.vol_tracker_common.base_callbacks import register_extended_callbacks
import dash_reports.fx_vol_tracker.pages.fx_vol_page as page


@callback(
    Output(page.CCY_PAIR_ID, 'value'),
    Input(page.SELECT_ALL_ID, 'n_clicks'),
    Input(page.CLEAR_ALL_ID, 'n_clicks'),
    State(page.CCY_PAIR_ID, 'options'),
    prevent_initial_call=True,
)
def on_select_clear(select_clicks, clear_clicks, options):
    if ctx.triggered_id == page.SELECT_ALL_ID:
        return [o['value'] if isinstance(o, dict) else o for o in options]
    return []


register_extended_callbacks(
    page.page_instance,
    extra_states=[
        State(page.OPTION_TENOR_ID, 'value'),
        State(page.SPREAD_PAIR_ID, 'value'),
        State(page.FLY_COMBO_ID, 'value'),
    ],
    filter_builder=lambda rvol, vol_unit, option_tenors, spreads, flies: (
        page.page_instance.build_filters(rvol, vol_unit, option_tenors, spreads, flies)
    ),
)
