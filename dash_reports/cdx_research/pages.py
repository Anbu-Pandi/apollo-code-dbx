"""Small callbacks: start a run, then append only newly completed panels."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dash import ALL, MATCH, Input, Output, State, Patch, callback, ctx, dash_table, dcc, html, no_update

from dash_reports.cdx_research.jobs import validate_inputs
from dash_reports.cdx_research.instruments import DEFAULT_TICKERS, PREFIXES, initial_options, resolve_selection
from dash_reports.cdx_options.pages.formatting.styles import (
    GRAPH_CARD_STYLE, CONTROL_ROW_STYLE, CONTROL_SURFACE_STYLE, CONTROL_GROUP_STYLE,
    CONTROL_LABEL_STYLE, INPUT_STYLE, DROPDOWN_STYLE, ACTION_BUTTON_STYLE,
    LIGHT_TABLE_CELL, LIGHT_TABLE_HEADER, LIGHT_TABLE_WRAP, PAGE_SHELL_STYLE,
    STATUS_TEXT_STYLE,
)

CARD = GRAPH_CARD_STYLE
CONTROL = {**CONTROL_ROW_STYLE, 'alignItems': 'end'}


def header(kind):
    return html.H6('CDX Live Quotes' if kind == 'quotes' else 'Cross-Asset Volatility & Correlation', className='subtitleheading')


def field(label, child):
    return html.Div([html.Label(label, style=CONTROL_LABEL_STYLE), child], style=CONTROL_GROUP_STYLE)


def ticker_band(items):
    if not items:
        items = [{'label': family, 'family': family, 'price': None, 'change': None, 'is_otr': True}
                 for family in DEFAULT_TICKERS]
    cards = []
    for item in items:
        price, change = item.get('price'), item.get('change')
        unit = item.get('unit', 'px' if item['family'] == 'CDX-NAHY' else 'bps')
        favorable = change is not None and (change > 0 if unit == 'px' else change < 0)
        tone = 'positive' if favorable else 'negative' if change else 'neutral'
        cards.append(html.Div([
            html.Span(item['label'], className='cdxr-ticker-name'),
            html.Span(f'{price:,.2f} {unit}' if price is not None else '—', className='cdxr-ticker-price'),
            html.Span(f'{change:+.2f} {unit}' if change is not None else 'Δ —', className=f'cdxr-ticker-change {tone}'),
            html.Span((item.get('asof') or '')[11:19] + ' ET' if item.get('asof') else 'Awaiting quotes', className='cdxr-ticker-time'),
        ], className='cdxr-ticker' + (' otr' if item.get('is_otr') else ''),
            title=f"{item.get('value', '')} | Live: {item.get('asof') or 'unavailable'} | Prior close: {item.get('previous')} ({item.get('prior_asof') or 'unavailable'})"))
    return cards


def layout(kind, query=None):
    query = query or {}
    p = f'cdxr-{kind}'
    today = datetime.now(ZoneInfo('America/New_York')).date()
    start = today - timedelta(days=1 if kind == 'quotes' else 5 * 365)
    if kind == 'quotes':
        while start.weekday() > 4:
            start -= timedelta(days=1)
    controls = [field('Date range', dcc.DatePickerRange(id=f'{p}-dates', start_date=query.get('start', str(start)),
                                                      end_date=query.get('end', str(today)), display_format='YYYY-MM-DD'))]
    if kind == 'quotes':
        controls += [field('Instruments', dcc.Dropdown(
            id=f'{p}-selection', options=initial_options(), value=list(DEFAULT_TICKERS), multi=True,
            style={**DROPDOWN_STYLE, 'width': '440px'})),
            field('Sampling (seconds)', dcc.Input(id=f'{p}-number', type='number', min=10, max=3600, value=300, style={**INPUT_STYLE, 'width': '90px'})),
            field('Live refresh', dcc.Dropdown(id=f'{p}-frequency', value=300,
                  options=[{'label': 'Paused', 'value': 0}] + [{'label': f'{s}s', 'value': s} for s in (30, 60, 120, 300)],
                  clearable=False, persistence=True, persistence_type='local',
                  style={**DROPDOWN_STYLE, 'width': '120px'})),
            dcc.Store(id=f'{p}-deltas', data=[]),
            dcc.Input(id=f'{p}-weekly-window', value=12, style={'display': 'none'}),
            dcc.Input(id=f'{p}-half-life', value=4, style={'display': 'none'})]
    else:
        controls += [field('Option tenors', dcc.Dropdown(id=f'{p}-selection', options=['1m', '3m', '6m'],
                                                       value=['1m', '3m'], multi=True, style={**DROPDOWN_STYLE, 'width': '170px'})),
                     field('Payer / put deltas', dcc.Dropdown(id=f'{p}-deltas',
                         options=[{'label': f'{d}d', 'value': d/100} for d in (10, 15, 25, 50, 75, 85, 90)],
                         value=[.15, .25, .5, .75, .85], multi=True, style={**DROPDOWN_STYLE, 'width': '250px'})),
                     field('Correlation window (observations)', dcc.Input(id=f'{p}-number', type='number', value=90, min=5, max=504, style={**INPUT_STYLE, 'width': '90px'})),
                     field('Weekly correlation window', dcc.Input(id=f'{p}-weekly-window', type='number', value=12, min=2, max=104, style={**INPUT_STYLE, 'width': '90px'})),
                     field('EW half-life (weeks)', dcc.Input(id=f'{p}-half-life', type='number', value=4, min=.1, max=104, style={**INPUT_STYLE, 'width': '90px'})),
                     dcc.Store(id=f'{p}-frequency', data=0)]
    controls.append(html.Button('Refresh' if kind == 'quotes' else 'Run / Refresh model', id=f'{p}-refresh', n_clicks=0, style=ACTION_BUTTON_STYLE))
    controls.append(html.Button('Rebuild selected history', id=f'{p}-rebuild', n_clicks=0,
                               style=ACTION_BUTTON_STYLE if kind == 'model' else {'display': 'none'}))
    content = html.Div([
        html.Div([dcc.Link('All Reports', href='/'), ' · ', dcc.Link('CDX Options', href='/cdx-options/'), ' · ',
                  dcc.Link('Live Quotes', href='/cdx-live-quotes/'), ' · ',
                  dcc.Link('Cross-Asset Volatility & Correlation', href='/cross-asset-correlation/')], style={'marginBottom': '12px'}),
        html.Div(ticker_band([]), id=f'{p}-ticker', className='cdxr-ticker-band'),
        html.Div('Latest available Tradeweb mids · change from prior business-day close', id=f'{p}-market-status', style=STATUS_TEXT_STYLE),
        html.Div(controls, style={**CONTROL_SURFACE_STYLE, **CONTROL, 'marginTop': '10px'}),
        html.Div('Loading…', id=f'{p}-status', role='status', style={**STATUS_TEXT_STYLE, 'marginBottom': '12px'}),
        field('Show sections', dcc.Dropdown(id=f'{p}-sections', multi=True,
            options=['Return correlations', 'Implied correlations', 'Volatility', 'All model fields', 'Inputs and diagnostics'],
            value=['Return correlations', 'Implied correlations'],
            style=DROPDOWN_STYLE)) if kind == 'model' else html.Div(),
        html.Div(id=f'{p}-panels'),
        dcc.Store(id=f'{p}-run'), dcc.Store(id=f'{p}-seen', data=0),
        dcc.Interval(id=f'{p}-poll', interval=5000, disabled=True, n_intervals=0),
        dcc.Interval(id=f'{p}-timer', interval=60000, disabled=kind != 'quotes', n_intervals=0),
        dcc.Interval(id=f'{p}-market-poll', interval=5000 if kind == 'quotes' else 30000, n_intervals=0),
        dcc.Store(id=f'{p}-market-revision', data=-1),
    ], className='cdx-dashboard cdx-research', style=PAGE_SHELL_STYLE)
    return content, []


def panel_category(title):
    title = title.lower()
    if 'all model fields' in title:
        return 'All model fields'
    if 'return correlation' in title or 'realized correlation' in title:
        return 'Return correlations'
    if 'correlation' in title:
        return 'Implied correlations'
    if 'vol' in title and any(s in title for s in ('—', 'ratio')):
        return 'Volatility'
    return 'Inputs and diagnostics'


def table_component(panel, key=None):
    kwargs = {'id': {'type': 'cdxr-data', 'key': key}} if key else {}
    return dash_table.DataTable(
        **kwargs, columns=[{'name': c, 'id': c} for c in panel.get('columns', [])], data=panel.get('table', []),
        page_size=20, sort_action='native', filter_action='native', export_format='csv',
        style_table=LIGHT_TABLE_WRAP, style_cell=LIGHT_TABLE_CELL, style_header=LIGHT_TABLE_HEADER)


def chart_data_tabs(panel, key=None):
    attributes = {'id': {'type': 'cdxr-chart', 'key': key}} if key else {}
    return dcc.Tabs(value='chart', children=[
        dcc.Tab(label='Chart', value='chart', children=dcc.Graph(
            **attributes, figure=panel['figure'], config={'displaylogo': False}, responsive=True)),
        dcc.Tab(label='Data', value='data', children=table_component(panel, key))])


def render_panel(panel, key=None, kind='quotes', sections=None):
    children = [html.H6(panel['title'])]
    if panel.get('note'):
        children.append(html.P(panel['note'], style={'fontSize': '12px', 'color': '#586675'}))
    if panel.get('error'):
        children.append(html.Div(panel['error'], style={'color': '#a32626'}))
    elif 'figure' in panel:
        if 'table' in panel and key and 'selected' in panel:
            names = [trace['name'] for trace in panel['figure']['data']]
            selected = panel.get('selected', names)
            figure, rows, columns = select_series(selected, panel)
            data_panel = {'figure': figure, 'table': rows, 'columns': [c['id'] for c in columns]}
            children += [field('Series / fields', dcc.Dropdown(id={'type': 'cdxr-series', 'key': key},
                            options=names, value=selected, multi=True, style=DROPDOWN_STYLE)),
                         dcc.Store(id={'type': 'cdxr-panel', 'key': key}, data=panel),
                         chart_data_tabs(data_panel, key)]
        elif 'table' in panel:
            children.append(chart_data_tabs(panel))
        else:
            children.append(dcc.Graph(figure=panel['figure'], config={'displaylogo': False}, responsive=True))
    elif 'table' in panel:
        children.append(dcc.Tabs(value='overview', children=[
            dcc.Tab(label='Overview', value='overview', children=html.P(
                f"{len(panel['table']):,} rows · {len(panel.get('columns', []))} fields. Open Data to filter, sort or export.")),
            dcc.Tab(label='Data', value='data', children=table_component(panel))]))
    category = 'Status' if panel.get('error') or not ('figure' in panel or 'table' in panel) else panel_category(panel['title'])
    attributes = {'id': {'type': 'cdxr-section', 'key': key, 'category': category}} if kind == 'model' else {}
    visible = kind != 'model' or sections is None or category in sections or panel.get('error') or not ('figure' in panel or 'table' in panel)
    return html.Div(children, style={**CARD, **({} if visible else {'display': 'none'})}, **attributes)


def select_series(selected, panel):
    selected = selected or []
    figure = {**panel['figure'], 'data': [t for t in panel['figure']['data'] if t.get('name') in selected]}
    names = {t.get('name') for t in panel['figure']['data']}
    columns = [c for c in panel['columns'] if c not in names or c in selected]
    return figure, [{c: row.get(c) for c in columns} for row in panel['table']], [{'name': c, 'id': c} for c in columns]


@callback(Output({'type': 'cdxr-chart', 'key': MATCH}, 'figure'),
          Output({'type': 'cdxr-data', 'key': MATCH}, 'data'),
          Output({'type': 'cdxr-data', 'key': MATCH}, 'columns'),
          Input({'type': 'cdxr-series', 'key': MATCH}, 'value'),
          State({'type': 'cdxr-panel', 'key': MATCH}, 'data'), prevent_initial_call=True)
def change_series(selected, panel):
    return select_series(selected, panel)


@callback(Output({'type': 'cdxr-section', 'key': ALL, 'category': ALL}, 'style'),
          Input('cdxr-model-sections', 'value'),
          State({'type': 'cdxr-section', 'key': ALL, 'category': ALL}, 'id'), prevent_initial_call=True)
def change_sections(selected, identities):
    return [{**CARD, **({} if i['category'] == 'Status' or i['category'] in (selected or []) else {'display': 'none'})} for i in identities]


def _register(kind):
    p = f'cdxr-{kind}'
    @callback(Output(f'{p}-run', 'data'),
              Input(f'{p}-refresh', 'n_clicks'), Input(f'{p}-timer', 'n_intervals'),
              Input(f'{p}-rebuild', 'n_clicks'),
              State(f'{p}-dates', 'start_date'), State(f'{p}-dates', 'end_date'),
              State(f'{p}-selection', 'value'), State(f'{p}-number', 'value'),
              State(f'{p}-deltas', 'data' if kind == 'quotes' else 'value'),
              State(f'{p}-weekly-window', 'value'), State(f'{p}-half-life', 'value'),
              State(f'{p}-run', 'data'))
    def start_run(clicks, ticks, rebuild_clicks, start, end, selection, number, deltas, weekly_window, half_life, current):
        from dash_reports.sub_servers.cdx_research_server.client import get_client
        params = {'start': start, 'end': end}
        if ctx.triggered_id == f'{p}-timer' and current and current.get('params'):

            params = dict(current['params'])
            old_end = datetime.fromisoformat(params['end']).date()
            now = datetime.now(ZoneInfo('America/New_York')).date()
            if current.get('live_dates') and old_end < now:
                params['start'] = str(datetime.fromisoformat(params['start']).date() + (now - old_end))
                params['end'] = str(now)
        else:
            params.update({'twids': selection, 'interval': number} if kind == 'quotes' else
                          {'tenors': selection, 'window': number, 'deltas': deltas, 'weekly_window': weekly_window,
                           'half_life': half_life, 'rebuild': ctx.triggered_id == f'{p}-rebuild'})
        try:
            params = validate_inputs(kind, params)
            explicit = ctx.triggered_id in (f'{p}-refresh', f'{p}-rebuild')
            result = get_client().research_start(kind, params, force=explicit)

            if current and current.get('id') == result['id'] and not current.get('error'):
                return no_update
            live_dates = (current.get('live_dates', False) if ctx.triggered_id == f'{p}-timer' and current
                          else params['end'] == str(datetime.now(ZoneInfo('America/New_York')).date()))
            return {'id': result['id'], 'params': result['params'], 'live_dates': live_dates}
        except Exception as exc:
            return {'error': str(exc)}

    outputs = [Output(f'{p}-panels', 'children'), Output(f'{p}-status', 'children'),
               Output(f'{p}-seen', 'data'), Output(f'{p}-poll', 'disabled'),
               Output(f'{p}-run', 'data', allow_duplicate=True)]
    states = [State(f'{p}-seen', 'data'), State(f'{p}-poll', 'disabled')]
    if kind == 'model':
        states.append(State(f'{p}-sections', 'value'))

    @callback(*outputs, Input(f'{p}-run', 'data'), Input(f'{p}-poll', 'n_intervals'),
              *states, prevent_initial_call=True)
    def poll_run(run, ticks, seen, poll_disabled=None, sections=None):
        from dash_reports.sub_servers.cdx_research_server.client import get_client
        def result(children, status, cursor, disabled=no_update, run_update=no_update):
            return [children, status, cursor, disabled, run_update]
        def arm_poll(should_run):
            if should_run:
                return False if poll_disabled in (True, None) else no_update
            return True if poll_disabled is not True else no_update
        if not run:
            return result(no_update, no_update, no_update, True)
        if run.get('error'):
            return result(no_update, run['error'], no_update, True)
        reset = not isinstance(seen, dict) or seen.get('id') != run['id']
        if not reset and isinstance(seen, dict) and seen.get('id') == run['id'] and seen.get('done'):
            return result(no_update, no_update, no_update, True)
        since = 0 if reset or not isinstance(seen, dict) or seen.get('id') != run['id'] else seen['revision']
        try:
            snapshot = get_client().research_poll(run['id'], since)
            if reset and snapshot['running'] and (not snapshot['panels'] or
                    (kind == 'quotes' and isinstance(seen, dict) and seen.get('done'))):
                return result(no_update, 'Updating quotes…' if kind == 'quotes' else 'Calculating…', no_update, arm_poll(True))
            if not reset and snapshot['revision'] == since:
                return result(no_update, no_update, no_update, arm_poll(snapshot['running']))
            children = [] if reset else Patch()
            for number, panel in enumerate(snapshot['panels'], start=since):
                children.append(render_panel(panel, f"{run['id']}:{number}", kind, sections))
            errors = sum(bool(panel.get('error')) for panel in snapshot['panels'])
            prior_errors = 0 if reset or not isinstance(seen, dict) else seen.get('errors', 0)
            errors += prior_errors
            created = datetime.fromtimestamp(snapshot['created'], ZoneInfo('America/New_York')).strftime('%H:%M:%S ET')
            status = f"{'Calculating…' if snapshot['running'] else 'Finished'} · Run started {created} · {snapshot['params']['start']} to {snapshot['params']['end']}"
            if errors:
                status += f' · {errors} panel(s) unavailable'
            if snapshot.get('error'):
                status += f" · Failed: {snapshot['error']}"
            return result(children if reset or snapshot['panels'] else no_update, status,
                          {'id': run['id'], 'revision': snapshot['revision'], 'done': not snapshot['running'], 'errors': errors},
                          arm_poll(snapshot['running']))
        except Exception as exc:
            if 'expired or the server restarted' in str(exc) and isinstance(run, dict) and run.get('params'):
                try:
                    fresh = get_client().research_start(kind, run['params'], force=False)
                    return result(no_update, 'Server restarted — starting a fresh run…', no_update, arm_poll(True),
                                  {'id': fresh['id'], 'params': fresh['params'],
                                   'live_dates': run.get('live_dates', False)})
                except Exception as restart_exc:
                    return result(no_update, f'Server unavailable: {restart_exc}', no_update, True)
            return result(no_update, f'Server unavailable: {exc}', no_update, True)

    if kind == 'quotes':
        @callback(Output(f'{p}-timer', 'interval'), Output(f'{p}-timer', 'disabled'),
                  Output(f'{p}-market-poll', 'interval'), Output(f'{p}-market-poll', 'disabled'),
                  Input(f'{p}-frequency', 'value'), Input(f'{p}-market-revision', 'data'),
                  State(f'{p}-timer', 'interval'), State(f'{p}-timer', 'disabled'),
                  State(f'{p}-market-poll', 'interval'), State(f'{p}-market-poll', 'disabled'))
        def refresh_frequency(seconds, revision, timer_interval=None, timer_disabled=None,
                              market_interval=None, market_disabled=None):
            interval = max(30, seconds or 60) * 1000
            ready = revision is not None and revision >= 0
            wanted = (interval, not bool(seconds), interval if ready else 5000, ready and not bool(seconds))
            current = (timer_interval, timer_disabled, market_interval, market_disabled)
            return tuple(want if want != cur else no_update for want, cur in zip(wanted, current))

    market_outputs = [Output(f'{p}-ticker', 'children'), Output(f'{p}-market-status', 'children'),
                      Output(f'{p}-market-revision', 'data')]
    market_states = [State(f'{p}-market-revision', 'data')]
    if kind == 'quotes':
        market_outputs += [Output(f'{p}-selection', 'options'), Output(f'{p}-selection', 'value')]
        market_states += [State(f'{p}-selection', 'value'), State(f'{p}-selection', 'options')]

    @callback(*market_outputs, Input(f'{p}-market-poll', 'n_intervals'), *market_states)
    def update_market(tick, revision, selected=None, current_options=None):
        from dash_reports.sub_servers.cdx_research_server.client import get_client
        extra = [no_update, no_update] if kind == 'quotes' else []
        try:
            snapshot = get_client().market_snapshot()
            if snapshot.get('error'):
                return [no_update, f"Price band refresh unavailable: {snapshot['error']}", no_update] + extra
            if not snapshot['items']:
                return [no_update, 'Loading live quotes and prior close…', no_update] + extra
            if snapshot['revision'] == revision:
                return [no_update, no_update, no_update] + extra
            status = f"Latest available Tradeweb mids · Δ vs {snapshot['previous_date']} close · Refreshed {datetime.fromtimestamp(snapshot['asof'], ZoneInfo('America/New_York')):%H:%M:%S} ET · Quote times shown per instrument"
            if kind == 'quotes':
                options = list(snapshot['options'])
                selected = selected or []
                missing = [x for x in selected if x not in PREFIXES and not any(o['value'] == x for o in options)]
                options += [{'label': x, 'value': x} for x in missing]
                if current_options is not None and {o.get('value') for o in current_options} == {o.get('value') for o in options}:
                    options_out = no_update
                else:
                    options_out = options
                if any(x in PREFIXES for x in selected):
                    resolved = resolve_selection(selected, snapshot['catalogue'])
                    value_out = no_update if set(resolved) == set(selected) else resolved
                else:
                    value_out = no_update
                extra = [options_out, value_out]
            return [ticker_band(snapshot['items']), status, snapshot['revision']] + extra
        except Exception as exc:
            return [no_update, f'Price band unavailable: {exc}', no_update] + extra


_register('quotes')
_register('model')
