import logging

from dash import html, dcc
from dash.dependencies import Input, Output, State

from dash_reports.industry_tracker.constants import (
    DEFAULT_INDUSTRY, FLASK_HTML_PATH, URL_PATH,
)
from dash_reports.industry_tracker.data.universe import list_industries


_PAGE_ID = 'industry-tracker'
_DROPDOWN_ID = f'{_PAGE_ID}-industry-select'
_IFRAME_ID = f'{_PAGE_ID}-iframe'
_REFRESH_BTN_ID = f'{_PAGE_ID}-refresh-btn'
_REFRESH_INTERVAL_ID = f'{_PAGE_ID}-refresh-interval'
_REFRESH_STATUS_ID = f'{_PAGE_ID}-refresh-status'
_RESIZE_SINK_ID = f'{_PAGE_ID}-resize-sink'


def _industry_options():
    return [
        {'label': ind.display_name, 'value': ind.key}
        for ind in list_industries()
    ]


def _resolve_default_industry() -> str:
    keys = [ind.key for ind in list_industries()]
    if DEFAULT_INDUSTRY in keys:
        return DEFAULT_INDUSTRY
    return keys[0] if keys else DEFAULT_INDUSTRY


def _resolve_industry_key(industry_key: str = None) -> str:
    keys = [ind.key for ind in list_industries()]
    if industry_key in keys:
        return industry_key
    return _resolve_default_industry()


def get_industry_tracker_headers(app):
    register_callbacks(app)
    return [
        html.Div([
            html.Label('Industry:', style={
                'fontWeight': '600', 'fontSize': '11px', 'color': '#55585A',
                'textTransform': 'uppercase', 'letterSpacing': '0.4px', 'marginRight': '8px',
            }),
            dcc.Dropdown(
                id=_DROPDOWN_ID,
                options=_industry_options(),
                value=_resolve_default_industry(),
                clearable=False,
                style={'minWidth': '220px', 'fontSize': '11.5px'},
            ),
            html.Button('Refresh', id=_REFRESH_BTN_ID, n_clicks=0, style={
                'marginLeft': '12px', 'padding': '5px 14px', 'fontSize': '11px',
                'fontWeight': '600', 'background': '#007B63', 'color': 'white',
                'border': 'none', 'borderRadius': '3px', 'cursor': 'pointer',
                'textTransform': 'uppercase', 'letterSpacing': '0.4px',
            }),
            html.Span(id=_REFRESH_STATUS_ID, style={
                'marginLeft': '12px', 'fontSize': '10.5px', 'color': '#55585A',
                'fontStyle': 'italic',
            }),
        ], style={'display': 'flex', 'alignItems': 'center', 'gap': '8px',
                  'padding': '8px 16px', 'background': '#FAF9F7',
                  'borderBottom': '1px solid #CAC2BA'}),
    ]


def create_industry_tracker_layout(initial_industry: str = None):
    initial = _resolve_industry_key(initial_industry)
    return [
        dcc.Interval(id=_REFRESH_INTERVAL_ID, interval=5_000, disabled=True),
        html.Div(id=_RESIZE_SINK_ID, style={'display': 'none'}),
        html.Iframe(
            id=_IFRAME_ID,
            src=f'{FLASK_HTML_PATH}/{initial}',
            style={
                'width': '100%', 'height': '85vh', 'minHeight': '600px',
                'border': 'none', 'background': '#FAF9F7', 'display': 'block',
                'margin': '0', 'padding': '0',
            },
        ),
    ]


def register_callbacks(app):
    if getattr(app, '_industry_tracker_cb_registered', False):
        return

    app.clientside_callback(
        """
        function(_, options) {
            var valid = {};
            (options || []).forEach(function(o) { valid[o.value] = true; });
            var requested = null;
            var qs = new URLSearchParams(window.location.search || '');
            if (qs.has('industry')) { requested = qs.get('industry'); }
            if (!requested) {
                var parts = (window.location.pathname || '').split('/industry-tracker/');
                if (parts.length > 1) { requested = parts[1].split('/')[0]; }
            }
            if (requested && valid[requested]) { return requested; }
            return window.dash_clientside.no_update;
        }
        """,
        Output(_DROPDOWN_ID, 'value'),
        Input(_DROPDOWN_ID, 'id'),
        State(_DROPDOWN_ID, 'options'),
    )

    app.clientside_callback(
        """
        function(industry) {
            if (!industry) { return window.dash_clientside.no_update; }
            return '%(path)s/' + industry + '?t=' + Date.now();
        }
        """ % {'path': FLASK_HTML_PATH},
        Output(_IFRAME_ID, 'src'),
        Input(_DROPDOWN_ID, 'value'),
        prevent_initial_call=True,
    )

    app.clientside_callback(
        """
        async function(n_clicks, industry) {
            if (!n_clicks || !industry) {
                return [
                    window.dash_clientside.no_update,
                    window.dash_clientside.no_update,
                    window.dash_clientside.no_update
                ];
            }
            try {
                var statusEl = document.getElementById('%(status_id)s');
                if (statusEl) { statusEl.textContent = 'Refreshing...'; }
                const r = await fetch('%(path)s/refresh/' + industry, {method: 'POST'});
                const j = await r.json();
                if (j && j.error) {
                    return ['Refresh failed: ' + j.error, true, window.dash_clientside.no_update];
                }
                if (j && j.status === 'already_refreshing') {
                    return ['Already refreshing...', false, window.dash_clientside.no_update];
                }
                return ['Refreshing...', false, window.dash_clientside.no_update];
            } catch (e) {
                return ['Refresh failed: ' + e.message, true, window.dash_clientside.no_update];
            }
        }
        """ % {'path': FLASK_HTML_PATH, 'status_id': _REFRESH_STATUS_ID},
        [Output(_REFRESH_STATUS_ID, 'children'),
         Output(_REFRESH_INTERVAL_ID, 'disabled'),
         Output(_IFRAME_ID, 'src', allow_duplicate=True)],
        Input(_REFRESH_BTN_ID, 'n_clicks'),
        State(_DROPDOWN_ID, 'value'),
        prevent_initial_call=True,
    )

    app.clientside_callback(
        """
        async function(n_intervals, industry) {
            if (!industry) {
                return [window.dash_clientside.no_update, true, window.dash_clientside.no_update];
            }
            try {
                const r = await fetch('%(path)s/status');
                const j = await r.json();
                if (j && j.refreshing) {
                    return ['Refreshing...', false, window.dash_clientside.no_update];
                }
                return ['Refresh complete.', true, '%(path)s/' + industry + '?t=' + Date.now()];
            } catch (e) {
                return ['Refresh status failed: ' + e.message, true, window.dash_clientside.no_update];
            }
        }
        """ % {'path': FLASK_HTML_PATH},
        [Output(_REFRESH_STATUS_ID, 'children', allow_duplicate=True),
         Output(_REFRESH_INTERVAL_ID, 'disabled', allow_duplicate=True),
         Output(_IFRAME_ID, 'src', allow_duplicate=True)],
        Input(_REFRESH_INTERVAL_ID, 'n_intervals'),
        State(_DROPDOWN_ID, 'value'),
        prevent_initial_call=True,
    )

    app.clientside_callback(
        """
        function(_) {
            if (window.__industryTrackerResize) { return ''; }
            window.__industryTrackerResize = function() {
                var f = document.getElementById('%(iframe_id)s');
                if (!f) { return; }
                var top = f.getBoundingClientRect().top;
                var h = Math.max(400, window.innerHeight - top - 8);
                f.style.height = h + 'px';
            };
            window.addEventListener('resize', window.__industryTrackerResize);
            var tries = 0;
            var timer = setInterval(function() {
                window.__industryTrackerResize();
                tries++;
                if (tries > 20) { clearInterval(timer); }
            }, 150);
            return '';
        }
        """ % {'iframe_id': _IFRAME_ID},
        Output(_RESIZE_SINK_ID, 'children'),
        Input(_DROPDOWN_ID, 'id'),
    )

    app._industry_tracker_cb_registered = True
    logging.info('Industry Tracker clientside callbacks registered')
