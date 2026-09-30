import pandas as pd
from dash import Input, Output, State, ALL, callback, ctx
from dash.exceptions import PreventUpdate


def register_vol_tracker_callbacks(page):
    @callback(
        [Output(page.output_div, 'children')],
        [Input(page.submit_id, 'n_clicks')],
        [State(page.date_id, 'date'),
         State(page.lookback_id, 'value'),
         State(page.entity_id, 'value'),
         State(page.rvol_window_id, 'value'),
         State(page.vol_unit_id, 'value')],
        prevent_initial_call=True
    )
    def on_submit(n_clicks, asof_date, lookback, entities, rvol_window, vol_unit):
        if n_clicks is None:
            raise PreventUpdate

        asof_date = pd.to_datetime(asof_date).date()
        entities = entities if isinstance(entities, list) else [entities]

        filters = page.build_filters(rvol_window, vol_unit)
        return [page.generate_result_tabs(asof_date, lookback, entities, filters)]

    @callback(
        Output(page.entity_tabs_id, 'value'),
        Output({'type': page.product_tabs_type, 'ccy': ALL}, 'value'),
        Output(page.focus_chart_id, 'children'),
        Input({'type': page.signal_cell_type, 'index': ALL}, 'n_clicks'),
        State(page.date_id, 'date'),
        State(page.lookback_id, 'value'),
        State(page.vol_unit_id, 'value'),
        prevent_initial_call=True,
    )
    def on_signal_cell_click(n_clicks_list, asof_date, lookback, vol_unit):
        if not ctx.triggered_id or all(not n for n in n_clicks_list):
            raise PreventUpdate

        entity, inst = ctx.triggered_id['index'].split('|')
        product_key = page.config.inst_to_product.get(inst, page.config.default_product)
        n_tabs = len(ctx.outputs_list[1])

        vol_mult, vol_label = page.get_vol_params(vol_unit or page.config.vol_unit_options[0]['value'])

        asof = pd.to_datetime(asof_date).date()
        data = page.load_entity_data(entity, asof, lookback or '1y')
        data_key, col_name, _ = page.config.all_instruments[inst]

        df = data.get(data_key)
        ts = df[col_name] if df is not None and col_name in df.columns else None
        roll_dates = page.get_roll_dates(data)

        return entity, [product_key] * n_tabs, page.build_focus_chart(
            entity, inst, ts, vol_mult, vol_label, roll_dates=roll_dates)


def register_extended_callbacks(page, extra_states=None, filter_builder=None):
    all_states = [
        State(page.date_id, 'date'),
        State(page.lookback_id, 'value'),
        State(page.entity_id, 'value'),
        State(page.rvol_window_id, 'value'),
        State(page.vol_unit_id, 'value'),
    ]
    if extra_states:
        all_states.extend(extra_states)

    @callback(
        [Output(page.output_div, 'children')],
        [Input(page.submit_id, 'n_clicks')],
        all_states,
        prevent_initial_call=True
    )
    def on_submit(n_clicks, *args):
        if n_clicks is None:
            raise PreventUpdate

        asof_date = pd.to_datetime(args[0]).date()
        lookback = args[1]
        entities = args[2] if isinstance(args[2], list) else [args[2]]

        if filter_builder:
            filters = filter_builder(*args[3:])
        else:
            filters = page.build_filters(args[3], args[4])

        return [page.generate_result_tabs(asof_date, lookback, entities, filters)]

    @callback(
        Output(page.entity_tabs_id, 'value'),
        Output({'type': page.product_tabs_type, 'ccy': ALL}, 'value'),
        Output(page.focus_chart_id, 'children'),
        Input({'type': page.signal_cell_type, 'index': ALL}, 'n_clicks'),
        State(page.date_id, 'date'),
        State(page.lookback_id, 'value'),
        State(page.vol_unit_id, 'value'),
        prevent_initial_call=True,
    )
    def on_signal_cell_click(n_clicks_list, asof_date, lookback, vol_unit):
        if not ctx.triggered_id or all(not n for n in n_clicks_list):
            raise PreventUpdate

        entity, inst = ctx.triggered_id['index'].split('|')
        product_key = page.config.inst_to_product.get(inst, page.config.default_product)
        n_tabs = len(ctx.outputs_list[1])

        vol_mult, vol_label = page.get_vol_params(vol_unit or page.config.vol_unit_options[0]['value'])

        asof = pd.to_datetime(asof_date).date()
        data = page.load_entity_data(entity, asof, lookback or '1y')
        data_key, col_name, _ = page.config.all_instruments[inst]

        df = data.get(data_key)
        ts = df[col_name] if df is not None and col_name in df.columns else None
        roll_dates = page.get_roll_dates(data)

        return entity, [product_key] * n_tabs, page.build_focus_chart(
            entity, inst, ts, vol_mult, vol_label, roll_dates=roll_dates)
