import json
import logging
import re as _re
import time
import uuid

import pandas as pd
from pyarrow.flight import FlightError
from dash import Input, Output, State, callback, clientside_callback, ctx, dcc, html, no_update
from dash.exceptions import PreventUpdate

import dash_reports.cdx_options.pages.cdx_options as page
from dash_reports.cdx_options.constants import (
    TAB_INDEX, VOL_GRID_KEYS, BLOTTER_KEYS, POSITION_KEYS,
    MAX_OPTION_STACKS, MAX_EXPIRY_CHART_SETS,
)
from dash_reports.cdx_options.pages.formatting.tables import build_trade_blotter_styles
from dash_reports.cdx_options.data.relative_vol import parse_rv_option


def get_client():
    from dash_reports.sub_servers.cdx_options_server.client import get_client as create_client

    return create_client()

_RC_JS = """
        function rcScope(tabData, kind, elementId) {
            var root = window.__cdxRC || (window.__cdxRC = {});
            var selection = (tabData && (tabData._selection_sig || tabData._calc_sig)) || '';
            var node = elementId ? document.getElementById(elementId) : null;
            var state = root[kind];
            if (!state || state.selection !== selection || (node && state.node !== node)) {
                state = {selection: selection, node: node, values: {}};
                root[kind] = state;
            }
            return state.values;
        }
        function chg(s, k, v) {
            var sig = JSON.stringify(v);
            if (s[k] === sig) return false;
            s[k] = sig;
            return true;
        }
        function rcReset(kind) {
            if (window.__cdxRC) delete window.__cdxRC[kind];
        }
"""

STEPS = [
    ('init', 'Initializing...'),
    ('hist_vols', 'Loading historical vol marks (6M)...'),
    ('hist_trades', 'Loading historical trades...'),
    ('live_trades', 'Loading live trades & quotes...'),
    ('agg_pipeline', 'Vol marks & Greeks (parallel by product)...'),
    ('positions', 'Loading & marking positions (parallel)...'),
    ('done', 'Ready'),
]

STEP_LABELS = {
    'init': 'Initializing',
    'hist_vols': 'Historical vols',
    'hist_trades': 'Historical trades',
    'live_trades': 'Live data',
    'agg_pipeline': 'Vol marks & grids',
    'positions': 'Positions',
    'done': 'Ready',
}

def _build_tracker(step_num, error=None, done=False):
    if done:
        return html.Div()
    if error:
        return html.Div(
            f'\u2716 Load failed: {error}',
            style={'color': '#d32f2f', 'fontSize': '11px', 'padding': '4px 8px',
                   'backgroundColor': '#fdecea', 'borderRadius': '4px'},
        )
    step_key = STEPS[min(step_num, len(STEPS) - 1)][0]
    label = STEP_LABELS.get(step_key, step_key)
    pct = int(step_num / max(len(STEPS) - 1, 1) * 100)
    return html.Div([
        html.Div([
            html.Div(style={
                'width': f'{pct}%', 'height': '3px',
                'backgroundColor': '#006D77', 'borderRadius': '2px',
                'transition': 'width 0.4s ease',
            }),
        ], style={'flex': '1', 'backgroundColor': '#e0e0e0', 'borderRadius': '2px',
                  'marginRight': '10px', 'height': '3px'}),
        html.Span(f'{label}...', style={'fontSize': '10px', 'color': '#666', 'whiteSpace': 'nowrap'}),
    ], style={'display': 'flex', 'alignItems': 'center', 'padding': '4px 0'})


# ---- Vol grid heatmap + selection highlighting (clientside) ----

clientside_callback(
    """function() {
        var n = """ + str(len(VOL_GRID_KEYS)) + """;
        var args = Array.prototype.slice.call(arguments);
        var virtualDataList = args.slice(0, n);
        var selectedCellsList = args.slice(n, 2 * n);
        var columnsList = args.slice(2 * n, 3 * n);
        var gridKeys = """ + repr(VOL_GRID_KEYS) + """;
        var rowColored = {spot_ref: true};
        var palette = [[0.0, [248, 105, 107]], [0.5, [255, 255, 255]], [1.0, [99, 190, 123]]];

        function finiteNumber(v) {
            if (v == null || v === '') return null;
            var n = Number(v);
            return isFinite(n) ? n : null;
        }

        function lerpColor(ratio) {
            ratio = Math.max(0, Math.min(1, ratio));
            for (var i = 0; i < palette.length - 1; i++) {
                var lo = palette[i];
                var hi = palette[i + 1];
                if (ratio <= hi[0]) {
                    var t = (ratio - lo[0]) / Math.max(hi[0] - lo[0], 1e-9);
                    var r = Math.round(lo[1][0] + (hi[1][0] - lo[1][0]) * t);
                    var g = Math.round(lo[1][1] + (hi[1][1] - lo[1][1]) * t);
                    var b = Math.round(lo[1][2] + (hi[1][2] - lo[1][2]) * t);
                    return 'rgb(' + r + ',' + g + ',' + b + ')';
                }
            }
            return 'rgb(' + palette[palette.length - 1][1].join(',') + ')';
        }

        function rgbGradient(val, vmin, vmax) {
            if (val == null) return null;
            if (vmax === vmin) return lerpColor(0.5);
            return lerpColor((val - vmin) / (vmax - vmin));
        }

        function textColor(bg) {
            if (!bg) return '#222';
            var nums = bg.replace('rgb(', '').replace(')', '').split(',');
            if (nums.length !== 3) return '#222';
            var r = Number(nums[0]);
            var g = Number(nums[1]);
            var b = Number(nums[2]);
            var lum = 0.299 * r + 0.587 * g + 0.114 * b;
            return lum > 150 ? '#222' : '#fff';
        }

        function numericColumns(data, idxCol) {
            if (!Array.isArray(data) || !data.length) return [];
            var cols = {};
            data.forEach(function(row) {
                Object.keys(row || {}).forEach(function(col) {
                    if (!idxCol || col !== idxCol) cols[col] = true;
                });
            });
            return Object.keys(cols).filter(function(col) {
                return data.some(function(row) { return finiteNumber((row || {})[col]) !== null; });
            });
        }

        function buildStyles(data, gridKey, idxCol) {
            if (!Array.isArray(data) || !data.length) return [];
            var numericCols = numericColumns(data, idxCol);
            if (!numericCols.length) return [];
            var allVals = [];
            data.forEach(function(row) {
                numericCols.forEach(function(col) {
                    var val = finiteNumber((row || {})[col]);
                    if (val !== null) allVals.push(val);
                });
            });
            if (!allVals.length) return [];
            var globalVmin = Math.min.apply(null, allVals);
            var globalVmax = Math.max.apply(null, allVals);
            var styles = [];
            data.forEach(function(row, rowIdx) {
                var vmin = globalVmin;
                var vmax = globalVmax;
                if (rowColored[gridKey]) {
                    var rowVals = [];
                    numericCols.forEach(function(col) {
                        var val = finiteNumber((row || {})[col]);
                        if (val !== null) rowVals.push(val);
                    });
                    if (rowVals.length) {
                        vmin = Math.min.apply(null, rowVals);
                        vmax = Math.max.apply(null, rowVals);
                    } else {
                        vmin = 0;
                        vmax = 1;
                    }
                }
                numericCols.forEach(function(col) {
                    var val = finiteNumber((row || {})[col]);
                    if (val === null) return;
                    var bg = rgbGradient(val, vmin, vmax);
                    styles.push({
                        if: {row_index: rowIdx, column_id: String(col)},
                        backgroundColor: bg,
                        color: textColor(bg),
                    });
                });
            });
            return styles;
        }

        function extractAtm(deltaData, idxCol) {
            if (!Array.isArray(deltaData) || !deltaData.length) return {};
            var numericCols = numericColumns(deltaData, idxCol);
            var atm = {};
            deltaData.forEach(function(row, rowIdx) {
                var bestCol = null;
                var bestDist = Infinity;
                numericCols.forEach(function(col) {
                    var val = finiteNumber((row || {})[col]);
                    if (val === null) return;
                    var dist = Math.abs(val - 0.50);
                    if (dist < bestDist) {
                        bestDist = dist;
                        bestCol = String(col);
                    }
                });
                if (bestCol != null) atm[rowIdx] = bestCol;
            });
            return atm;
        }

        function applyAtm(styles, atmMap) {
            Object.keys(atmMap).forEach(function(rowIdx) {
                styles.push({
                    if: {row_index: Number(rowIdx), column_id: atmMap[rowIdx]},
                    border: '2px solid #000000',
                    fontWeight: '700',
                });
            });
        }

        var allSelected = [];
        selectedCellsList.forEach(function(cells) {
            if (Array.isArray(cells)) allSelected = allSelected.concat(cells);
        });

        var deltaIdx = gridKeys.indexOf('deltas');
        var deltaCols = deltaIdx >= 0 ? (columnsList[deltaIdx] || []) : [];
        var deltaIdxCol = deltaCols.length ? deltaCols[0].id : null;
        var atmMap = deltaIdx >= 0 ? extractAtm(virtualDataList[deltaIdx], deltaIdxCol) : {};

        return gridKeys.map(function(key, idx) {
            var cols = columnsList[idx] || [];
            var idxCol = cols.length ? cols[0].id : null;
            var styles = buildStyles(virtualDataList[idx], key, idxCol);
            if (key !== 'spot_ref' && Object.keys(atmMap).length) applyAtm(styles, atmMap);
            if (key !== 'spot_ref') {
                var highlightedRows = {};
                var highlightedCols = {};
                allSelected.forEach(function(cell) {
                    if (cell && cell.column_id) {
                        highlightedRows[cell.row || 0] = true;
                        highlightedCols[cell.column_id] = true;
                    }
                });
                Object.keys(highlightedRows).forEach(function(rowIdx) {
                    styles.push({
                        if: {row_index: Number(rowIdx)},
                        borderTop: '1.5px solid rgba(88,166,255,0.4)',
                        borderBottom: '1.5px solid rgba(88,166,255,0.4)',
                    });
                });
                Object.keys(highlightedCols).forEach(function(colId) {
                    styles.push({
                        if: {column_id: colId},
                        borderLeft: '1.5px solid rgba(88,166,255,0.4)',
                        borderRight: '1.5px solid rgba(88,166,255,0.4)',
                    });
                });
                allSelected.forEach(function(cell) {
                    if (cell && cell.column_id) {
                        styles.push({
                            if: {row_index: cell.row || 0, column_id: cell.column_id},
                            border: '2px solid #58a6ff',
                            boxShadow: 'inset 0 0 4px rgba(88,166,255,0.3)',
                        });
                    }
                });
            }
            return styles;
        });
    }""",
    [Output(f'cdx-vol-grid-{k}', 'style_data_conditional') for k in VOL_GRID_KEYS],
    [Input(f'cdx-vol-grid-{k}', 'derived_virtual_data') for k in VOL_GRID_KEYS] +
    [Input(f'cdx-vol-grid-{k}', 'selected_cells') for k in VOL_GRID_KEYS],
    [State(f'cdx-vol-grid-{k}', 'columns') for k in VOL_GRID_KEYS],
    prevent_initial_call=True,
)


# ---- Activity toggle (clientside) ----

clientside_callback(
    """
    function(n) {
        var body = document.getElementById('cdx-activity-body');
        var icon = document.getElementById('cdx-activity-toggle-icon');
        if (!body) return window.dash_clientside.no_update;
        var hidden = body.style.display === 'none';
        body.style.display = hidden ? '' : 'none';
        if (icon) icon.textContent = hidden ? '▾' : '▸';
        return window.dash_clientside.no_update;
    }
    """,
    Output('cdx-activity-toggle', 'style'),
    Input('cdx-activity-toggle', 'n_clicks'),
    prevent_initial_call=True,
)


# ── WS dispatch: route incoming messages to Stores (clientside) ──────────

clientside_callback(
    """function(msg, curTab, committed, loading) {
        var nu = window.dash_clientside.no_update;
        var R = [nu, nu, nu, nu, nu, nu, nu, nu, nu, nu, nu];
        if (!msg) return R;
        try {
            var currentRequestId = loading && loading.request_id ? loading.request_id : null;
            function isStaleRequest(data) {
                if (!data) return false;
                if (data.request_id && currentRequestId && data.request_id !== currentRequestId) return true;
                if (loading && loading.pending && currentRequestId
                        && !data.request_id
                        && (data.type === 'compute_status' || data.type === 'compute_error')) return true;
                return false;
            }
            function hasSel(v) {
                return Array.isArray(v) ? v.length > 0 : v != null && v !== '';
            }
            function normSel(v, numeric) {
                var arr = Array.isArray(v) ? v : (v == null ? [] : [v]);
                return arr.filter(function(x) { return x != null && x !== ''; }).map(function(x) {
                    if (numeric) {
                        var n = Number(x);
                        return isNaN(n) ? String(x) : String(n);
                    }
                    return String(x);
                });
            }
            function sameSel(a, b, numeric) {
                return JSON.stringify(normSel(a, numeric)) === JSON.stringify(normSel(b, numeric));
            }
            function sigFor(product, strike, expiry) {
                return JSON.stringify({
                    product: product == null ? '' : String(product),
                    strike: normSel(strike, true),
                    expiry: normSel(expiry, false)
                });
            }
            function selectionMismatch(data) {
                if (!data || !committed || !committed.product) return false;
                if (data.product && data.product !== committed.product) return true;
                if (data.strike != null && hasSel(committed.strike) && !sameSel(data.strike, committed.strike, true)) return true;
                if (data.expiry != null && hasSel(committed.expiry) && !sameSel(data.expiry, committed.expiry, false)) return true;
                return false;
            }
            var data;
            if (typeof msg === 'string') data = JSON.parse(msg);
            else if (msg && msg.data) data = JSON.parse(msg.data);
            else data = msg;
            console.info('CDX WS msg type=' + data.type + ' src=' + (data._source||'?')
                + (data.type === 'tab_data' ? ' vg=' + !!data.vol_grids : ''));
            if (isStaleRequest(data)) {
                console.info('CDX WS: stale request ignored type=' + data.type + ' req=' + (data.request_id || '?'));
                return R;
            }
            if (data.type === 'progress')            { R[0] = data; return R; }
            if (data.type === 'light_refresh_done')  { R[1] = data; return R; }
            if (data.type === 'activity')            { R[2] = data; return R; }
            if (data.type === 'spot_update')         { R[3] = data; return R; }
            if (data.type === 'status_info')         { R[6] = data; return R; }
            if (data.type === 'task_state')          { R[10] = data; return R; }
            if (data.type === 'compute_status')      {
                if (selectionMismatch(data)) {
                    console.info('CDX WS: stale compute_status ignored for ' + (data.product || '?'));
                    return R;
                }
                R[8] = {text: data.text || '', ts: Date.now()};
                R[9] = data.text || '';
                if (data.text && currentRequestId && data.request_id === currentRequestId) {
                    R[7] = Object.assign({}, loading || {}, {
                        pending: true, stage: 'rendering', detail: data.text, request_id: currentRequestId
                    });
                }
                if (!data.text && currentRequestId && data.request_id === currentRequestId) {
                    R[7] = Object.assign({}, loading || {}, {
                        pending: false, stage: 'ready', detail: '', request_id: currentRequestId
                    });
                }
                return R;
            }
            if (data.type === 'tab_data') {
                if (!committed || !committed.product) {
                    console.info('CDX WS: tab_data ignored before committed selection');
                    return R;
                }
                if (data.product !== committed.product) {
                    console.info('CDX WS: stale tab_data for ' + data.product + ' ignored; want ' + committed.product);
                    return R;
                }
                if (hasSel(committed.strike) && !sameSel(data.strike, committed.strike, true)) {
                    console.info('CDX WS: stale strike tab_data ignored for ' + data.product);
                    return R;
                }
                if (hasSel(committed.expiry) && !sameSel(data.expiry, committed.expiry, false)) {
                    console.info('CDX WS: stale expiry tab_data ignored for ' + data.product);
                    return R;
                }
                if (data._source === 'bridge' && curTab && data.product !== curTab.product) {
                    console.info('CDX WS: bridge for ' + data.product + ' ignored (viewing ' + curTab.product + ')');
                    return R;
                }
                if (curTab && data._cache_state === 'cached-stale'
                        && data._refresh_generation != null && curTab._generation != null
                        && Number(curTab._generation) >= Number(data._refresh_generation)) {
                    console.info('CDX WS: superseded stale generation ignored');
                    return R;
                }
                if (curTab && data._cache_state !== 'cached-stale'
                        && data._generation != null && curTab._generation != null
                        && Number(data._generation) < Number(curTab._generation)) {
                    console.info('CDX WS: older generation ignored');
                    return R;
                }
                var incomingSig = sigFor(data.product, data.strike, data.expiry);
                var incomingContext = data.calc_context || {};
                var incomingCalcSig = incomingContext.calc_sig || data._calc_sig || incomingSig;
                if (curTab && curTab._calc_sig && incomingCalcSig !== curTab._calc_sig
                        && curTab.override_text && !data.override_text) {
                    console.info('CDX WS: non-override tab_data ignored while override view is active');
                    return R;
                }
                var selectionChanged = !curTab || curTab._selection_sig !== incomingSig;
                var contextChanged = !curTab || curTab._calc_sig !== incomingCalcSig;
                var tabKeys = ['vol_grids','trade_blotter','positions','expiry_charts','option_stack'];
                function usablePayload(key, payload) {
                    if (!payload || payload._cleared || payload._empty || payload._error) return false;
                    function hasRows(items) {
                        return Object.keys(items || {}).some(function(k) {
                            return items[k] && Array.isArray(items[k].data) && items[k].data.length > 0;
                        });
                    }
                    if (key === 'vol_grids') return hasRows(payload.grids);
                    if (key === 'trade_blotter') return hasRows(payload.blotters);
                    if (key === 'positions') return Array.isArray(payload.figures) && payload.figures.length > 0;
                    if (key === 'expiry_charts') return !!payload.exp_vols_figure
                        || (Array.isArray(payload.charts) && payload.charts.length > 0);
                    if (key === 'option_stack') return Array.isArray(payload.stacks) && payload.stacks.length > 0;
                    return false;
                }
                var base = Object.assign({}, curTab || {});
                var incomingTabs = tabKeys.filter(function(k) { return data[k] != null; });
                var hasTabPayload = incomingTabs.length > 0;
                var metadataOnlyStale = data._cache_state === 'cached-stale'
                    && !selectionChanged
                    && incomingTabs.every(function(k) { return usablePayload(k, base[k]); });
                if (selectionChanged && hasTabPayload) {
                    tabKeys.forEach(function(k) { delete base[k]; });
                }
                var tabSigs = Object.assign({}, (curTab && curTab._tab_sigs) || {});
                if (selectionChanged && hasTabPayload) tabSigs = {};
                if (!metadataOnlyStale) {
                    incomingTabs.forEach(function(k) {
                        base[k] = data[k];
                        tabSigs[k] = incomingCalcSig;
                    });
                }
                ['defaults','update_text','grid_badge','override_text','product',
                 'strike','expiry','_source','_calc_sig','calc_context','request_id',
                 '_cache_state','_stale','_stale_reason','_stale_at','_materialized_at',
                 '_data_asof','_generation','_refresh_generation','_refresh_duration_ms'].forEach(function(k) {
                    if (data[k] != null) base[k] = data[k];
                });
                base._selection_sig = incomingSig;
                base._calc_sig = incomingCalcSig;
                base.calc_context = Object.assign({}, incomingContext, {calc_sig: incomingCalcSig});
                base._tab_sigs = tabSigs;
                base._changed_tabs = metadataOnlyStale ? [] : incomingTabs;
                base._display_state = data._cache_state === 'cached-stale' ? 'cached-stale' : 'fresh';
                if (base._display_state === 'fresh') {
                    delete base._stale_at;
                    delete base._refresh_generation;
                }
                base._received_at = Date.now();
                var readyTabs = tabKeys.filter(function(k) { return tabSigs[k] === incomingCalcSig; }).length;
                console.info('CDX WS: tab_data merged src=' + (data._source||'?')
                    + ' changed=' + selectionChanged + ' ctxChanged=' + contextChanged + ' ready=' + readyTabs + '/' + tabKeys.length
                    + ' rendered=' + base._changed_tabs.join(',')
                    + ' vg=' + !!base.vol_grids + ' os=' + !!base.option_stack + ' ec=' + !!base.expiry_charts);
                if (currentRequestId && data.request_id === currentRequestId
                        && data._cache_state !== 'cached-stale') {
                    R[7] = Object.assign({}, loading || {}, {
                        pending: false, stage: 'ready', detail: '', request_id: currentRequestId
                    });
                }
                if (currentRequestId && data.request_id === currentRequestId
                        && data._cache_state === 'cached-stale') {
                    R[7] = Object.assign({}, loading || {}, {
                        pending: true, stage: 'refreshing', detail: 'Cached stale view shown',
                        request_id: currentRequestId
                    });
                }
                R[4] = base; return R;
            }
            if (data.type === 'defaults')            {
                R[5] = data;
                if (currentRequestId && data.request_id === currentRequestId) {
                    R[7] = Object.assign({}, loading || {}, {
                        pending: true, stage: 'defaults_ready', detail: 'Selection defaults ready',
                        request_id: currentRequestId
                    });
                }
                return R;
            }
            if (data.type === 'compute_error')       {
                R[7] = Object.assign({}, loading || {}, {
                    pending: false, stage: 'error', detail: 'Refresh failed',
                    request_id: data.request_id || currentRequestId
                });
                return R;
            }
            if (data.type === 'refresh_failed')      {
                if (selectionMismatch(data)) return R;
                var failed = Object.assign({}, curTab || {}, {
                    _display_state: 'refresh-failed',
                    _changed_tabs: [],
                    _stale: true,
                    _stale_reason: data.detail || data.reason || 'refresh_failed',
                    _generation: data.generation,
                    _received_at: Date.now()
                });
                R[4] = failed;
                return R;
            }
        } catch (e) {
            console.error('CDX WS parse error:', e, 'msg type:', typeof msg, 'length:', typeof msg === 'string' ? msg.length : 'N/A');
        }
        return R;
    }""",
    Output(page.CDX_OPTIONS_WS_PROGRESS_STORE, 'data'),
    Output(page.CDX_OPTIONS_WS_LIGHT_STORE, 'data'),
    Output(page.CDX_OPTIONS_WS_ACTIVITY_STORE, 'data'),
    Output(page.CDX_OPTIONS_WS_SPOT_STORE, 'data'),
    Output(page.CDX_OPTIONS_WS_TAB_DATA_STORE, 'data'),
    Output(page.CDX_OPTIONS_WS_DEFAULTS_STORE, 'data'),
    Output(page.CDX_OPTIONS_WS_STATUS_INFO_STORE, 'data'),
    Output(page.CDX_OPTIONS_LOADING_STORE, 'data', allow_duplicate=True),
    Output(page.CDX_OPTIONS_WS_COMPUTE_STATUS_STORE, 'data'),
    Output(page.CDX_OPTIONS_LAST_UPDATE_TEXT, 'children', allow_duplicate=True),
    Output(page.CDX_OPTIONS_WS_TASKS_STORE, 'data'),
    Input(page.CDX_OPTIONS_WS, 'message'),
    State(page.CDX_OPTIONS_WS_TAB_DATA_STORE, 'data'),
    State(page.CDX_OPTIONS_DD_COMMITTED, 'data'),
    State(page.CDX_OPTIONS_LOADING_STORE, 'data'),
    prevent_initial_call=True,
)


clientside_callback(
    """function(msg, committed, sessionData) {
        var nu = window.dash_clientside.no_update;
        if (!msg) return nu;
        function hasSel(v) {
            return Array.isArray(v) ? v.length > 0 : v != null && v !== '';
        }
        function asList(v) {
            return Array.isArray(v) ? v : (v == null ? [] : [v]);
        }
        function normSel(v, numeric) {
            var arr = asList(v);
            return arr.filter(function(x) { return x != null && x !== ''; }).map(function(x) {
                if (numeric) {
                    var n = Number(x);
                    return isNaN(n) ? String(x) : String(n);
                }
                return String(x);
            });
        }
        function sameSel(a, b, numeric) {
            return JSON.stringify(normSel(a, numeric)) === JSON.stringify(normSel(b, numeric));
        }
        try {
            var data = typeof msg === 'string' ? JSON.parse(msg) : (msg && msg.data ? JSON.parse(msg.data) : msg);
            if (!data || data.type !== 'tab_data') return nu;
            if (!committed || !committed.product) return nu;
            var reason = '';
            if (data.product !== committed.product) {
                reason = 'product_mismatch';
            } else if (hasSel(committed.strike) && !sameSel(data.strike, committed.strike, true)) {
                reason = 'strike_mismatch';
            } else if (hasSel(committed.expiry) && !sameSel(data.expiry, committed.expiry, false)) {
                reason = 'expiry_mismatch';
            } else {
                return nu;
            }
            return JSON.stringify({
                command: 'client_diag',
                session_id: sessionData && sessionData.session_id ? sessionData.session_id : null,
                category: 'stale_tab_data',
                reason: reason,
                product: committed.product,
                strike: committed.strike,
                expiry: committed.expiry,
                incoming_product: data.product,
                incoming_strike: data.strike,
                incoming_expiry: data.expiry,
                source: data._source || ''
            });
        } catch (e) {
            return nu;
        }
    }""",
    Output(page.CDX_OPTIONS_WS, 'send', allow_duplicate=True),
    Input(page.CDX_OPTIONS_WS, 'message'),
    State(page.CDX_OPTIONS_DD_COMMITTED, 'data'),
    State(page.CDX_OPTIONS_SESSION_STORE, 'data'),
    prevent_initial_call=True,
)


clientside_callback(
    """function(tabData, cache) {
        var nu = window.dash_clientside.no_update;
        if (!tabData || !tabData.product || tabData._source === 'clear'
                || tabData._display_state === 'cached' || tabData._display_state === 'cached-stale'
                || tabData._display_state === 'refresh-failed') return nu;
        var next = Object.assign({}, cache || {});
        next[tabData.product] = {
            payload: Object.assign({}, tabData, {_display_state: 'cached'}),
            cached_at: Date.now()
        };
        return next;
    }""",
    Output(page.CDX_OPTIONS_TAB_CACHE, 'data'),
    Input(page.CDX_OPTIONS_WS_TAB_DATA_STORE, 'data'),
    State(page.CDX_OPTIONS_TAB_CACHE, 'data'),
    prevent_initial_call=True,
)


# ── Clientside: render vol grids from WS tab_data store ──────────────────

_VG_DATA_OUTPUTS = []
for _k in VOL_GRID_KEYS:
    _VG_DATA_OUTPUTS.extend([
        Output(f'cdx-vol-grid-{_k}', 'data', allow_duplicate=True),
        Output(f'cdx-vol-grid-{_k}', 'columns', allow_duplicate=True),
        Output(f'cdx-vol-grid-{_k}-title', 'children', allow_duplicate=True),
    ])

clientside_callback(
    """function(tabData) {
        var nu = window.dash_clientside.no_update;
""" + _RC_JS + """
        var n = """ + str(len(VOL_GRID_KEYS)) + """;
        var results = new Array(n * 3).fill(nu);
        try {
            if (!tabData) return results;
            if (Array.isArray(tabData._changed_tabs)
                    && tabData._changed_tabs.indexOf('vol_grids') < 0) return results;
            var vg = tabData.vol_grids;
            if (!vg) return results;
            if (vg._cleared) {
                rcReset('vg');
                for (var j = 0; j < n; j++) { results[j*3] = []; results[j*3+1] = []; results[j*3+2] = ''; }
                return results;
            }
            var grids = vg.grids;
            if (!grids) return results;
        var ovrText = vg.override_text != null ? vg.override_text : (tabData.override_text || '');
        var ovr = ovrText ? '  \u00b7  [' + ovrText + ']' : '';
        var keys = """ + str(VOL_GRID_KEYS) + """;
        var rc = rcScope(tabData, 'vg', 'cdx-vol-grid-' + keys[0]);
        var populated = 0;
        for (var i = 0; i < keys.length; i++) {
            var g = grids[keys[i]];
            if (!g) continue;
            results[i * 3] = g.data || [];
            if (chg(rc, 'c' + i, g.columns)) results[i * 3 + 1] = g.columns || [];
            var vgTitle = '  ' + (g.title || '') + ovr;
            if (chg(rc, 't' + i, vgTitle)) results[i * 3 + 2] = vgTitle;
            populated++;
        }
        console.info('CDX VG: populated ' + populated + '/' + keys.length + ' grids');
        } catch(e) {
            console.error('CDX VG callback error:', e);
        }
        return results;
    }""",
    _VG_DATA_OUTPUTS,
    Input(page.CDX_OPTIONS_WS_TAB_DATA_STORE, 'data'),
    prevent_initial_call=True,
)


# ── Clientside: render trade blotter from WS tab_data store ──────────────

_BLOT_OUTPUTS = []
for _bk in BLOTTER_KEYS:
    _tid = f'cdx-blotter-{_bk}'
    _BLOT_OUTPUTS.extend([
        Output(_tid, 'data', allow_duplicate=True),
        Output(_tid, 'columns', allow_duplicate=True),
        Output(f'{_tid}-full-data', 'data', allow_duplicate=True),
        Output(f'{_tid}-columns', 'data', allow_duplicate=True),
    ])
_BLOT_OUTPUTS.append(Output('cdx-blotter-loading-banner', 'style', allow_duplicate=True))
_BLOT_OUTPUTS.append(Output('cdx-blotter-loading-banner', 'children', allow_duplicate=True))

clientside_callback(
    """function(tabData) {
        var nu = window.dash_clientside.no_update;
""" + _RC_JS + """
        var nBlotters = """ + str(len(BLOTTER_KEYS)) + """;
        var results = new Array(nBlotters * 4 + 2).fill(nu);
        if (tabData && Array.isArray(tabData._changed_tabs)
                && tabData._changed_tabs.indexOf('trade_blotter') < 0) return results;
        if (!tabData || !tabData.trade_blotter) return results;
        var tb = tabData.trade_blotter;
        if (tb._cleared) {
            rcReset('bl');
            for (var j = 0; j < nBlotters; j++) { results[j*4]=[]; results[j*4+1]=[]; results[j*4+2]=[]; results[j*4+3]=[]; }
            results[nBlotters*4] = {display:'none'}; results[nBlotters*4+1] = '';
            return results;
        }
        var blotters = tb.blotters || {};
        var keys = """ + str(BLOTTER_KEYS) + """;
        var rc = rcScope(tabData, 'bl', 'cdx-blotter-' + keys[0]);
        for (var i = 0; i < keys.length; i++) {
            var b = blotters[keys[i]];
            if (!b) continue;
            results[i * 4] = b.data || [];
            results[i * 4 + 2] = b.data || [];
            if (chg(rc, 'c' + i, b.columns)) { results[i * 4 + 1] = b.columns || []; results[i * 4 + 3] = b.columns || []; }
        }
        var bannerIdx = nBlotters * 4;
        var spotRef = tb.live_spot_ref || '';
        var ovr = tb.override_text != null ? tb.override_text : (tabData.override_text || '');
        var info = '';
        if (spotRef) info += 'Spot: ' + spotRef;
        if (ovr) info += (info ? '  \u00b7  ' : '') + '[' + ovr + ']';
        var bannerStyle;
        var bannerText;
        if (tb.still_loading) {
            bannerStyle = {display: 'block', color: '#e08800', fontSize: '11px',
                padding: '4px 8px', backgroundColor: '#fff8e1', borderRadius: '4px', marginBottom: '8px'};
            bannerText = 'Showing raw trades (vol enrichment in progress...)' + (info ? '  \u00b7  ' + info : '');
        } else if (info) {
            bannerStyle = {display: 'block', color: '#006D77', fontSize: '11px',
                padding: '3px 8px', backgroundColor: '#e0f7fa', borderRadius: '4px', marginBottom: '6px'};
            bannerText = info;
        } else {
            bannerStyle = {display: 'none'};
            bannerText = '';
        }
        if (chg(rc, 'banner-style', bannerStyle)) results[bannerIdx] = bannerStyle;
        if (chg(rc, 'banner-text', bannerText)) results[bannerIdx + 1] = bannerText;
        return results;
    }""",
    _BLOT_OUTPUTS,
    Input(page.CDX_OPTIONS_WS_TAB_DATA_STORE, 'data'),
    prevent_initial_call=True,
)


# ── Clientside: render positions (Plotly figures) from WS tab_data ────────

_POS_KEYS = [k for k, _ in POSITION_KEYS]
_POS_OUTPUTS = []
for _pk in _POS_KEYS:
    _POS_OUTPUTS.extend([
        Output(f'cdx-pos-{_pk}', 'figure', allow_duplicate=True),
        Output(f'cdx-pos-{_pk}', 'style', allow_duplicate=True),
    ])

clientside_callback(
    """function(tabData) {
        var nu = window.dash_clientside.no_update;
""" + _RC_JS + """
        var keys = """ + str(_POS_KEYS) + """;
        var results = new Array(keys.length * 2).fill(nu);
        function graphStyle(figure) {
            return {height: Math.max(440, Number(figure && figure.layout && figure.layout.height) || 440) + 'px',
                    width: '100%', minWidth: 0};
        }
        function stableFigure(figure, key) {
            var next = Object.assign({}, figure || {});
            next.layout = Object.assign({}, next.layout || {}, {
                uirevision: (tabData._selection_sig || tabData.product || '') + ':' + key
            });
            return next;
        }
        if (tabData && Array.isArray(tabData._changed_tabs)
                && tabData._changed_tabs.indexOf('positions') < 0) return results;
        if (!tabData || !tabData.positions) return results;
        if (tabData.positions._cleared) {
            rcReset('pos');
            for (var j = 0; j < keys.length; j++) { results[j*2] = {data:[],layout:{}}; results[j*2+1] = {display:'none'}; }
            return results;
        }
        var rc = rcScope(tabData, 'pos', 'cdx-pos-' + keys[0]);
        var figures = tabData.positions.figures || [];
        var figMap = {};
        for (var i = 0; i < figures.length; i++) {
            figMap[figures[i].key] = figures[i];
        }
        for (var i = 0; i < keys.length; i++) {
            var f = figMap[keys[i]];
            if (f && f.figure) {
                results[i * 2] = stableFigure(f.figure, keys[i]);
                var size = graphStyle(f.figure);
                if (chg(rc, 'v' + i, size)) results[i * 2 + 1] = size;
            } else if (f && f.empty) {
                results[i * 2] = stableFigure({
                    data: [],
                    layout: {
                        xaxis: {visible: false},
                        yaxis: {visible: false},
                        annotations: [{
                            text: 'No position data for this selection',
                            x: 0.5, y: 0.5, xref: 'paper', yref: 'paper',
                            showarrow: false
                        }]
                    }
                }, keys[i]);
                var size = graphStyle(null);
                if (chg(rc, 'v' + i, size)) results[i * 2 + 1] = size;
            } else {
                if (chg(rc, 'v' + i, false)) results[i * 2 + 1] = {display: 'none'};
            }
        }
        return results;
    }""",
    _POS_OUTPUTS,
    Input(page.CDX_OPTIONS_WS_TAB_DATA_STORE, 'data'),
    prevent_initial_call=True,
)


# ── Clientside: render expiry charts (multi-instance) from WS tab_data ───

_EXP_OUTPUTS = [
    Output('cdx-exp-vols', 'figure', allow_duplicate=True),
    Output('cdx-exp-vols', 'style', allow_duplicate=True),
    Output('cdx-exp-spot-ts', 'figure', allow_duplicate=True),
    Output('cdx-exp-spot-ts-wrap', 'style', allow_duplicate=True),
]
for _ei in range(MAX_EXPIRY_CHART_SETS):
    _eid = f'cdx-exp-chart-{_ei}'
    _EXP_OUTPUTS.extend([
        Output(f'{_eid}-wrap', 'style', allow_duplicate=True),
        Output(f'{_eid}-header', 'children', allow_duplicate=True),
        Output(f'{_eid}-vol-hist', 'figure', allow_duplicate=True),
        Output(f'{_eid}-wrap', 'data-chart-key'),
    ])

clientside_callback(
    """function(tabData) {
        var nu = window.dash_clientside.no_update;
""" + _RC_JS + """
        var N = """ + str(MAX_EXPIRY_CHART_SETS) + """;
        var results = new Array(4 + N * 4).fill(nu);
        function chartCardStyle(display) {
            return {
                display: display,
                backgroundColor: '#ffffff',
                border: '1px solid #d9e2ec',
                borderRadius: '10px',
                boxShadow: '0 2px 8px rgba(15, 35, 55, 0.06)',
                padding: '9px',
                marginBottom: '10px', minWidth: 0, maxWidth: '100%', boxSizing: 'border-box'
            };
        }
        function stableFigure(figure, key) {
            var next = Object.assign({}, figure || {});
            next.layout = Object.assign({}, next.layout || {}, {
                uirevision: (tabData._selection_sig || tabData.product || '') + ':' + key
            });
            return next;
        }
        function graphStyle(figure) {
            return {height: Math.max(440, Number(figure && figure.layout && figure.layout.height) || 440) + 'px',
                    width: '100%', minWidth: 0};
        }
        if (tabData && Array.isArray(tabData._changed_tabs)
                && tabData._changed_tabs.indexOf('expiry_charts') < 0) return results;
        if (!tabData || !tabData.expiry_charts) return results;
        var ec = tabData.expiry_charts;
        if (ec._cleared) {
            rcReset('exp');
            results[1] = {display:'none'}; results[3] = {display:'none'};
            for (var j = 0; j < N; j++) { results[4+j*4] = {display:'none'}; results[7+j*4] = ''; }
            return results;
        }
        var rc = rcScope(tabData, 'exp', 'cdx-exp-vols');
        if (ec.exp_vols_figure) {
            results[0] = stableFigure(ec.exp_vols_figure, 'exp-vols');
            var size = graphStyle(ec.exp_vols_figure);
            if (chg(rc, 'exp-vols-visible', size)) results[1] = size;
        } else if (chg(rc, 'exp-vols-visible', false)) {
            results[1] = {display: 'none'};
        }
        if (ec.spot_ts_figure) {
            results[2] = stableFigure(ec.spot_ts_figure, 'spot-ts');
            if (chg(rc, 'spot-visible', true)) results[3] = chartCardStyle('block');
        } else if (chg(rc, 'spot-visible', false)) {
            results[3] = {display: 'none'};
        }
        var charts = (ec.charts || []).filter(Boolean).slice().sort(function(a, b) {
            return String(a.expiry).localeCompare(String(b.expiry)) || Number(a.strike || 0) - Number(b.strike || 0);
        });
        for (var i = 0; i < N; i++) {
            var base = 4 + i * 4;
            if (i < charts.length && charts[i]) {
                var c = charts[i];
                var hdr = 'Expiry ' + String(c.expiry || '').slice(0, 10);
                if (c.strike != null) hdr += '  ·  Strike ' + c.strike;
                if (chg(rc, 'chart-visible-' + i, true)) results[base] = chartCardStyle('block');
                if (chg(rc, 'chart-header-' + i, hdr)) results[base + 1] = hdr;
                results[base + 2] = stableFigure(
                    c.vol_hist_figure,
                    'vol-hist-' + String(c.expiry || '') + ':' + String(c.strike == null ? i : c.strike)
                );
                results[base + 3] = 'history:' + String(tabData.product || '') + ':'
                    + String(c.expiry || '').slice(0, 10) + ':' + String(c.strike == null ? '' : Number(c.strike));
            } else if (chg(rc, 'chart-visible-' + i, false)) {
                results[base] = {display: 'none'};
                results[base + 1] = '';
                results[base + 2] = {};
                results[base + 3] = '';
            }
        }
        return results;
    }""",
    _EXP_OUTPUTS,
    Input(page.CDX_OPTIONS_WS_TAB_DATA_STORE, 'data'),
    prevent_initial_call=True,
)


# ── Clientside: render option stack (multi-grid) from WS tab_data ────────

_OS_OUTPUTS = []
for _i in range(MAX_OPTION_STACKS):
    _sid = f'cdx-option-ladder-{_i}'
    _OS_OUTPUTS.extend([
        Output(f'{_sid}', 'data', allow_duplicate=True),
        Output(f'{_sid}', 'columns', allow_duplicate=True),
        Output(f'{_sid}-title', 'children', allow_duplicate=True),
        Output(f'{_sid}-subtitle', 'children', allow_duplicate=True),
        Output(f'{_sid}-wrap', 'style', allow_duplicate=True),
    ])

clientside_callback(
    """function(tabData) {
        var nu = window.dash_clientside.no_update;
""" + _RC_JS + """
        var N = """ + str(MAX_OPTION_STACKS) + """;
        var results = new Array(N * 5).fill(nu);
        function stackCardStyle(display) {
            return {
                display: display,
                backgroundColor: '#ffffff',
                border: '1px solid #d9e2ec',
                borderRadius: '10px',
                boxShadow: '0 2px 8px rgba(15, 35, 55, 0.06)',
                padding: '9px',
                marginBottom: '10px'
            };
        }
        if (tabData && Array.isArray(tabData._changed_tabs)
                && tabData._changed_tabs.indexOf('option_stack') < 0) return results;
        if (!tabData || !tabData.option_stack) return results;
        if (tabData.option_stack._cleared) {
            rcReset('os');
            for (var j = 0; j < N; j++) { results[j*5]=[]; results[j*5+1]=[]; results[j*5+2]=''; results[j*5+3]=''; results[j*5+4]={display:'none'}; }
            return results;
        }
        var stacks = tabData.option_stack.stacks || [];
        var rc = rcScope(tabData, 'os', 'cdx-option-ladder-0');
        var stackOvrText = tabData.option_stack.override_text != null
            ? tabData.option_stack.override_text : (tabData.override_text || '');
        var sigs = tabData._tab_sigs || {};
        var vgr = tabData.vol_grids;
        var calcSig = (tabData.calc_context && tabData.calc_context.calc_sig) || tabData._calc_sig || tabData._selection_sig;
        var spotInSync = (vgr && vgr.live_spot_ref && sigs.vol_grids && sigs.option_stack
            && sigs.vol_grids === sigs.option_stack && sigs.option_stack === calcSig);
        for (var i = 0; i < N; i++) {
            if (i < stacks.length) {
                var s = stacks[i];
                var cols = (s.columns || []).map(function(c) { return {name: c, id: c}; });
                var title = 'Option Stack: ' + (s.product || '');
                if (s.expiry) title += ' | ' + s.expiry;
                if (s.strike != null) title += ' | K=' + s.strike;
                if (spotInSync) title += '  \u00b7  Spot: ' + vgr.live_spot_ref;
                if (stackOvrText) title += '  \u00b7  [' + stackOvrText + ']';
                results[i * 5] = s.records || [];
                if (chg(rc, 'c' + i, cols)) results[i * 5 + 1] = cols;
                if (chg(rc, 't' + i, title)) results[i * 5 + 2] = title;
                if (chg(rc, 'v' + i, true)) results[i * 5 + 4] = stackCardStyle('block');
            } else {
                if (chg(rc, 'v' + i, false)) {
                    results[i * 5] = [];
                    results[i * 5 + 1] = [];
                    results[i * 5 + 2] = '';
                    results[i * 5 + 3] = '';
                    results[i * 5 + 4] = {display: 'none'};
                }
            }
        }
        return results;
    }""",
    _OS_OUTPUTS,
    Input(page.CDX_OPTIONS_WS_TAB_DATA_STORE, 'data'),
    prevent_initial_call=True,
)


# ── Clientside: update dropdowns from WS defaults store ──────────────────

clientside_callback(
    """function(defs, curProduct, curStrike, curExpiry, memory, committed, currentTab) {
        var nu = window.dash_clientside.no_update;
        if (!defs) return [nu, nu, nu, nu, nu, nu, nu, nu, nu];
        function emptySel(v) {
            return !v || (Array.isArray(v) && !v.length);
        }
        function normSel(v, numeric) {
            var arr = Array.isArray(v) ? v : (v == null ? [] : [v]);
            return arr.filter(function(x) { return x != null && x !== ''; }).map(function(x) {
                if (numeric) {
                    var n = Number(x);
                    return isNaN(n) ? String(x) : String(n);
                }
                return String(x);
            });
        }
        function sameSel(a, b, numeric) {
            return JSON.stringify(normSel(a, numeric)) === JSON.stringify(normSel(b, numeric));
        }
        function asList(v) {
            return Array.isArray(v) ? v : (v == null || v === '' ? [] : [v]);
        }
        function needsFullSelection(tab) {
            return tab === 'expiry-charts' || tab === 'option-stack';
        }
        function parseUrlStrike(raw) {
            return String(raw || '').split(',').map(function(s){ return s.trim(); }).filter(Boolean).map(function(s) {
                var n = Number(s);
                if (!isNaN(n)) return n;
                var m = s.match(/^([\\d.]+)\\s*[kK]$/);
                return m ? Number(m[1]) * 1000 : s;
            });
        }
        function parseUrlExpiry(raw) {
            return String(raw || '').split(',').map(function(s){ return s.trim(); }).filter(Boolean);
        }
        var productOptions = defs.product_options || [];
        var strikeOptions = defs.strike_options || [];
        var expiryOptions = defs.expiry_options || [];
        var productValues = productOptions.map(function(o) { return o.value; });
        var strikeMap = {};
        strikeOptions.forEach(function(o) {
            var n = Number(o.value);
            strikeMap[isNaN(n) ? String(o.value) : String(n)] = o.value;
        });
        var expiryMap = {};
        expiryOptions.forEach(function(o) {
            var s = String(o.value);
            expiryMap[s] = o.value;
            expiryMap[s.slice(0, 10)] = o.value;
        });
        function matchStrike(v) {
            return asList(v).map(function(x) {
                var n = Number(x);
                var key = isNaN(n) ? String(x) : String(n);
                return Object.prototype.hasOwnProperty.call(strikeMap, key) ? strikeMap[key] : null;
            }).filter(function(x) { return x != null; });
        }
        function matchExpiry(v) {
            return asList(v).map(function(x) {
                var s = String(x);
                return expiryMap[s] || expiryMap[s.slice(0, 10)] || null;
            }).filter(function(x) { return x != null; });
        }
        var params = new URLSearchParams(window.location.search || '');
        var urlProduct = params.get('product');
        urlProduct = (urlProduct && productValues.indexOf(urlProduct.trim()) >= 0) ? urlProduct.trim() : null;
        var hydrateProduct = !curProduct && !!(urlProduct || defs.active_product) && !(committed && committed.product);
        var nextProduct = curProduct || (committed && committed.product) || (hydrateProduct ? (urlProduct || defs.active_product) : null);
        var saved = (memory && nextProduct && memory[nextProduct]) || {};
        var sameCommittedProduct = !!(committed && committed.product === nextProduct);
        var hydrateStrike = emptySel(curStrike) && (!sameCommittedProduct || emptySel(committed && committed.strike));
        var urlStrike = params.has('strike') ? matchStrike(parseUrlStrike(params.get('strike'))) : [];
        var nextStrike = hydrateStrike ? (urlStrike.length ? urlStrike : matchStrike(saved.strike)) : asList(curStrike);
        if (hydrateStrike && !nextStrike.length) nextStrike = matchStrike(defs.strike);
        var hydrateExpiry = emptySel(curExpiry) && (!sameCommittedProduct || emptySel(committed && committed.expiry));
        var urlExpiry = params.has('expiry') ? matchExpiry(parseUrlExpiry(params.get('expiry'))) : [];
        var nextExpiry = hydrateExpiry ? (urlExpiry.length ? urlExpiry : matchExpiry(saved.expiry)) : asList(curExpiry);
        if (hydrateExpiry && !nextExpiry.length) nextExpiry = matchExpiry(defs.expiry);
        var activeTab = currentTab || 'vol-grids';
        var selectionReady = !needsFullSelection(activeTab) || (nextStrike.length && nextExpiry.length);
        var prodVal = hydrateProduct ? nextProduct : nu;
        var strikeVal = hydrateStrike ? nextStrike : nu;
        var expiryVal = hydrateExpiry ? nextExpiry : nu;
        var committedVal = nu;
        if (selectionReady && nextProduct && (!committed
                || committed.product !== nextProduct
                || !sameSel(committed.strike, nextStrike, true)
                || !sameSel(committed.expiry, nextExpiry, false))) {
            committedVal = {product: nextProduct, strike: nextStrike, expiry: nextExpiry, ts: Date.now()};
        }
        return [
            productOptions || nu,
            prodVal,
            strikeOptions || nu,
            strikeVal,
            expiryOptions || nu,
            expiryVal,
            productOptions || nu,
            productOptions || nu,
            committedVal
        ];
    }""",
    Output(page.CDX_OPTIONS_ACTIVE_PRODUCT_ID, 'options', allow_duplicate=True),
    Output(page.CDX_OPTIONS_ACTIVE_PRODUCT_ID, 'value', allow_duplicate=True),
    Output(page.CDX_OPTIONS_STRIKE_ID, 'options', allow_duplicate=True),
    Output(page.CDX_OPTIONS_STRIKE_ID, 'value', allow_duplicate=True),
    Output(page.CDX_OPTIONS_EXPIRY_ID, 'options', allow_duplicate=True),
    Output(page.CDX_OPTIONS_EXPIRY_ID, 'value', allow_duplicate=True),
    Output(page.CDX_RV_OPT1_PRODUCT_ID, 'options', allow_duplicate=True),
    Output(page.CDX_RV_OPT2_PRODUCT_ID, 'options', allow_duplicate=True),
    Output(page.CDX_OPTIONS_DD_COMMITTED, 'data', allow_duplicate=True),
    Input(page.CDX_OPTIONS_WS_DEFAULTS_STORE, 'data'),
    State(page.CDX_OPTIONS_ACTIVE_PRODUCT_ID, 'value'),
    State(page.CDX_OPTIONS_STRIKE_ID, 'value'),
    State(page.CDX_OPTIONS_EXPIRY_ID, 'value'),
    State(page.CDX_OPTIONS_PRODUCT_MEMORY, 'data'),
    State(page.CDX_OPTIONS_DD_COMMITTED, 'data'),
    State(page.CDX_OPTIONS_TABS_ID, 'value'),
    prevent_initial_call=True,
)


# ── WS connection status indicator ────────────────────────────────────────

clientside_callback(
    """function(n, state, recovery) {
        var nu = window.dash_clientside.no_update;
        var unchanged = [nu, nu, nu, nu];
        if (!state) return unchanged;
        var previous = recovery || {};
        if (state.readyState === 1) {
            if (previous.attempt) return [nu, {}, nu, nu];
            return unchanged;
        }
        if (state.readyState !== 3) return unchanged;
        var now = Date.now();
        var delay = Math.min(30000, 1000 * Math.pow(2, Math.min(previous.attempt || 0, 5)));
        if (previous.ts && now - previous.ts < delay) return unchanged;
        var attempt = (previous.attempt || 0) + 1;
        var socket = {namespace: 'dash_extensions', type: 'WebSocket', props: {
            id: 'cdx-options-ws', url: '/cdx-options/ws', state: {readyState: 0}
        }};
        return [
            {namespace: 'dash_html_components', type: 'Div', props: {key: 'reconnect-' + now + '-' + attempt, children: socket}},
            {attempt: attempt, ts: now}, {phase: 'reconnecting'},
            {pending: false, stage: 'reconnecting', detail: 'Reconnecting to live updates…'}
        ];
    }""",
    Output(page.CDX_OPTIONS_WS_CONTAINER, 'children'),
    Output(page.CDX_OPTIONS_WS_RECOVERY, 'data'),
    Output(page.CDX_OPTIONS_LIFECYCLE, 'data', allow_duplicate=True),
    Output(page.CDX_OPTIONS_LOADING_STORE, 'data', allow_duplicate=True),
    Input(page.CDX_OPTIONS_STATUS_TICK, 'n_intervals'),
    Input(page.CDX_OPTIONS_WS, 'state'),
    State(page.CDX_OPTIONS_WS_RECOVERY, 'data'),
    prevent_initial_call=True,
)

clientside_callback(
    """function(state) {
        var nu = window.dash_clientside.no_update;
        if (!state) return [nu, nu];
        var rs = state.readyState;
        if (rs === 1) return [{color: '#1b7a3d', fontSize: '11px', cursor: 'default', lineHeight: '1'}, 'Connected'];
        if (rs === 0) return [{color: '#8a6d1d', fontSize: '11px', cursor: 'default', lineHeight: '1'}, 'Connecting...'];
        return [{color: '#c0392b', fontSize: '11px', cursor: 'default', lineHeight: '1'}, 'Disconnected'];
    }""",
    Output(page.CDX_OPTIONS_WS_STATUS, 'style'),
    Output(page.CDX_OPTIONS_WS_STATUS, 'title'),
    Input(page.CDX_OPTIONS_WS, 'state'),
    prevent_initial_call=True,
)


# ── Clientside: self-healing — auto-subscribe when tab_data missing ──────

clientside_callback(
    """function(activeTab, wsState, defs, tabData, committed, loading, lifecycle, currentLog, sessionData, override) {
        var nu = window.dash_clientside.no_update;
        if (!wsState || wsState.readyState !== 1) return [nu, nu, nu];
        if (!lifecycle || lifecycle.phase !== 'ready') return [nu, nu, nu];
        if (!defs || !committed || !committed.product) return [nu, nu, nu];
        if (committed.awaiting_defaults) return [nu, nu, nu];
        function normSel(v, numeric) {
            var arr = Array.isArray(v) ? v : (v == null ? [] : [v]);
            return arr.filter(function(x) { return x != null && x !== ''; }).map(function(x) {
                if (numeric) {
                    var n = Number(x);
                    return isNaN(n) ? String(x) : String(n);
                }
                return String(x);
            });
        }
        function needsFullSelection(tab) {
            return tab === 'expiry-charts' || tab === 'option-stack';
        }
        function sigFor(product, strike, expiry) {
            return JSON.stringify({
                product: product == null ? '' : String(product),
                strike: normSel(strike, true),
                expiry: normSel(expiry, false)
            });
        }
        function activeTabKey(tab) {
            tab = tab || 'vol-grids';
            return {
                'vol-grids': 'vol_grids',
                'trade-blotter': 'trade_blotter',
                'positions': 'positions',
                'expiry-charts': 'expiry_charts',
                'option-stack': 'option_stack'
            }[tab];
        }
        function nowText() {
            var d = new Date();
            function pad(v) { return String(v).padStart(2, '0'); }
            return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
        }
        function selectionText(product, strike, expiry) {
            var parts = [];
            var expiries = normSel(expiry, false).map(function(x) { return String(x).slice(0, 10); });
            var strikes = normSel(strike, true);
            if (product) parts.push(String(product));
            if (expiries.length) parts.push('Exp=' + expiries.join(', '));
            if (strikes.length) parts.push('K=' + strikes.join(', '));
            return parts.join(' | ');
        }
        function appendLog(log, requestText, text) {
            var next = Array.isArray(log) ? log.slice() : [];
            var last = next.length ? next[next.length - 1] : null;
            if (Array.isArray(last) && last[1] === requestText && last[2] === text) return null;
            next.push([nowText(), requestText, text]);
            return next.slice(-6);
        }
        function makeRequestId(prefix) {
            var id = (window.crypto && window.crypto.randomUUID)
                ? window.crypto.randomUUID()
                : String(Date.now()) + '-' + Math.random().toString(36).slice(2);
            return prefix + '-' + id;
        }
        var trigger = (window.dash_clientside.callback_context.triggered || [])[0];
        var triggerId = trigger && trigger.prop_id ? trigger.prop_id.split('.')[0] : '';
        var tabValue = activeTab || 'vol-grids';
        if (needsFullSelection(tabValue) && (!normSel(committed.strike, true).length || !normSel(committed.expiry, false).length))
            return [nu, nu, nu];
        var key = activeTabKey(tabValue);
        if (!key) return [nu, nu, nu];
        var ov = override || {};
        var hasOverride = (ov.ref_px != null) || (ov.ref_bps != null)
            || (ov.vol_shift != null && ov.vol_shift !== 0)
            || (ov.vol_lookback && ov.vol_lookback !== '5b');
        var requestKey = JSON.stringify({
            command: hasOverride ? 'override' : 'subscribe',
            product: committed.product,
            strike: normSel(committed.strike, true),
            expiry: normSel(committed.expiry, false),
            tab: key || '',
            ref_px: ov.ref_px == null ? null : ov.ref_px,
            ref_bps: ov.ref_bps == null ? null : ov.ref_bps,
            vol_shift: ov.vol_shift == null ? null : ov.vol_shift,
            vol_lookback: ov.vol_lookback || '5b'
        });
        var staleMs = (loading && loading.ts) ? (Date.now() - loading.ts) : 0;
        if (loading && loading.pending && loading.request_key === requestKey && staleMs < 120000) return [nu, nu, nu];
        if (triggerId !== 'cdx-options-tabs' && loading && loading.pending && staleMs < 30000)
            return [nu, nu, nu];
        var wantedSig = sigFor(committed.product, committed.strike, committed.expiry);
        var currentCalcSig = tabData && ((tabData.calc_context && tabData.calc_context.calc_sig) || tabData._calc_sig || tabData._selection_sig);
        if (key && tabData && tabData._selection_sig === wantedSig && tabData._tab_sigs && tabData._tab_sigs[key] === currentCalcSig) return [nu, nu, nu];
        if (loading && loading.heal_capped === requestKey) return [nu, nu, nu];
        var healAttempts = (loading && loading.heal_key === requestKey) ? (loading.heal_attempts || 0) : 0;
        if (healAttempts >= 5) {
            return [nu, Object.assign({}, loading || {}, {pending: false, stage: 'error',
                detail: 'Auto-refresh paused after 5 attempts — switch tabs or press Refresh',
                heal_capped: requestKey}), nu];
        }
        if (triggerId === 'cdx-options-tabs' && !(staleMs >= 30000)) {
            if (!key) return [nu, nu, nu];
            console.info('CDX self-heal: tab switch missing ' + key + ', requesting active tab only');
        }
        console.info('CDX self-heal: tab_data missing for ' + committed.product
            + (staleMs > 30000 ? ' (loading timed out after ' + Math.round(staleMs/1000) + 's)' : '')
            + ', auto-' + (hasOverride ? 'overriding' : 'subscribing'));
        var nextLog = nu;
        var diagCategory = null;
        if (staleMs >= 30000) {
            diagCategory = 'loading_timeout';
            nextLog = appendLog(
                currentLog,
                selectionText(committed.product, committed.strike, committed.expiry),
                'Loading timed out after ' + Math.round(staleMs / 1000) + 's; re-' + (hasOverride ? 'overriding' : 'subscribing') + ' ' + (key || 'active_tab')
            );
            if (!nextLog) nextLog = nu;
        }
        var msg = {
            command: hasOverride ? 'override' : 'subscribe',
            session_id: sessionData && sessionData.session_id ? sessionData.session_id : null,
            product: committed.product,
            strike: committed.strike,
            expiry: committed.expiry,
            active_tab: tabValue,
            requested_tabs: key ? [key] : null,
            diag_category: diagCategory,
            diag_reason: diagCategory ? 'missing_tab_data' : null,
            diag_tab: diagCategory ? (key || 'active_tab') : null,
            diag_waited_ms: diagCategory ? staleMs : null,
            ts: Date.now()
        };
        if (hasOverride) {
            msg.ref_px = (ov.ref_px != null) ? ov.ref_px : null;
            msg.ref_bps = (ov.ref_bps != null) ? ov.ref_bps : null;
            msg.vol_shift_bps = (ov.vol_shift != null) ? ov.vol_shift : null;
            msg.vol_lookback = ov.vol_lookback || '5b';
        }
        var requestId = makeRequestId('heal');
        msg.request_id = requestId;
        return [
            JSON.stringify(msg),
            {pending: true, product: committed.product, strike: committed.strike, expiry: committed.expiry,
             request_id: requestId, request_key: requestKey, active_tab: tabValue, ts: Date.now(),
             heal_key: requestKey, heal_attempts: healAttempts + 1},
            nextLog
        ];
    }""",
    Output(page.CDX_OPTIONS_WS, 'send', allow_duplicate=True),
    Output(page.CDX_OPTIONS_LOADING_STORE, 'data', allow_duplicate=True),
    Output(page.CDX_OPTIONS_EVENT_LOG_STORE, 'data', allow_duplicate=True),
    Input(page.CDX_OPTIONS_TABS_ID, 'value'),
    Input(page.CDX_OPTIONS_WS, 'state'),
    State(page.CDX_OPTIONS_WS_DEFAULTS_STORE, 'data'),
    State(page.CDX_OPTIONS_WS_TAB_DATA_STORE, 'data'),
    State(page.CDX_OPTIONS_DD_COMMITTED, 'data'),
    State(page.CDX_OPTIONS_LOADING_STORE, 'data'),
    State(page.CDX_OPTIONS_LIFECYCLE, 'data'),
    State(page.CDX_OPTIONS_EVENT_LOG_STORE, 'data'),
    State(page.CDX_OPTIONS_SESSION_STORE, 'data'),
    State(page.CDX_OPTIONS_OVERRIDE_STORE, 'data'),
    prevent_initial_call=True,
)


# ── Clientside: update status text & badge from WS tab_data ──────────────

clientside_callback(
    """function(tabData, loading) {
        var nu = window.dash_clientside.no_update;
        if (!tabData) return [nu, nu];
        function timeText(value) {
            if (!value) return '';
            if (/^\d{4}-\d{2}-\d{2}$/.test(String(value))) return String(value);
            var dt = new Date(value);
            if (isNaN(dt.getTime())) return String(value);
            return dt.toLocaleString([], {
                month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit'
            });
        }
        var text = tabData.update_text || '';
        if (tabData.override_text) text += '  \u00b7  [' + tabData.override_text + ']';
        if (tabData._data_asof) text += (text ? '  \u00b7  ' : '') + 'Data as of ' + timeText(tabData._data_asof);
        if (tabData._materialized_at) text += '  \u00b7  View built ' + timeText(tabData._materialized_at);
        if (tabData._generation != null) text += '  \u00b7  Gen ' + tabData._generation;
        if (tabData._refresh_generation != null && tabData._refresh_generation !== tabData._generation) {
            text += '\u2192' + tabData._refresh_generation;
        }
        if (tabData._source) text += '  \u00b7  ' + tabData._source;
        if (tabData._refresh_duration_ms != null) text += '  \u00b7  ' + Math.round(tabData._refresh_duration_ms) + 'ms';
        if (tabData._display_state === 'cached') text += '  \u00b7  Browser cached while refreshing';
        if (tabData._display_state === 'cached-stale') {
            text += '  \u00b7  Cached stale';
            if (tabData._stale_reason) text += ' (' + tabData._stale_reason + ')';
            text += '  \u00b7  Refreshing';
        }
        if (tabData._display_state === 'refresh-failed' || (loading && loading.stage === 'error')) {
            text += '  \u00b7  Refresh failed; showing last usable view';
        }
        return [text || nu, tabData.grid_badge || nu];
    }""",
    Output(page.CDX_OPTIONS_LAST_UPDATE_TEXT, 'children', allow_duplicate=True),
    Output(page.CDX_OPTIONS_GRID_BADGE, 'children', allow_duplicate=True),
    Input(page.CDX_OPTIONS_WS_TAB_DATA_STORE, 'data'),
    Input(page.CDX_OPTIONS_LOADING_STORE, 'data'),
    prevent_initial_call=True,
)

clientside_callback(
    """function(loading, tabData, tabValue, committed, tick) {
        function normSel(v, numeric) {
            var arr = Array.isArray(v) ? v : (v == null ? [] : [v]);
            return arr.filter(function(x) { return x != null && x !== ''; }).map(function(x) {
                if (numeric) {
                    var n = Number(x);
                    return isNaN(n) ? String(x) : String(n);
                }
                return String(x);
            });
        }
        function needsFullSelection(tab) {
            return tab === 'expiry-charts' || tab === 'option-stack';
        }
        function sigFor(product, strike, expiry) {
            return JSON.stringify({
                product: product == null ? '' : String(product),
                strike: normSel(strike, true),
                expiry: normSel(expiry, false)
            });
        }
        var hidden = [{display: 'none'}, ''];
        var tab = tabValue || 'vol-grids';
        var key = {
            'vol-grids': 'vol_grids',
            'trade-blotter': 'trade_blotter',
            'positions': 'positions',
            'expiry-charts': 'expiry_charts',
            'option-stack': 'option_stack'
        }[tab];
        function hasRows(items) {
            return Object.keys(items || {}).some(function(k) {
                return items[k] && Array.isArray(items[k].data) && items[k].data.length;
            });
        }
        function usableTab(data, tabKey) {
            var payload = data && data[tabKey];
            if (!payload || payload._cleared || payload._empty || payload._error) return false;
            if (tabKey === 'vol_grids') return hasRows(payload.grids);
            if (tabKey === 'trade_blotter') return hasRows(payload.blotters);
            if (tabKey === 'positions') return Array.isArray(payload.figures) && payload.figures.length > 0;
            if (tabKey === 'expiry_charts') {
                return !!payload.exp_vols_figure || !!payload.spot_ts_figure
                    || (Array.isArray(payload.charts) && payload.charts.length > 0);
            }
            if (tabKey === 'option_stack') {
                return Array.isArray(payload.stacks) && payload.stacks.some(function(stack) {
                    return stack && Array.isArray(stack.records) && stack.records.length > 0;
                });
            }
            return false;
        }
        if (!key) return hidden;
        if (!(loading && loading.pending)) return hidden;
        var elapsed = Math.max(0, Math.floor((Date.now() - (loading.ts || Date.now())) / 1000));
        var cached = !!(tabData && tabData.product === loading.product
            && (tabData._display_state === 'cached' || tabData._display_state === 'cached-stale')
            && usableTab(tabData, key));
        var labels = {
            'vol-grids': 'Vol Grids',
            'trade-blotter': 'Trade Blotter',
            'positions': 'Positions & Greeks',
            'expiry-charts': 'Expiry Charts',
            'option-stack': 'Option Stack'
        };
        var stage = loading.stage || 'queued';
        var text;
        if (cached) {
            var cacheTime = tabData._received_at ? new Date(tabData._received_at).toLocaleTimeString([], {
                hour: '2-digit', minute: '2-digit', second: '2-digit'
            }) : '';
            text = 'Cached view' + (cacheTime ? ' from ' + cacheTime : '') + ' \u00b7 Refreshing ' + labels[tab] + ' \u00b7 ' + elapsed + 's';
        } else if (stage === 'defaults' || stage === 'defaults_ready') {
            text = (stage === 'defaults' ? 'Loading product defaults' : 'Defaults ready') + ' \u00b7 ' + elapsed + 's';
        } else {
            text = 'Refreshing ' + labels[tab] + ' \u00b7 ' + elapsed + 's';
        }
        return [{
            display: 'inline-flex',
            alignItems: 'center',
            padding: '2px 8px',
            marginLeft: '8px',
            borderRadius: '999px',
            border: cached ? '1px solid #e0a800' : '1px solid #b8c7d9',
            backgroundColor: cached ? '#fff8e1' : '#ffffff',
            color: cached ? '#8a6d1d' : '#006D77',
            fontSize: '10px',
            fontWeight: '800',
            letterSpacing: '0.2px',
            boxShadow: '0 2px 8px rgba(15, 35, 55, 0.06)'
        }, text];
    }""",
    Output(page.CDX_OPTIONS_INLINE_BADGE, 'style'),
    Output(page.CDX_OPTIONS_INLINE_BADGE, 'children'),
    Input(page.CDX_OPTIONS_LOADING_STORE, 'data'),
    Input(page.CDX_OPTIONS_WS_TAB_DATA_STORE, 'data'),
    Input(page.CDX_OPTIONS_TABS_ID, 'value'),
    Input(page.CDX_OPTIONS_DD_COMMITTED, 'data'),
    Input(page.CDX_OPTIONS_STATUS_TICK, 'n_intervals'),
    prevent_initial_call=True,
)

# ── Loading state: clear pending when data arrives (no blocking overlay) ──

clientside_callback(
    """function(tabData, loading, tabValue) {
        if (!tabData) return window.dash_clientside.no_update;
        if (loading && loading.pending && loading.product && tabData.product !== loading.product)
            return window.dash_clientside.no_update;
        function normSel(v, numeric) {
            var arr = Array.isArray(v) ? v : (v == null ? [] : [v]);
            return arr.filter(function(x) { return x != null && x !== ''; }).map(function(x) {
                if (numeric) {
                    var n = Number(x);
                    return isNaN(n) ? String(x) : String(n);
                }
                return String(x);
            });
        }
        function needsFullSelection(tab) {
            return tab === 'expiry-charts' || tab === 'option-stack';
        }
        function sigFor(product, strike, expiry) {
            return JSON.stringify({
                product: product == null ? '' : String(product),
                strike: normSel(strike, true),
                expiry: normSel(expiry, false)
            });
        }
        var activeTab = tabValue || 'vol-grids';
        var key = {
            'vol-grids': 'vol_grids',
            'trade-blotter': 'trade_blotter',
            'positions': 'positions',
            'expiry-charts': 'expiry_charts',
            'option-stack': 'option_stack'
        }[activeTab];
        var staleMs = (loading && loading.ts) ? (Date.now() - loading.ts) : 0;
        var timedOut = staleMs > 30000;
        function done() {
            var next = Object.assign({}, loading || {});
            next.pending = false;
            return next;
        }
        if (loading && loading.pending && !timedOut) {
            if (needsFullSelection(activeTab) && (!normSel(loading.strike, true).length || !normSel(loading.expiry, false).length))
                return done();
            if (!key) return done();
            var wantedSig = sigFor(loading.product, loading.strike, loading.expiry);
            var currentCalcSig = (tabData.calc_context && tabData.calc_context.calc_sig) || tabData._calc_sig || tabData._selection_sig;
            if (!(tabData._selection_sig === wantedSig && tabData._tab_sigs && tabData._tab_sigs[key] === currentCalcSig))
                return window.dash_clientside.no_update;
        }
        return done();
    }""",
    Output(page.CDX_OPTIONS_LOADING_STORE, 'data', allow_duplicate=True),
    Input(page.CDX_OPTIONS_WS_TAB_DATA_STORE, 'data'),
    State(page.CDX_OPTIONS_LOADING_STORE, 'data'),
    State(page.CDX_OPTIONS_TABS_ID, 'value'),
    prevent_initial_call=True,
)


# ── Clientside: spot update (delta-one strip + ref placeholders) ─────────

clientside_callback(
    """function(wsData, activeProduct) {
        var nu = window.dash_clientside.no_update;
        if (!wsData || (!wsData.items && !wsData.spots)) return [nu, nu, nu];

        var pxPh = 'px', bpsPh = 'bps';
        if (activeProduct && wsData.spots && wsData.spots[activeProduct]) {
            var entry = wsData.spots[activeProduct];
            if (entry.px != null) pxPh = entry.px.toFixed(2);
            if (entry.bps != null) bpsPh = entry.bps.toFixed(2);
        }

        var items = wsData.items || [];
        if (!items.length) return [nu, pxPh, bpsPh];

        var children = items.map(function(d) {
            var spd = d.spread, px = d.price, chg = d.chg, otr = d.is_otr;
            var spdStr = (spd != null && !isNaN(spd)) ? spd.toFixed(1) : '--';
            var pxStr = (px != null && !isNaN(px)) ? px.toFixed(2) : '--';
            var chgColor = (chg != null && chg < 0) ? '#1b7a3d' : (chg != null && chg > 0) ? '#c0392b' : '#65758b';
            var chgStr = (chg != null && !isNaN(chg)) ? (chg >= 0 ? '+' : '') + chg.toFixed(1) : '';
            var labelColor = otr ? '#172033' : '#65758b';
            var bgColor = otr ? '#ffffff' : '#f8fafd';
            var borderColor = otr ? '#006D77' : '#d9e2ec';

            return {
                namespace: 'dash_html_components', type: 'Span',
                props: {
                    style: {
                        display: 'inline-flex', alignItems: 'center', gap: '6px',
                        padding: '3px 10px', borderRadius: '999px', marginRight: '2px',
                        backgroundColor: bgColor, border: '1px solid ' + borderColor,
                        fontSize: '11px', boxShadow: otr ? '0 2px 8px rgba(0,109,119,0.12)' : 'none'
                    },
                    children: [
                        {namespace: 'dash_html_components', type: 'Span', props: {children: d.product || '', style: {color: labelColor, fontWeight: otr ? '700' : '500'}}},
                        {namespace: 'dash_html_components', type: 'Span', props: {children: pxStr + ' px', style: {color: '#8a6d1d', fontWeight: '600'}}},
                        {namespace: 'dash_html_components', type: 'Span', props: {children: spdStr + ' spd', style: {color: '#0b57a4', fontWeight: '600'}}},
                        chgStr ? {namespace: 'dash_html_components', type: 'Span', props: {children: chgStr, style: {color: chgColor, fontWeight: '600', fontSize: '10px'}}} : null
                    ].filter(Boolean)
                }
            };
        });

        return [children, pxPh, bpsPh];
    }""",
    Output(page.CDX_OPTIONS_DELTA_ONE_STRIP, 'children', allow_duplicate=True),
    Output(page.CDX_OPTIONS_REF_PX_ID, 'placeholder', allow_duplicate=True),
    Output(page.CDX_OPTIONS_REF_BPS_ID, 'placeholder', allow_duplicate=True),
    Input(page.CDX_OPTIONS_WS_SPOT_STORE, 'data'),
    State(page.CDX_OPTIONS_ACTIVE_PRODUCT_ID, 'value'),
    prevent_initial_call=True,
)


# ── Auto-boot: websocket init handshake ──────────────────────────────────

clientside_callback(
    """function(n, wsState, lifecycle, tickers, asof_date, lookback, mark_pos_values, mark_time_val, thresh_time_val,
               refresh_freq, session_product, session_strike, session_expiry, sessionData) {
        var nu = window.dash_clientside.no_update;
        if (lifecycle && lifecycle.phase === 'booting') return [nu, nu, nu];
        if (!n) return [nu, nu, nu];
        if (!wsState || wsState.readyState !== 1) return [nu, nu, nu];
        var sid = sessionData && sessionData.session_id ? sessionData.session_id : '';
        var nextSession = nu;
        if (!sid) {
            try {
                sid = window.crypto && window.crypto.randomUUID ? window.crypto.randomUUID() : '';
            } catch (e) {
                sid = '';
            }
            if (!sid)
                sid = 'sess-' + Date.now() + '-' + Math.random().toString(36).slice(2, 10);
            nextSession = {session_id: sid, created_ts: Date.now()};
        }
        var params = new URLSearchParams(window.location.search || '');
        if (params.get('product')) session_product = params.get('product').trim();
        if (params.get('strike')) {
            session_strike = params.get('strike').split(',').map(function(value) {
                var number = Number(value.trim());
                return isNaN(number) ? value.trim() : number;
            }).filter(function(value) { return value !== ''; });
        }
        if (params.get('expiry')) {
            session_expiry = params.get('expiry').split(',').map(function(value) {
                return value.trim();
            }).filter(Boolean);
        }
        return [
            JSON.stringify({
                command: 'init',
                session_id: sid,
                tickers: tickers,
                todt: asof_date,
                lookback: lookback || '6m',
                mark_pos: (mark_pos_values || []).indexOf('mark') >= 0,
                mark_time: mark_time_val || '',
                thresh_time: thresh_time_val || '4h',
                refresh: refresh_freq || 300,
                active_product: session_product,
                strike: session_strike,
                expiry: session_expiry,
                ts: Date.now()
            }),
            {phase: 'booting'},
            nextSession
        ];
    }""",
    Output(page.CDX_OPTIONS_WS, 'send', allow_duplicate=True),
    Output(page.CDX_OPTIONS_LIFECYCLE, 'data', allow_duplicate=True),
    Output(page.CDX_OPTIONS_SESSION_STORE, 'data'),
    Input(page.CDX_OPTIONS_AUTOBOOT_INTERVAL, 'n_intervals'),
    Input(page.CDX_OPTIONS_WS, 'state'),
    State(page.CDX_OPTIONS_LIFECYCLE, 'data'),
    State(page.CDX_OPTIONS_PRODUCT_ID, 'value'),
    State(page.CDX_OPTIONS_DATE_ID, 'date'),
    State(page.CDX_OPTIONS_LOOKBACK_ID, 'value'),
    State(page.CDX_OPTIONS_MARK_POS_ID, 'value'),
    State(page.CDX_OPTIONS_MARK_TIME_ID, 'value'),
    State(page.CDX_OPTIONS_THRESH_TIME_ID, 'value'),
    State(page.CDX_OPTIONS_REFRESH_FREQ, 'value'),
    State(page.CDX_OPTIONS_ACTIVE_PRODUCT_ID, 'value'),
    State(page.CDX_OPTIONS_STRIKE_ID, 'value'),
    State(page.CDX_OPTIONS_EXPIRY_ID, 'value'),
    State(page.CDX_OPTIONS_SESSION_STORE, 'data'),
    prevent_initial_call=True,
)

clientside_callback(
    """function(defs, lifecycle) {
        if (!defs || !defs.product_options || !defs.product_options.length) return window.dash_clientside.no_update;
        if (lifecycle && lifecycle.phase === 'ready') return window.dash_clientside.no_update;
        return {phase: 'ready'};
    }""",
    Output(page.CDX_OPTIONS_LIFECYCLE, 'data', allow_duplicate=True),
    Input(page.CDX_OPTIONS_WS_DEFAULTS_STORE, 'data'),
    State(page.CDX_OPTIONS_LIFECYCLE, 'data'),
    prevent_initial_call=True,
)


# ── Reload All: kick off heavy thread ────────────────────────────────────

@callback(
    Output(page.CDX_OPTIONS_PROGRESS_CONTAINER, 'style', allow_duplicate=True),
    Output(page.CDX_OPTIONS_PROGRESS_CONTAINER, 'children', allow_duplicate=True),
    Output(page.CDX_OPTIONS_SUBMIT_ID, 'disabled', allow_duplicate=True),
    Output(page.CDX_OPTIONS_REBUILD_GRIDS, 'disabled', allow_duplicate=True),
    Input(page.CDX_OPTIONS_SUBMIT_ID, 'n_clicks'),
    State(page.CDX_OPTIONS_PRODUCT_ID, 'value'),
    State(page.CDX_OPTIONS_DATE_ID, 'date'),
    State(page.CDX_OPTIONS_LOOKBACK_ID, 'value'),
    State(page.CDX_OPTIONS_MARK_POS_ID, 'value'),
    State(page.CDX_OPTIONS_MARK_TIME_ID, 'value'),
    State(page.CDX_OPTIONS_THRESH_TIME_ID, 'value'),
    prevent_initial_call=True
)
def on_submit(n_clicks, tickers, asof_date, lookback, mark_pos_values, mark_time_val, thresh_time_val):
    if n_clicks is None:
        raise PreventUpdate

    mark_pos = 'mark' in (mark_pos_values or [])
    try:
        client = get_client()
        status = client.get_status()
        is_refresh = (status and status.get('loader') is not None
                      and status.get('hist_loaded')
                      and status.get('tickers') == tickers
                      and status.get('todt') == str(pd.to_datetime(asof_date).date() if asof_date else None))

        if is_refresh:
            client.refresh(mark_pos=mark_pos, mark_time=mark_time_val or '', thresh_time=thresh_time_val or '4h')
        else:
            client.load(
                tickers=tickers, todt=asof_date,
                lookback=lookback or '6m',
                mark_pos=mark_pos, mark_time=mark_time_val or '',
                thresh_time=thresh_time_val or '4h',
            )
    except Exception as e:
        logging.exception('on_submit failed')
        return {'display': 'block', 'padding': '4px 0'}, html.Div(f'Error: {e}', style={'color': '#c0392b'}), False, False

    return {'display': 'block', 'padding': '4px 0'}, _build_tracker(0), True, True


# ── Config inputs: mark_time / thresh_time / mark_pos → WS config cmd ────

clientside_callback(
    """function(mt_val, tt_val, mp_vals, lifecycle, sessionData) {
        var nu = window.dash_clientside.no_update;
        if (!lifecycle || lifecycle.phase !== 'ready') return [nu, nu];
        if (mt_val === undefined || mt_val === null) return [nu, nu];
        var mark_pos = (mp_vals || []).indexOf('mark') >= 0;
        var cmd = JSON.stringify({
            command: 'config',
            session_id: sessionData && sessionData.session_id ? sessionData.session_id : null,
            mark_time: mt_val || '',
            thresh_time: tt_val || '4h',
            mark_pos: mark_pos
        });
        var lbl = mt_val ? 'mark@' + mt_val : 'mark=LIVE';
        return [cmd, '\u27f3 ' + lbl + ' \u2014 refreshing...'];
    }""",
    Output(page.CDX_OPTIONS_WS, 'send', allow_duplicate=True),
    Output(page.CDX_OPTIONS_LAST_UPDATE_TEXT, 'children', allow_duplicate=True),
    Input(page.CDX_OPTIONS_MARK_TIME_ID, 'value'),
    State(page.CDX_OPTIONS_THRESH_TIME_ID, 'value'),
    State(page.CDX_OPTIONS_MARK_POS_ID, 'value'),
    State(page.CDX_OPTIONS_LIFECYCLE, 'data'),
    State(page.CDX_OPTIONS_SESSION_STORE, 'data'),
    prevent_initial_call=True,
)

clientside_callback(
    """function(tt_val, mt_val, mp_vals, lifecycle, sessionData) {
        var nu = window.dash_clientside.no_update;
        if (!lifecycle || lifecycle.phase !== 'ready') return [nu, nu];
        if (!tt_val) return [nu, nu];
        var mark_pos = (mp_vals || []).indexOf('mark') >= 0;
        return [
            JSON.stringify({
                command: 'config',
                session_id: sessionData && sessionData.session_id ? sessionData.session_id : null,
                mark_time: mt_val || '',
                thresh_time: tt_val,
                mark_pos: mark_pos
            }),
            '\u27f3 Applying thresh=' + tt_val + '...'
        ];
    }""",
    Output(page.CDX_OPTIONS_WS, 'send', allow_duplicate=True),
    Output(page.CDX_OPTIONS_LAST_UPDATE_TEXT, 'children', allow_duplicate=True),
    Input(page.CDX_OPTIONS_THRESH_TIME_ID, 'value'),
    State(page.CDX_OPTIONS_MARK_TIME_ID, 'value'),
    State(page.CDX_OPTIONS_MARK_POS_ID, 'value'),
    State(page.CDX_OPTIONS_LIFECYCLE, 'data'),
    State(page.CDX_OPTIONS_SESSION_STORE, 'data'),
    prevent_initial_call=True,
)

clientside_callback(
    """function(mp_vals, mt_val, tt_val, lifecycle, sessionData) {
        var nu = window.dash_clientside.no_update;
        if (!lifecycle || lifecycle.phase !== 'ready') return [nu, nu];
        if (!mp_vals) return [nu, nu];
        var mark_pos = mp_vals.indexOf('mark') >= 0;
        var lbl = mark_pos ? 'Mark Pos ON' : 'Mark Pos OFF';
        return [
            JSON.stringify({
                command: 'config',
                session_id: sessionData && sessionData.session_id ? sessionData.session_id : null,
                mark_time: mt_val || '',
                thresh_time: tt_val || '4h',
                mark_pos: mark_pos
            }),
            '\u27f3 ' + lbl + ' \u2014 refreshing...'
        ];
    }""",
    Output(page.CDX_OPTIONS_WS, 'send', allow_duplicate=True),
    Output(page.CDX_OPTIONS_LAST_UPDATE_TEXT, 'children', allow_duplicate=True),
    Input(page.CDX_OPTIONS_MARK_POS_ID, 'value'),
    State(page.CDX_OPTIONS_MARK_TIME_ID, 'value'),
    State(page.CDX_OPTIONS_THRESH_TIME_ID, 'value'),
    State(page.CDX_OPTIONS_LIFECYCLE, 'data'),
    State(page.CDX_OPTIONS_SESSION_STORE, 'data'),
    prevent_initial_call=True,
)


# ── Progress event: updates progress bar + delta strip (clientside) ──────

clientside_callback(
    """function(prog, spotData) {
        var nu = window.dash_clientside.no_update;
        if (!prog || typeof prog !== 'object') return [nu, nu, nu, nu, nu];
        var stepKeys = """ + repr([step[0] for step in STEPS]) + """;
        var stepLabels = """ + repr(STEP_LABELS) + """;

        function cmp(type, props) {
            return {namespace: 'dash_html_components', type: type, props: props || {}};
        }

        function tracker(stepNum, error, done) {
            if (done) return cmp('Div', {});
            if (error) {
                return cmp('Div', {
                    children: '\u2716 Load failed: ' + error,
                    style: {
                        color: '#d32f2f',
                        fontSize: '11px',
                        padding: '4px 8px',
                        backgroundColor: '#fdecea',
                        borderRadius: '4px',
                    }
                });
            }
            stepNum = Math.max(0, Math.min(stepKeys.length - 1, Number(stepNum || 0)));
            var stepKey = stepKeys[stepNum] || 'init';
            var label = stepLabels[stepKey] || stepKey;
            var pct = Math.floor(stepNum / Math.max(stepKeys.length - 1, 1) * 100);
            return cmp('Div', {
                style: {display: 'flex', alignItems: 'center', padding: '4px 0'},
                children: [
                    cmp('Div', {
                        style: {
                            flex: '1',
                            backgroundColor: '#e0e0e0',
                            borderRadius: '2px',
                            marginRight: '10px',
                            height: '3px',
                        },
                        children: cmp('Div', {
                            style: {
                                width: pct + '%',
                                height: '3px',
                                backgroundColor: '#006D77',
                                borderRadius: '2px',
                                transition: 'width 0.4s ease',
                            }
                        })
                    }),
                    cmp('Span', {
                        children: label + '...',
                        style: {fontSize: '10px', color: '#666', whiteSpace: 'nowrap'}
                    })
                ]
            });
        }

        function deltaStrip(items) {
            if (!Array.isArray(items) || !items.length) {
                return cmp('Span', {
                    children: 'Awaiting delta-one data...',
                    style: {color: '#484f58', fontSize: '11px'}
                });
            }
            return items.map(function(d) {
                var spd = Number(d && d.spread);
                var px = Number(d && d.price);
                var chg = Number(d && d.chg);
                var hasSpd = isFinite(spd);
                var hasPx = isFinite(px);
                var hasChg = isFinite(chg);
                var otr = !!(d && d.is_otr);
                var chgColor = hasChg && chg < 0 ? '#3fb950' : hasChg && chg > 0 ? '#f85149' : '#484f58';
                var labelColor = otr ? '#f0f6fc' : '#8b949e';
                return cmp('Span', {
                    style: {
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: '6px',
                        padding: '3px 10px',
                        borderRadius: '4px',
                        marginRight: '2px',
                        backgroundColor: otr ? '#161b22' : '#0d1117',
                        border: '1px solid ' + (otr ? '#30363d' : '#21262d'),
                        fontSize: '11px',
                    },
                    children: [
                        cmp('Span', {
                            children: (d && d.product) || '',
                            style: {color: labelColor, fontWeight: otr ? '700' : '500'}
                        }),
                        cmp('Span', {
                            children: (hasPx ? px.toFixed(2) : '--') + ' px',
                            style: {color: '#d4a843'}
                        }),
                        cmp('Span', {
                            children: (hasSpd ? spd.toFixed(1) : '--') + ' spd',
                            style: {color: '#58a6ff'}
                        }),
                        hasChg ? cmp('Span', {
                            children: (chg >= 0 ? '+' : '') + chg.toFixed(1),
                            style: {color: chgColor, fontWeight: '600', fontSize: '10px'}
                        }) : cmp('Span', {})
                    ]
                });
            });
        }

        var strip = deltaStrip((spotData && spotData.items) || []);
        if (prog.error) {
            return [tracker(prog.num, prog.error, false), {display: 'block', padding: '8px 0'}, false, false, strip];
        }
        var done = !!prog.done;
        return [
            done ? cmp('Div', {}) : tracker(prog.num, null, false),
            done ? {display: 'block', padding: '8px 0'} : nu,
            done ? false : true,
            done ? false : true,
            strip
        ];
    }""",
    Output(page.CDX_OPTIONS_PROGRESS_CONTAINER, 'children'),
    Output(page.CDX_OPTIONS_PROGRESS_CONTAINER, 'style', allow_duplicate=True),
    Output(page.CDX_OPTIONS_SUBMIT_ID, 'disabled', allow_duplicate=True),
    Output(page.CDX_OPTIONS_REBUILD_GRIDS, 'disabled', allow_duplicate=True),
    Output(page.CDX_OPTIONS_DELTA_ONE_STRIP, 'children'),
    Input(page.CDX_OPTIONS_WS_PROGRESS_STORE, 'data'),
    Input(page.CDX_OPTIONS_WS_SPOT_STORE, 'data'),
    prevent_initial_call=True,
)


# ── Refresh frequency dropdown -> Flight server ─────────────────────────

clientside_callback(
    """function(seconds) {
        if (!seconds) return window.dash_clientside.no_update;
        return JSON.stringify({command: 'set_refresh_interval', seconds: seconds});
    }""",
    Output(page.CDX_OPTIONS_WS, 'send'),
    Input(page.CDX_OPTIONS_REFRESH_FREQ, 'value'),
    prevent_initial_call=True,
)




# ── Activity log + status bar (clientside) ───────────────────────────────

clientside_callback(
    """function(wsData, currentLog, committed) {
        var nu = window.dash_clientside.no_update;
        if (!wsData || typeof wsData !== 'object') return nu;
        var text = String(wsData.text || '').trim();
        if (!text) return nu;

        function asList(v) {
            if (v == null) return [];
            return Array.isArray(v) ? v : [v];
        }

        function nowText() {
            var d = new Date();
            function pad(v) { return String(v).padStart(2, '0'); }
            return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
        }

        var parts = [];
        var product = wsData.product || (committed && committed.product) || '';
        var expiries = asList(wsData.expiry).filter(function(x) { return x != null && x !== ''; }).map(function(x) { return String(x).slice(0, 10); });
        var strikes = asList(wsData.strike).filter(function(x) { return x != null && x !== ''; }).map(function(x) { return String(x); });
        if (!expiries.length && committed) {
            expiries = asList(committed.expiry).filter(function(x) { return x != null && x !== ''; }).map(function(x) { return String(x).slice(0, 10); });
        }
        if (!strikes.length && committed) {
            strikes = asList(committed.strike).filter(function(x) { return x != null && x !== ''; }).map(function(x) { return String(x); });
        }
        if (product) parts.push(String(product));
        if (expiries.length) parts.push('Exp=' + expiries.join(', '));
        if (strikes.length) parts.push('K=' + strikes.join(', '));
        var requestText = parts.join(' | ');

        var log = Array.isArray(currentLog) ? currentLog.slice() : [];
        var last = log.length ? log[log.length - 1] : null;
        if (Array.isArray(last) && last[1] === requestText && last[2] === text) return nu;
        log.push([nowText(), requestText, text]);
        return log.slice(-6);
    }""",
    Output(page.CDX_OPTIONS_EVENT_LOG_STORE, 'data', allow_duplicate=True),
    Input(page.CDX_OPTIONS_WS_COMPUTE_STATUS_STORE, 'data'),
    State(page.CDX_OPTIONS_EVENT_LOG_STORE, 'data'),
    State(page.CDX_OPTIONS_DD_COMMITTED, 'data'),
    prevent_initial_call=True,
)

clientside_callback(
    """function(wsData, eventLog, taskData, sessionData) {
        var nu = window.dash_clientside.no_update;
        if (!wsData && !eventLog && !taskData) return nu;

        function cmp(type, props) {
            return {namespace: 'dash_html_components', type: type, props: props || {}};
        }

        function body(children, extraStyle) {
            var style = {overflowY: 'auto', padding: '2px 12px 8px 12px', maxHeight: '170px'};
            if (extraStyle) Object.keys(extraStyle).forEach(function(k) { style[k] = extraStyle[k]; });
            return cmp('Div', {id: 'cdx-activity-body', style: style, children: children});
        }

        function divider() {
            return cmp('Div', {style: {borderTop: '1px solid #21262d', margin: '4px 0'}});
        }

        function section(title, color) {
            return cmp('Div', {
                children: title,
                style: {color: color, fontWeight: '700', marginBottom: '3px', letterSpacing: '0.5px'}
            });
        }

        function pad(v) {
            return String(v).padStart(2, '0');
        }

        function fmtTs(ms) {
            if (!ms) return '--:--:--';
            var d = new Date(ms);
            return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
        }

        function isActiveTask(task) {
            return !!task && (task.status === 'running' || task.status === 'queued');
        }

        function taskStatusColor(status) {
            return {
                queued: '#8b949e',
                running: '#58a6ff',
                done: '#3fb950',
                error: '#f85149',
                cancelled: '#6e7681'
            }[status] || '#c9d1d9';
        }

        function taskLine(task) {
            var ts = fmtTs(task.updated_ms || task.created_ms);
            var status = String(task.status || '').toUpperCase();
            var title = task.title || task.kind || 'Task';
            var selection = task.selection_label || '';
            var detail = task.detail || '';
            var metaParts = [];
            if (task.scope === 'global') metaParts.push('global');
            if (selection) metaParts.push(selection);
            if (detail) metaParts.push(detail);
            return cmp('Div', {
                style: {marginBottom: '2px', lineHeight: '1.45'},
                children: [
                    cmp('Span', {children: '[' + ts + '] ', style: {color: '#6e7681'}}),
                    cmp('Span', {children: status + ' ', style: {color: taskStatusColor(task.status), fontWeight: '700'}}),
                    cmp('Span', {children: title, style: {color: '#c9d1d9'}}),
                    metaParts.length ? cmp('Span', {children: '  |  ' + metaParts.join(' | '), style: {color: '#8b949e'}}) : null
                ].filter(Boolean)
            });
        }

        var tasks = taskData && Array.isArray(taskData.tasks) ? taskData.tasks : [];
        var sessionId = sessionData && sessionData.session_id ? String(sessionData.session_id) : '';
        var myTasks = tasks.filter(function(task) {
            return sessionId && String(task.owner_session_id || '') === sessionId;
        });
        var globalTasks = tasks.filter(function(task) {
            return task.scope === 'global' && String(task.owner_session_id || '') !== sessionId;
        });
        var myActive = myTasks.filter(isActiveTask);
        var globalActive = globalTasks.filter(isActiveTask);
        var totalRunning = tasks.filter(function(task) { return isActiveTask(task) && task.status === 'running' && (task.scope === 'global' || String(task.owner_session_id || '') === sessionId); }).length;
        var totalQueued = tasks.filter(function(task) { return isActiveTask(task) && task.status === 'queued' && (task.scope === 'global' || String(task.owner_session_id || '') === sessionId); }).length;
        var summaryParts = [];
        if (totalRunning) summaryParts.push(totalRunning + ' running');
        if (totalQueued) summaryParts.push(totalQueued + ' queued');
        if (globalActive.length) summaryParts.push(globalActive.length + ' global');

        var header = cmp('Div', {
            id: 'cdx-activity-toggle',
            style: {
                color: '#58a6ff',
                fontWeight: 'bold',
                fontSize: '10px',
                padding: '4px 12px 2px 12px',
                letterSpacing: '1px',
                borderBottom: '1px solid #21262d',
                cursor: 'pointer',
                userSelect: 'none',
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
            },
            children: [
                cmp('Span', {children: 'CDX OPTIONS TRADING > PROCESSING / ACTIVITY'}),
                cmp('Span', {
                    style: {display: 'inline-flex', alignItems: 'center', gap: '8px'},
                    children: [
                        summaryParts.length ? cmp('Span', {
                            children: summaryParts.join(' | '),
                            style: {color: '#d29922', fontWeight: '700'}
                        }) : null,
                        cmp('Span', {id: 'cdx-activity-toggle-icon', style: {fontSize: '12px'}, children: '▾'})
                    ].filter(Boolean)
                }),
            ]
        });

        var entries = wsData && Array.isArray(wsData.entries) ? wsData.entries : [];
        var colors = {
            start: '#58a6ff',
            done: '#3fb950',
            query: '#c9d1d9',
            compute: '#c9d1d9',
            data: '#3fb950',
            light: '#d29922',
            error: '#f85149',
            render: '#8b949e',
            info: '#8b949e',
        };
        var lines = [];

        if (myTasks.length) {
            lines.push(section('MY TASKS', '#d29922'));
            myTasks.slice(0, 4).forEach(function(task) {
                lines.push(taskLine(task));
            });
        }

        if (globalActive.length) {
            if (lines.length) lines.push(divider());
            lines.push(section('GLOBAL', '#58a6ff'));
            globalActive.slice(0, 4).forEach(function(task) {
                lines.push(taskLine(task));
            });
        }

        if (Array.isArray(eventLog) && eventLog.length) {
            if (lines.length) lines.push(divider());
            lines.push(section('TAB PROCESSING', '#d29922'));
            eventLog.slice(-3).reverse().forEach(function(entry) {
                var ts = Array.isArray(entry) && entry.length > 0 ? entry[0] : '--:--:--';
                var requestText = Array.isArray(entry) && entry.length > 2 ? entry[1] : '';
                var message = Array.isArray(entry) && entry.length > 2 ? entry[2] : (Array.isArray(entry) && entry.length > 1 ? entry[1] : '');
                lines.push(cmp('Div', {
                    style: {marginBottom: '1px', lineHeight: '1.4'},
                    children: [
                        cmp('Span', {children: '[' + ts + '] ', style: {color: '#6e7681'}}),
                        cmp('Span', {children: requestText ? requestText + '  |  ' : '', style: {color: '#58a6ff'}}),
                        cmp('Span', {children: message, style: {color: '#d29922'}}),
                    ]
                }));
            });
        }

        if (entries.length) {
            if (lines.length) lines.push(divider());
            lines.push(section('ACTIVITY', '#58a6ff'));
            entries.slice(-3).reverse().forEach(function(entry) {
                var ts = Array.isArray(entry) && entry.length > 0 ? entry[0] : '--:--:--';
                var category = Array.isArray(entry) && entry.length > 1 ? entry[1] : '';
                var message = Array.isArray(entry) && entry.length > 2 ? entry[2] : '';
                lines.push(cmp('Div', {
                    style: {marginBottom: '1px', lineHeight: '1.4'},
                    children: [
                        cmp('Span', {children: '[' + ts + '] ', style: {color: '#6e7681'}}),
                        cmp('Span', {children: message, style: {color: colors[category] || '#c9d1d9'}}),
                    ]
                }));
            });
        }

        if (!lines.length) {
            return [header, body('No activity yet.', {color: '#8b949e'})];
        }
        return [header, body(lines)];
    }""",
    Output(page.CDX_OPTIONS_STATUS_BAR, 'children'),
    Input(page.CDX_OPTIONS_WS_ACTIVITY_STORE, 'data'),
    Input(page.CDX_OPTIONS_EVENT_LOG_STORE, 'data'),
    Input(page.CDX_OPTIONS_WS_TASKS_STORE, 'data'),
    State(page.CDX_OPTIONS_SESSION_STORE, 'data'),
    prevent_initial_call=True,
)


# ── Trading info badge ───────────────────────────────────────────────────

clientside_callback(
    """function(info, progress, taskData, uiMarkTime, uiThreshTime, currentDate, sessionData) {
        var nu = window.dash_clientside.no_update;
        if (!info) return [nu, nu];
        var localT = info.now_local || '--:--';
        var markT = uiMarkTime || info.mark_time || '';
        var threshT = uiThreshTime || info.thresh_time || '4h';
        var phase = String(info.status_phase || info.status || 'live').toLowerCase();
        var progressBusy = !!(progress && progress.done === false);
        var tasks = taskData && Array.isArray(taskData.tasks) ? taskData.tasks : [];
        var sessionId = sessionData && sessionData.session_id ? String(sessionData.session_id) : '';
        function isVisibleTask(task) {
            return !!task && (task.scope === 'global' || (sessionId && String(task.owner_session_id || '') === sessionId));
        }
        function isActiveTask(task) {
            return isVisibleTask(task) && (task.status === 'running' || task.status === 'queued');
        }
        var activeTasks = tasks.filter(isActiveTask);
        var activeTask = activeTasks.length ? activeTasks[0] : null;
        if (progressBusy && phase === 'live') {
            phase = (markT && markT.indexOf(':') >= 0) ? 'remarking' : 'refreshing';
        } else if (phase === 'live' && activeTask) {
            var activeKind = String(activeTask.kind || '');
            phase = activeKind === 'rebuild' ? 'rebuilding' : (activeKind === 'remark' ? 'remarking' : 'updating');
        }
        var labelText = String(phase || 'live').toUpperCase();
        var phaseColor = {
            live: '#2e7d32',
            loading: '#1565c0',
            refreshing: '#d29922',
            remarking: '#d29922',
            rebuilding: '#8e24aa',
            updating: '#d29922',
            error: '#d32f2f'
        }[phase] || '#2e7d32';
        var grids = info.grids_loaded || 0;
        var timeStr = localT;
        if (markT && markT.indexOf(':') >= 0 && markT !== localT) {
            timeStr = localT + ' (mark @' + markT + ' / ' + threshT + ')';
        } else if (threshT && threshT !== '4h') {
            timeStr = localT + ' (' + threshT + ')';
        }
        var statusText = '';
        if (info.progress_error) {
            labelText = 'ERROR';
            phaseColor = '#d32f2f';
            statusText = String(info.progress_error);
        } else if (info.status_text) {
            statusText = String(info.status_text);
        } else if (progressBusy && progress.label) {
            statusText = String(progress.label);
        } else if (activeTask) {
            statusText = String(activeTask.detail || activeTask.title || '');
        }
        var badge = [
            {namespace: 'dash_html_components', type: 'Span',
             props: {children: labelText, style: {color: phaseColor, fontWeight: '700', marginRight: '8px'}}},
            {namespace: 'dash_html_components', type: 'Span',
             props: {children: timeStr, style: {marginRight: '8px'}}}
        ];
        if (statusText) {
            badge.push({namespace: 'dash_html_components', type: 'Span',
                props: {children: statusText, style: {marginRight: '8px', color: '#6e7681'}}});
        }
        if (activeTasks.length) {
            var runningCount = activeTasks.filter(function(task) { return task.status === 'running'; }).length;
            var queuedCount = activeTasks.filter(function(task) { return task.status === 'queued'; }).length;
            var globalCount = activeTasks.filter(function(task) {
                return task.scope === 'global' && String(task.owner_session_id || '') !== sessionId;
            }).length;
            var taskParts = [];
            if (runningCount) taskParts.push(runningCount + ' running');
            if (queuedCount) taskParts.push(queuedCount + ' queued');
            if (globalCount) taskParts.push(globalCount + ' global');
            if (taskParts.length) {
                badge.push({namespace: 'dash_html_components', type: 'Span',
                    props: {children: taskParts.join(' | '), style: {marginRight: '8px', color: '#d29922', fontWeight: '600'}}});
            }
        }
        if (grids) {
            badge.push({namespace: 'dash_html_components', type: 'Span',
                props: {children: grids + ' grids', style: {marginRight: '8px'}}});
        }
        var dateOut = nu;
        if (info.todt && currentDate && String(currentDate).slice(0, 10) !== String(info.todt).slice(0, 10)) {
            dateOut = info.todt;
        }
        return [badge, dateOut];
    }""",
    Output(page.CDX_OPTIONS_TRADING_INFO_ID, 'children'),
    Output(page.CDX_OPTIONS_DATE_ID, 'date'),
    Input(page.CDX_OPTIONS_WS_STATUS_INFO_STORE, 'data'),
    Input(page.CDX_OPTIONS_WS_PROGRESS_STORE, 'data'),
    Input(page.CDX_OPTIONS_WS_TASKS_STORE, 'data'),
    Input(page.CDX_OPTIONS_MARK_TIME_ID, 'value'),
    Input(page.CDX_OPTIONS_THRESH_TIME_ID, 'value'),
    State(page.CDX_OPTIONS_DATE_ID, 'date'),
    State(page.CDX_OPTIONS_SESSION_STORE, 'data'),
    prevent_initial_call=True,
)


# ── Tab visibility toggle (clientside CSS) ───────────────────────────────

clientside_callback(
    """function(tabValue, currentStore) {
        currentStore = currentStore || {value: 'vol-grids', ts: 0};
        var nu = window.dash_clientside.no_update;
        if (tabValue === currentStore.value) return nu;
        return {value: tabValue, ts: Date.now()};
    }""",
    Output(page.CDX_OPTIONS_TAB_STORE, 'data'),
    Input(page.CDX_OPTIONS_TABS_ID, 'value'),
    State(page.CDX_OPTIONS_TAB_STORE, 'data'),
    prevent_initial_call=True,
)

clientside_callback(
    """function(tabValue) {
        var tab = tabValue || 'vol-grids';
        var panels = {
            'vol-grids': 'cdx-tab-panel-vol-grids',
            'trade-blotter': 'cdx-tab-panel-trade-blotter',
            'positions': 'cdx-tab-panel-positions',
            'expiry-charts': 'cdx-tab-panel-expiry-charts',
            'option-stack': 'cdx-tab-panel-option-stack',
            'relative-vol': 'cdx-tab-panel-relative-vol'
        };
        var styles = [];
        Object.keys(panels).forEach(function(t) {
            styles.push({display: t === tab ? 'block' : 'none'});
        });
        return styles;
    }""",
    [Output(page.TAB_PANEL_VOL_GRIDS, 'style'),
     Output(page.TAB_PANEL_TRADE_BLOTTER, 'style'),
     Output(page.TAB_PANEL_POSITIONS, 'style'),
     Output(page.TAB_PANEL_EXPIRY_CHARTS, 'style'),
     Output(page.TAB_PANEL_OPTION_STACK, 'style'),
     Output(page.TAB_PANEL_RELATIVE_VOL, 'style')],
    Input(page.CDX_OPTIONS_TABS_ID, 'value'),
    prevent_initial_call=True,
)



# ── Dropdown change: send WS subscribe command, no Flight RPCs ───────────

clientside_callback(
    """function(product, strike, expiry, current, lifecycle, currentTab) {
        var nu = window.dash_clientside.no_update;
        if (!lifecycle || lifecycle.phase !== 'ready') return [nu, true];
        if (!product) return [nu, true];
        if (current && current.product && current.product !== product) return [nu, true];
        function asList(v) {
            return Array.isArray(v) ? v : (v == null || v === '' ? [] : [v]);
        }
        function needsFullSelection(tab) {
            return tab === 'expiry-charts' || tab === 'option-stack';
        }
        var nextStrike = asList(strike);
        var nextExpiry = asList(expiry);
        var activeTab = currentTab || 'vol-grids';
        if (needsFullSelection(activeTab) && (!nextStrike.length || !nextExpiry.length)) return [nu, true];
        var prevStrike = asList(current && current.strike);
        var prevExpiry = asList(current && current.expiry);
        if (current && current.product === product
                && ((prevStrike.length && !nextStrike.length) || (prevExpiry.length && !nextExpiry.length))) {
            return [nu, true];
        }
        var pending = {product: product, strike: strike, expiry: expiry, ts: Date.now()};
        if (current && current.product === product
            && JSON.stringify(current.strike) === JSON.stringify(strike)
            && JSON.stringify(current.expiry) === JSON.stringify(expiry)) {
            return [nu, true];
        }
        return [pending, false];
    }""",
    Output(page.CDX_OPTIONS_DD_PENDING, 'data'),
    Output(page.CDX_OPTIONS_DD_DEBOUNCE, 'disabled'),
    Input(page.CDX_OPTIONS_ACTIVE_PRODUCT_ID, 'value'),
    Input(page.CDX_OPTIONS_STRIKE_ID, 'value'),
    Input(page.CDX_OPTIONS_EXPIRY_ID, 'value'),
    State(page.CDX_OPTIONS_DD_COMMITTED, 'data'),
    State(page.CDX_OPTIONS_LIFECYCLE, 'data'),
    State(page.CDX_OPTIONS_TABS_ID, 'value'),
    prevent_initial_call=True,
)

clientside_callback(
    """function(n, pending) {
        if (!pending || !pending.product) return [window.dash_clientside.no_update, true];
        return [pending, true];
    }""",
    Output(page.CDX_OPTIONS_DD_COMMITTED, 'data'),
    Output(page.CDX_OPTIONS_DD_DEBOUNCE, 'disabled', allow_duplicate=True),
    Input(page.CDX_OPTIONS_DD_DEBOUNCE, 'n_intervals'),
    State(page.CDX_OPTIONS_DD_PENDING, 'data'),
    prevent_initial_call=True,
)

clientside_callback(
    """function(committed, currentTab, sessionData, override) {
        var nu = window.dash_clientside.no_update;
        if (!committed || !committed.product) return [nu, nu, nu, nu];
        if (committed.awaiting_defaults) return [nu, nu, nu, nu];
        function asList(v) {
            return Array.isArray(v) ? v : (v == null || v === '' ? [] : [v]);
        }
        function needsFullSelection(tab) {
            return tab === 'expiry-charts' || tab === 'option-stack';
        }
        function activeTabKey(tab) {
            return {
                'vol-grids': 'vol_grids',
                'trade-blotter': 'trade_blotter',
                'positions': 'positions',
                'expiry-charts': 'expiry_charts',
                'option-stack': 'option_stack'
            }[tab || 'vol-grids'];
        }
        function makeRequestId(prefix) {
            var id = (window.crypto && window.crypto.randomUUID)
                ? window.crypto.randomUUID()
                : String(Date.now()) + '-' + Math.random().toString(36).slice(2);
            return prefix + '-' + id;
        }
        var activeTab = currentTab || 'vol-grids';
        if (needsFullSelection(activeTab) && (!asList(committed.strike).length || !asList(committed.expiry).length))
            return [nu, nu, nu, nu];
        var ov = override || {};
        var hasOverride = (ov.ref_px != null) || (ov.ref_bps != null)
            || (ov.vol_shift != null && ov.vol_shift !== 0)
            || (ov.vol_lookback && ov.vol_lookback !== '5b');
        var requestId = makeRequestId('dd');
        var requestedTab = activeTabKey(activeTab);
        if (!requestedTab) return [nu, nu, nu, nu];
        var msg = {
            command: hasOverride ? 'override' : 'subscribe',
            session_id: sessionData && sessionData.session_id ? sessionData.session_id : null,
            product: committed.product,
            strike: committed.strike,
            expiry: committed.expiry,
            active_tab: activeTab,
            requested_tabs: requestedTab ? [requestedTab] : null,
            request_id: requestId,
            ts: Date.now()
        };
        if (hasOverride) {
            msg.ref_px = (ov.ref_px != null) ? ov.ref_px : null;
            msg.ref_bps = (ov.ref_bps != null) ? ov.ref_bps : null;
            msg.vol_shift_bps = (ov.vol_shift != null) ? ov.vol_shift : null;
            msg.vol_lookback = ov.vol_lookback || '5b';
        }
        return [
            JSON.stringify(msg),
            nu,
            {pending: true, stage: 'rendering', detail: 'Queued active tab refresh',
             product: committed.product, strike: committed.strike, expiry: committed.expiry,
             request_id: requestId, ts: Date.now()},
            nu
        ];
    }""",
    Output(page.CDX_OPTIONS_WS, 'send', allow_duplicate=True),
    Output(page.CDX_OPTIONS_LAST_UPDATE_TEXT, 'children', allow_duplicate=True),
    Output(page.CDX_OPTIONS_LOADING_STORE, 'data', allow_duplicate=True),
    Output(page.CDX_OPTIONS_WS_TAB_DATA_STORE, 'data', allow_duplicate=True),
    Input(page.CDX_OPTIONS_DD_COMMITTED, 'data'),
    State(page.CDX_OPTIONS_TABS_ID, 'value'),
    State(page.CDX_OPTIONS_SESSION_STORE, 'data'),
    State(page.CDX_OPTIONS_OVERRIDE_STORE, 'data'),
    prevent_initial_call=True,
)


clientside_callback(
    """function(activeProduct, strike, expiry, committed, memory, lifecycle, sessionData, tabCache, currentTab) {
        var nu = window.dash_clientside.no_update;
        var empty = [nu, nu, nu, nu, nu, nu, nu, nu, nu, nu, nu, nu];
        if (!lifecycle || lifecycle.phase !== 'ready') return empty;
        if (!activeProduct || !committed || !committed.product || committed.product === activeProduct)
            return empty;
        function asList(v) {
            return Array.isArray(v) ? v : (v == null || v === '' ? [] : [v]);
        }
        function activeTabKey(tab) {
            return {
                'vol-grids': 'vol_grids',
                'trade-blotter': 'trade_blotter',
                'positions': 'positions',
                'expiry-charts': 'expiry_charts',
                'option-stack': 'option_stack'
            }[tab || 'vol-grids'];
        }
        function clearPayload(requestId) {
            return {
                product: activeProduct,
                strike: [],
                expiry: [],
                request_id: requestId,
                _source: 'clear',
                _selection_sig: JSON.stringify({product: String(activeProduct), strike: [], expiry: []}),
                _calc_sig: requestId,
                calc_context: {calc_sig: requestId},
                _tab_sigs: {},
                _changed_tabs: ['vol_grids','trade_blotter','positions','expiry_charts','option_stack'],
                vol_grids: {_cleared: true},
                trade_blotter: {_cleared: true},
                positions: {_cleared: true},
                expiry_charts: {_cleared: true},
                option_stack: {_cleared: true}
            };
        }
        function makeRequestId(prefix) {
            var id = (window.crypto && window.crypto.randomUUID)
                ? window.crypto.randomUUID()
                : String(Date.now()) + '-' + Math.random().toString(36).slice(2);
            return prefix + '-' + id;
        }
        var requestId = makeRequestId('prod');
        var activeTab = currentTab || 'vol-grids';
        var requestedTab = activeTabKey(activeTab);
        var provisional = {
            product: activeProduct,
            strike: [],
            expiry: [],
            awaiting_defaults: true,
            request_id: requestId,
            ts: Date.now()
        };
        var nextMemory = Object.assign({}, memory || {});
        nextMemory[committed.product] = {strike: asList(strike), expiry: asList(expiry)};
        var cachedEntry = tabCache && tabCache[activeProduct];
        var restored = cachedEntry && cachedEntry.payload
            ? Object.assign({}, cachedEntry.payload, {
                _display_state: 'cached',
                _cached_at: cachedEntry.cached_at || Date.now(),
                _changed_tabs: ['vol_grids','trade_blotter','positions','expiry_charts','option_stack'].filter(
                    function(k) { return cachedEntry.payload[k] != null; }
                )
            })
            : clearPayload(requestId);
        return [
            nextMemory,
            [],
            [],
            null,
            null,
            '',
            '',
            {},
            JSON.stringify({
                command: 'subscribe',
                session_id: sessionData && sessionData.session_id ? sessionData.session_id : null,
                product: activeProduct,
                active_tab: activeTab,
                requested_tabs: requestedTab ? [requestedTab] : null,
                request_id: requestId,
                ts: Date.now()
            }),
            {pending: true, stage: 'defaults', detail: 'Loading product selection',
             product: activeProduct, strike: [], expiry: [], request_id: requestId, ts: Date.now()},
            restored,
            provisional
        ];
    }""",
    Output(page.CDX_OPTIONS_PRODUCT_MEMORY, 'data', allow_duplicate=True),
    Output(page.CDX_OPTIONS_STRIKE_ID, 'value', allow_duplicate=True),
    Output(page.CDX_OPTIONS_EXPIRY_ID, 'value', allow_duplicate=True),
    Output(page.CDX_OPTIONS_REF_PX_ID, 'value', allow_duplicate=True),
    Output(page.CDX_OPTIONS_REF_BPS_ID, 'value', allow_duplicate=True),
    Output(page.CDX_OPTIONS_PX_CONV, 'children', allow_duplicate=True),
    Output(page.CDX_OPTIONS_BPS_CONV, 'children', allow_duplicate=True),
    Output(page.CDX_OPTIONS_OVERRIDE_STORE, 'data', allow_duplicate=True),
    Output(page.CDX_OPTIONS_WS, 'send', allow_duplicate=True),
    Output(page.CDX_OPTIONS_LOADING_STORE, 'data', allow_duplicate=True),
    Output(page.CDX_OPTIONS_WS_TAB_DATA_STORE, 'data', allow_duplicate=True),
    Output(page.CDX_OPTIONS_DD_COMMITTED, 'data', allow_duplicate=True),
    Input(page.CDX_OPTIONS_ACTIVE_PRODUCT_ID, 'value'),
    State(page.CDX_OPTIONS_STRIKE_ID, 'value'),
    State(page.CDX_OPTIONS_EXPIRY_ID, 'value'),
    State(page.CDX_OPTIONS_DD_COMMITTED, 'data'),
    State(page.CDX_OPTIONS_PRODUCT_MEMORY, 'data'),
    State(page.CDX_OPTIONS_LIFECYCLE, 'data'),
    State(page.CDX_OPTIONS_SESSION_STORE, 'data'),
    State(page.CDX_OPTIONS_TAB_CACHE, 'data'),
    State(page.CDX_OPTIONS_TABS_ID, 'value'),
    prevent_initial_call=True,
)


# ── Override inputs: send WS override command ────────────────────────────

def _normalize_spot_override(product, ref_px, ref_bps, triggered_id=None):
    if ref_px is None or ref_bps is None:
        return ref_px, ref_bps
    if triggered_id == page.CDX_OPTIONS_REF_PX_ID:
        return ref_px, None
    if triggered_id == page.CDX_OPTIONS_REF_BPS_ID:
        return None, ref_bps
    try:
        from sdr.dtccpy.credit_utils import _is_px_index
        return (ref_px, None) if _is_px_index(product) else (None, ref_bps)
    except Exception:
        return ref_px, None


def _clear_tab_payload(product, strike, expiry, request_id):
    strike_vals = strike if isinstance(strike, list) else ([] if strike in (None, '') else [strike])
    expiry_vals = expiry if isinstance(expiry, list) else ([] if expiry in (None, '') else [expiry])
    sig = json.dumps({
        'product': '' if product is None else str(product),
        'strike': [str(float(v)) if isinstance(v, (int, float)) else str(v) for v in strike_vals],
        'expiry': [str(v) for v in expiry_vals],
    }, sort_keys=True)
    return {
        'product': product,
        'strike': strike,
        'expiry': expiry,
        'request_id': request_id,
        '_source': 'clear',
        '_selection_sig': sig,
        '_calc_sig': request_id,
        'calc_context': {'calc_sig': request_id},
        '_tab_sigs': {},
        'vol_grids': {'_cleared': True},
        'trade_blotter': {'_cleared': True},
        'positions': {'_cleared': True},
        'expiry_charts': {'_cleared': True},
        'option_stack': {'_cleared': True},
    }


@callback(
    Output(page.CDX_OPTIONS_OVERRIDE_STORE, 'data'),
    Output(page.CDX_OPTIONS_PX_CONV, 'children'),
    Output(page.CDX_OPTIONS_BPS_CONV, 'children'),
    Output(page.CDX_OPTIONS_REF_PX_ID, 'value', allow_duplicate=True),
    Output(page.CDX_OPTIONS_REF_BPS_ID, 'value', allow_duplicate=True),
    Output(page.CDX_OPTIONS_WS, 'send', allow_duplicate=True),
    Output(page.CDX_OPTIONS_LAST_UPDATE_TEXT, 'children', allow_duplicate=True),
    Output(page.CDX_OPTIONS_LOADING_STORE, 'data', allow_duplicate=True),
    Output(page.CDX_OPTIONS_WS_TAB_DATA_STORE, 'data', allow_duplicate=True),
    Input(page.CDX_OPTIONS_REF_PX_ID, 'value'),
    Input(page.CDX_OPTIONS_REF_BPS_ID, 'value'),
    Input(page.CDX_OPTIONS_VOL_SHIFT_ID, 'value'),
    Input(page.CDX_OPTIONS_VOL_LOOKBACK_ID, 'value'),
    State(page.CDX_OPTIONS_ACTIVE_PRODUCT_ID, 'value'),
    State(page.CDX_OPTIONS_STRIKE_ID, 'value'),
    State(page.CDX_OPTIONS_EXPIRY_ID, 'value'),
    State(page.CDX_OPTIONS_TABS_ID, 'value'),
    State(page.CDX_OPTIONS_OVERRIDE_STORE, 'data'),
    State(page.CDX_OPTIONS_LIFECYCLE, 'data'),
    State(page.CDX_OPTIONS_SESSION_STORE, 'data'),
    prevent_initial_call=True
)
def on_spot_vol_overrides(ref_px, ref_bps, vol_shift, vol_lookback, product, strike, expiry, active_tab, current_store, lifecycle, session_data):
    if not lifecycle or lifecycle.get('phase') != 'ready':
        raise PreventUpdate
    if not product:
        raise PreventUpdate
    ref_px = float(ref_px) if ref_px is not None and ref_px != '' else None
    ref_bps = float(ref_bps) if ref_bps is not None and ref_bps != '' else None
    vol_shift = float(vol_shift) if vol_shift is not None and vol_shift != '' else None
    vol_lookback = str(vol_lookback).strip() if vol_lookback is not None and str(vol_lookback).strip() else '5b'
    ref_px, ref_bps = _normalize_spot_override(product, ref_px, ref_bps, ctx.triggered_id)

    current_store = current_store or {}
    if (ref_px == current_store.get('ref_px')
            and ref_bps == current_store.get('ref_bps')
            and vol_shift == current_store.get('vol_shift')
            and vol_lookback == current_store.get('vol_lookback', '5b')):
        raise PreventUpdate

    all_cleared = ref_px is None and ref_bps is None and vol_shift is None and vol_lookback == '5b'
    was_cleared = (current_store.get('ref_px') is None and current_store.get('ref_bps') is None
                   and current_store.get('vol_shift') is None
                   and current_store.get('vol_lookback', '5b') == '5b')
    if all_cleared and was_cleared:
        raise PreventUpdate

    request_id = f'ovr-{uuid.uuid4()}'
    store = {
        'ref_px': ref_px,
        'ref_bps': ref_bps,
        'vol_shift': vol_shift,
        'vol_lookback': vol_lookback,
        'request_id': request_id,
    }

    px_conv = ''
    bps_conv = ''
    if ref_px is not None or ref_bps is not None:
        try:
            show_px, show_bps = get_client().convert_spot(product, ref_px=ref_px, ref_bps=ref_bps)
            if ref_px is not None and show_bps is not None:
                px_conv = f'= {show_bps:.2f} bps'
            if ref_bps is not None and show_px is not None:
                bps_conv = f'= {show_px:.2f} px'
        except Exception:
            logging.exception('convert_spot failed in on_spot_vol_overrides')

    import json
    requested_tab = {
        'vol-grids': 'vol_grids',
        'trade-blotter': 'trade_blotter',
        'positions': 'positions',
        'expiry-charts': 'expiry_charts',
        'option-stack': 'option_stack',
    }.get(active_tab or 'vol-grids')
    ws_cmd = json.dumps({
        'command': 'override',
        'session_id': (session_data or {}).get('session_id'),
        'product': product, 'strike': strike, 'expiry': expiry,
        'active_tab': active_tab,
        'requested_tabs': [requested_tab] if requested_tab else None,
        'request_id': request_id,
        'ref_px': ref_px, 'ref_bps': ref_bps,
        'vol_shift_bps': vol_shift, 'vol_lookback': vol_lookback,
        'ts': time.time(),
    })

    calc_parts = []
    if ref_px is not None: calc_parts.append(f'Px={ref_px:.2f}')
    if ref_bps is not None: calc_parts.append(f'Bps={ref_bps:.2f}')
    if vol_shift is not None: calc_parts.append(f'VolShift={vol_shift:+.0f}')
    if vol_lookback != '5b': calc_parts.append(f'Lb={vol_lookback}')
    status = '\u27f3 Recalculating' + (' (' + ', '.join(calc_parts) + ')' if calc_parts else '') + '...'

    loading = {
        'pending': True,
        'product': product,
        'strike': strike,
        'expiry': expiry,
        'request_id': request_id,
        'ts': time.time() * 1000,
    }
    return store, px_conv, bps_conv, ref_px, ref_bps, ws_cmd, status, loading, no_update


# ── Rebuild Grids → WS command ────────────────────────────────────────────

clientside_callback(
    """function(n, sessionData) {
        if (!n) return [window.dash_clientside.no_update, window.dash_clientside.no_update];
        return [JSON.stringify({
            command: 'rebuild_grids',
            session_id: sessionData && sessionData.session_id ? sessionData.session_id : null
        }), '\u27f3 Rebuilding grids...'];
    }""",
    Output(page.CDX_OPTIONS_WS, 'send', allow_duplicate=True),
    Output(page.CDX_OPTIONS_LAST_UPDATE_TEXT, 'children', allow_duplicate=True),
    Input(page.CDX_OPTIONS_REBUILD_GRIDS, 'n_clicks'),
    State(page.CDX_OPTIONS_SESSION_STORE, 'data'),
    prevent_initial_call=True,
)


# ── Relative Vol compare (user-triggered, keeps Flight RPC) ──────────────

def rv_strike_spaces(product, current='delta'):
    from dash_reports.cdx_options.data.pricing_grid import is_price_based
    options = [{'label': 'Delta', 'value': 'delta'}]
    if product:
        options.append({'label': 'Price strike' if is_price_based(product) else 'Spread strike (bps)',
                        'value': 'price_strike' if is_price_based(product) else 'spread_strike'})
    allowed = {option['value'] for option in options}
    return options, current if current in allowed else 'delta'


for _rv_leg in (1, 2):
    callback(
        Output(f'cdx-rv-opt{_rv_leg}-space', 'options'),
        Output(f'cdx-rv-opt{_rv_leg}-space', 'value'),
        Input(f'cdx-rv-opt{_rv_leg}-product', 'value'),
        State(f'cdx-rv-opt{_rv_leg}-space', 'value'),
    )(rv_strike_spaces)
    clientside_callback(
        """function(space, value) {
            var nu = window.dash_clientside.no_update;
            var delta = !space || space === 'delta';
            var valid = value != null && isFinite(Number(value)) && Number(value) > 0
                && (delta ? Number(value) < 1 : Number(value) > 1);
            return [delta ? 'Delta (0–1)' : (space === 'price_strike' ? 'Price strike' : 'Spread strike (bps)'),
                    delta ? '0.25' : (space === 'price_strike' ? '107' : '300'),
                    valid ? nu : null];
        }""",
        Output(f'cdx-rv-opt{_rv_leg}-input-label', 'children'),
        Output(f'cdx-rv-opt{_rv_leg}-delta', 'placeholder'),
        Output(f'cdx-rv-opt{_rv_leg}-delta', 'value', allow_duplicate=True),
        Input(f'cdx-rv-opt{_rv_leg}-space', 'value'),
        State(f'cdx-rv-opt{_rv_leg}-delta', 'value'),
        prevent_initial_call=True,
    )

@callback(
    Output('cdx-rv-chart', 'figure'),
    Output('cdx-rv-feedback', 'children'),
    Output('cdx-rv-feedback', 'style'),
    Input(page.CDX_RV_SUBMIT_ID, 'n_clicks'),
    State(page.CDX_RV_OPT1_PRODUCT_ID, 'value'),
    State(page.CDX_RV_OPT1_TENOR_ID, 'value'),
    State(page.CDX_RV_OPT1_DELTA_ID, 'value'),
    State(page.CDX_RV_OPT2_PRODUCT_ID, 'value'),
    State(page.CDX_RV_OPT2_TENOR_ID, 'value'),
    State(page.CDX_RV_OPT2_DELTA_ID, 'value'),
    State(page.CDX_RV_OPT1_SPACE_ID, 'value'),
    State(page.CDX_RV_OPT2_SPACE_ID, 'value'),
    prevent_initial_call=True,
    running=[(Output(page.CDX_RV_SUBMIT_ID, 'disabled'), True, False),
             (Output(page.CDX_RV_SUBMIT_ID, 'children'), 'Calculating volatility…', 'Compare volatility')],
)
def on_rv_compare(n_clicks, opt1_product, opt1_tenor, opt1_delta,
                  opt2_product, opt2_tenor, opt2_delta, opt1_space='delta', opt2_space='delta'):
    if n_clicks is None:
        raise PreventUpdate

    opt1 = {'product': opt1_product, 'tenor': opt1_tenor, 'strike': opt1_delta, 'strike_type': opt1_space or 'delta'}
    opt2 = {'product': opt2_product, 'tenor': opt2_tenor, 'strike': opt2_delta, 'strike_type': opt2_space or 'delta'} if opt2_product else None

    def error(message):
        from dash_reports.cdx_options.data.charts import _finish_chart
        import plotly.graph_objects as go
        logging.warning('Relative Vol rejected: %s; opt1=%s opt2=%s', message, opt1, opt2)
        fig = _finish_chart(go.Figure(), 'Relative volatility')
        fig.update_xaxes(visible=False)
        fig.update_yaxes(visible=False)
        fig.add_annotation(text='Check the inputs above, then select Compare volatility.',
                           x=.5, y=.5, xref='paper', yref='paper', showarrow=False)
        return fig, message, {'fontSize': '13px', 'padding': '10px', 'marginTop': '10px',
                             'color': '#9b2525', 'backgroundColor': '#fff2f0', 'borderRadius': '6px'}

    try:
        opt1 = parse_rv_option(opt1)
        opt2 = parse_rv_option(opt2, 'Leg 2') if opt2 else None
    except ValueError as exc:
        return error(str(exc))

    started = time.monotonic()
    logging.info('Relative Vol requested: opt1=%s opt2=%s', opt1, opt2)
    try:
        data = get_client().render_rv_chart(opt1, opt2)
    except (FlightError, RuntimeError):
        logging.exception('Relative Vol calculation failed: opt1=%s opt2=%s', opt1, opt2)
        return error('The volatility calculation failed. Please retry; the server log contains the details.')
    fig = data.get('figure')
    if not fig:
        return error(data.get('error', 'No volatility data for this selection.'))
    if not fig.get('data'):
        return error('No volatility history for the selected products, tenors and strikes/deltas.')
    elapsed = time.monotonic() - started
    return fig, f'Chart updated · {elapsed:.1f}s', {
        'fontSize': '12px', 'paddingTop': '10px', 'color': '#327b5a'}


# ── Strike/Expiry container visibility ───────────────────────────────────

TABS_WITH_STRIKE_EXPIRY = {'expiry-charts', 'option-stack'}

@callback(
    Output(page.CDX_OPTIONS_STRIKE_EXPIRY_CONTAINER, 'style'),
    Input(page.CDX_OPTIONS_TABS_ID, 'value'),
)
def toggle_strike_expiry(tab):
    visible = tab in TABS_WITH_STRIKE_EXPIRY
    return {'display': 'flex'} if visible else {'display': 'none'}


# ── Blotter summary callbacks ────────────────────────────────────────────

_BLOTTER_KEYS = BLOTTER_KEYS

clientside_callback(
    """function() {
        var dataList = Array.prototype.slice.call(arguments);
        var aggCols = ['estimated_notional', 'net_vega_ann', 'live_vega', 'est_MVE', 'live_MVE', 'intraday_tcost', 'live_pnl'];

        function cmp(type, props) {
            return {namespace: 'dash_html_components', type: type, props: props || {}};
        }

        function uiName(col) {
            return String(col || '')
                .replace(/_/g, ' ')
                .toLowerCase()
                .replace(/(^|\\s)\\S/g, function(m) { return m.toUpperCase(); });
        }

        function formatValue(col, value) {
            if (col === 'product') return String(value || '');
            var num = Number(value);
            if (!isFinite(num)) return '';
            return num.toLocaleString(undefined, {minimumFractionDigits: 0, maximumFractionDigits: 0});
        }

        function buildSummary(data) {
            if (!Array.isArray(data) || !data.length) return null;
            if (!data.some(function(row) { return row && row.product != null; })) return null;

            var cols = ['product'];
            aggCols.forEach(function(col) {
                if (data.some(function(row) { return row && Object.prototype.hasOwnProperty.call(row, col); })) {
                    cols.push(col);
                }
            });
            cols.push('trades');

            var grouped = {};
            data.forEach(function(row) {
                if (!row || row.product == null || row.product === '') return;
                var product = String(row.product);
                if (!grouped[product]) {
                    grouped[product] = {product: product, trades: 0};
                    cols.forEach(function(col) {
                        if (col !== 'product' && col !== 'trades') grouped[product][col] = 0;
                    });
                }
                grouped[product].trades += 1;
                cols.forEach(function(col) {
                    if (col === 'product' || col === 'trades') return;
                    var num = Number(row[col]);
                    if (isFinite(num)) grouped[product][col] += num;
                });
            });

            var products = Object.keys(grouped).sort();
            if (!products.length) return null;

            return cmp('Div', {
                style: {overflowX: 'auto', border: '1px solid #ccc', borderRadius: '4px'},
                children: cmp('Table', {
                    style: {width: '100%', borderCollapse: 'collapse'},
                    children: [
                        cmp('Thead', {
                            children: cmp('Tr', {
                                children: cols.map(function(col) {
                                    return cmp('Th', {
                                        children: uiName(col),
                                        style: {
                                            fontWeight: '700',
                                            backgroundColor: '#2c3e50',
                                            color: '#ecf0f1',
                                            fontSize: '10px',
                                            borderBottom: '2px solid #1a252f',
                                            fontFamily: 'Consolas, "Courier New", monospace',
                                            textTransform: 'uppercase',
                                            letterSpacing: '0.3px',
                                            padding: '4px 6px',
                                        }
                                    });
                                })
                            })
                        }),
                        cmp('Tbody', {
                            children: products.map(function(product) {
                                var row = grouped[product];
                                return cmp('Tr', {
                                    children: cols.map(function(col) {
                                        return cmp('Td', {
                                            children: formatValue(col, row[col]),
                                            style: {
                                                textAlign: col === 'product' ? 'left' : 'center',
                                                padding: '3px 6px',
                                                fontSize: '11px',
                                                fontFamily: 'Consolas, "Courier New", monospace',
                                                border: '1px solid #e0e0e0',
                                                fontWeight: col === 'product' ? '600' : '400',
                                            }
                                        });
                                    })
                                });
                            })
                        })
                    ]
                })
            });
        }

        return dataList.map(buildSummary);
    }""",
    [Output(f'cdx-blotter-{k}-summary', 'children') for k in _BLOTTER_KEYS],
    [Input(f'cdx-blotter-{k}', 'derived_virtual_data') for k in _BLOTTER_KEYS],
    prevent_initial_call=True,
)


@callback(
    [Output(f'cdx-blotter-{k}', 'style_data_conditional') for k in _BLOTTER_KEYS],
    [Input(f'cdx-blotter-{k}', 'derived_virtual_data') for k in _BLOTTER_KEYS],
    [State(f'cdx-blotter-{k}', 'data') for k in _BLOTTER_KEYS],
    prevent_initial_call=True,
)
def style_trade_blotters(*args):
    n = len(_BLOTTER_KEYS)
    virtual_data = args[:n]
    table_data = args[n:]
    styles = []
    for visible, raw in zip(virtual_data, table_data):
        styles.append(build_trade_blotter_styles(visible if visible is not None else raw))
    return styles


# ── Blotter filter callbacks ─────────────────────────────────────────────

def _parse_numeric_filter(term, df):
    match = _re.match(r'^(>=|<=|!=|>|<|=)?\s*(-?[\d.]+)$', term.strip())
    if not match:
        return None, None
    op, val = match.groups()
    op = op or '='
    try:
        val = float(val)
    except ValueError:
        return None, None
    return op, val


def _apply_numeric_op(series, op, val):
    num_series = pd.to_numeric(series, errors='coerce')
    if op == '>':
        return num_series > val
    elif op == '>=':
        return num_series >= val
    elif op == '<':
        return num_series < val
    elif op == '<=':
        return num_series <= val
    elif op == '!=':
        return num_series != val
    else:
        return num_series == val


def _match_columns(hint, columns):
    hint_lower = hint.lower().replace('_', '')
    for col in columns:
        if col.lower() == hint_lower or col.lower().replace('_', '') == hint_lower:
            return [col]
    matches = [c for c in columns if c.lower().startswith(hint_lower) or c.lower().replace('_', '').startswith(hint_lower)]
    if matches:
        return matches
    return [c for c in columns if hint_lower in c.lower().replace('_', '')]


def _apply_single_term(df, term, str_cols, num_cols):
    term = term.strip()
    if not term:
        return pd.Series([True] * len(df), index=df.index)

    if ':' in term and not term.startswith(':') and not term.startswith('~'):
        col_hint, value = term.split(':', 1)
        target_cols = _match_columns(col_hint, df.columns.tolist())
        if target_cols:
            value = value.strip()
            op, num_val = _parse_numeric_filter(value, df)
            mask = pd.Series([False] * len(df), index=df.index)
            for col in target_cols:
                if op is not None and col in num_cols:
                    mask |= _apply_numeric_op(df[col], op, num_val)
                elif value.startswith('~'):
                    pattern = value[1:]
                    try:
                        mask |= df[col].astype(str).str.contains(pattern, case=False, regex=True, na=False)
                    except Exception:
                        mask |= df[col].astype(str).str.contains(pattern, case=False, regex=False, na=False)
                else:
                    mask |= df[col].astype(str).str.contains(value, case=False, regex=False, na=False)
            return mask

    op, val = _parse_numeric_filter(term, df)
    if op is not None:
        mask = pd.Series([False] * len(df), index=df.index)
        for col in num_cols:
            mask |= _apply_numeric_op(df[col], op, val)
        return mask

    if term.startswith('~') or (term.startswith('/') and term.endswith('/')):
        pattern = term[1:] if term.startswith('~') else term[1:-1]
        mask = pd.Series([False] * len(df), index=df.index)
        for col in str_cols:
            try:
                mask |= df[col].astype(str).str.contains(pattern, case=False, regex=True, na=False)
            except Exception:
                mask |= df[col].astype(str).str.contains(pattern, case=False, regex=False, na=False)
        return mask

    mask = pd.Series([False] * len(df), index=df.index)
    for col in str_cols:
        mask |= df[col].astype(str).str.contains(term, case=False, regex=False, na=False)
    return mask


def _apply_quick_filter(data, filter_text):
    if not data or not filter_text:
        return data
    df = pd.DataFrame(data)
    if df.empty:
        return data
    filter_text = filter_text.strip()
    if not filter_text:
        return data

    str_cols = df.columns.tolist()
    num_cols = df.select_dtypes(include=['number']).columns.tolist()

    if '&' in filter_text:
        parts = [p.strip() for p in filter_text.split('&')]
        mask = pd.Series([True] * len(df), index=df.index)
        for part in parts:
            if not part:
                continue
            if '|' in part:
                or_parts = [op.strip() for op in part.split('|')]
                or_mask = pd.Series([False] * len(df), index=df.index)
                for op in or_parts:
                    or_mask |= _apply_single_term(df, op, str_cols, num_cols)
                mask &= or_mask
            else:
                mask &= _apply_single_term(df, part, str_cols, num_cols)
    elif '|' in filter_text:
        parts = [p.strip() for p in filter_text.split('|')]
        mask = pd.Series([False] * len(df), index=df.index)
        for part in parts:
            mask |= _apply_single_term(df, part, str_cols, num_cols)
    else:
        mask = _apply_single_term(df, filter_text, str_cols, num_cols)

    return df[mask].to_dict('records')


def _make_filter_callback(blotter_key):
    table_id = f'cdx-blotter-{blotter_key}'
    filter_id = f'{table_id}-adv-filter'
    store_id = f'{table_id}-full-data'

    @callback(
        Output(table_id, 'data'),
        Input(filter_id, 'value'),
        State(store_id, 'data'),
        prevent_initial_call=True,
    )
    def apply_filter(filter_text, full_data):
        return _apply_quick_filter(full_data, filter_text)

    apply_filter.__name__ = f'apply_filter_{blotter_key}'
    return apply_filter


for _bk in _BLOTTER_KEYS:
    _make_filter_callback(_bk)


@callback(
    [Output(f'cdx-blotter-{k}', 'data', allow_duplicate=True) for k in _BLOTTER_KEYS],
    Input('cdx-blotter-unified-filter', 'value'),
    [State(f'cdx-blotter-{k}-full-data', 'data') for k in _BLOTTER_KEYS],
    prevent_initial_call=True,
)
def apply_unified_blotter_filter(filter_text, *full_data_list):
    results = []
    for data in full_data_list:
        results.append(_apply_quick_filter(data, filter_text))
    return results


# ── URL query-string: read all params on page load ───────────────────────

clientside_callback(
    """function(search) {
        var nu = window.dash_clientside.no_update;
        var p = new URLSearchParams(search || '');
        var vt = ['vol-grids','trade-blotter','positions','expiry-charts','option-stack','relative-vol'];
        var tab = p.get('tab');

        function parseExpiry(raw) {
            var parts = raw.split(',').map(function(s){ return s.trim(); }).filter(Boolean);
            return parts.length ? parts : nu;
        }
        function parseStrike(raw) {
            var parts = raw.split(',').map(function(s){ return s.trim(); }).filter(Boolean);
            return parts.length ? parts.map(function(s) {
                var n = Number(s);
                if (!isNaN(n)) return n;
                var m = s.match(/^([\\d.]+)\\s*[kK]$/);
                if (m) return Number(m[1]) * 1000;
                return s;
            }) : nu;
        }

        return [
            p.has('tickers') && p.get('tickers') ? p.get('tickers').split(',').map(function(s){return s.trim();}) : nu,
            p.get('date') || nu,
            p.get('lookback') || nu,
            p.has('mark_pos') ? (p.get('mark_pos') === 'false' ? [] : ['mark']) : nu,
            p.has('mark_time') ? p.get('mark_time') : nu,
            p.get('thresh_time') || nu,
            p.has('refresh') ? parseInt(p.get('refresh')) : nu,
            (tab && vt.indexOf(tab) >= 0) ? tab : nu,
            p.get('product') ? p.get('product').trim() : nu,
            p.has('expiry') && p.get('expiry') ? parseExpiry(p.get('expiry')) : nu,
            p.has('strike') && p.get('strike') ? parseStrike(p.get('strike')) : nu,
            p.has('ref_px') ? Number(p.get('ref_px')) : nu,
            p.has('ref_bps') ? Number(p.get('ref_bps')) : nu,
            p.has('vol_shift') ? Number(p.get('vol_shift')) : nu,
            p.get('vol_lookback') || nu,
            p.get('rv1_product') || nu,
            p.get('rv1_tenor') || nu,
            p.has('rv1_delta') ? Number(p.get('rv1_delta')) : nu,
            p.get('rv2_product') || nu,
            p.get('rv2_tenor') || nu,
            p.has('rv2_delta') ? Number(p.get('rv2_delta')) : nu,
            p.get('rv1_space') || nu,
            p.get('rv2_space') || nu
        ];
    }""",
    Output(page.CDX_OPTIONS_PRODUCT_ID, 'value'),
    Output(page.CDX_OPTIONS_DATE_ID, 'date', allow_duplicate=True),
    Output(page.CDX_OPTIONS_LOOKBACK_ID, 'value'),
    Output(page.CDX_OPTIONS_MARK_POS_ID, 'value'),
    Output(page.CDX_OPTIONS_MARK_TIME_ID, 'value'),
    Output(page.CDX_OPTIONS_THRESH_TIME_ID, 'value'),
    Output(page.CDX_OPTIONS_REFRESH_FREQ, 'value'),
    Output(page.CDX_OPTIONS_TABS_ID, 'value'),
    Output(page.CDX_OPTIONS_ACTIVE_PRODUCT_ID, 'value', allow_duplicate=True),
    Output(page.CDX_OPTIONS_EXPIRY_ID, 'value', allow_duplicate=True),
    Output(page.CDX_OPTIONS_STRIKE_ID, 'value', allow_duplicate=True),
    Output(page.CDX_OPTIONS_REF_PX_ID, 'value'),
    Output(page.CDX_OPTIONS_REF_BPS_ID, 'value'),
    Output(page.CDX_OPTIONS_VOL_SHIFT_ID, 'value'),
    Output(page.CDX_OPTIONS_VOL_LOOKBACK_ID, 'value'),
    Output(page.CDX_RV_OPT1_PRODUCT_ID, 'value'),
    Output(page.CDX_RV_OPT1_TENOR_ID, 'value'),
    Output(page.CDX_RV_OPT1_DELTA_ID, 'value'),
    Output(page.CDX_RV_OPT2_PRODUCT_ID, 'value'),
    Output(page.CDX_RV_OPT2_TENOR_ID, 'value'),
    Output(page.CDX_RV_OPT2_DELTA_ID, 'value'),
    Output(page.CDX_RV_OPT1_SPACE_ID, 'value', allow_duplicate=True),
    Output(page.CDX_RV_OPT2_SPACE_ID, 'value', allow_duplicate=True),
    Input(page.CDX_OPTIONS_URL, 'search'),
    prevent_initial_call='initial_duplicate',
)


# ── URL query-string: write all inputs back to URL on change ─────────────

clientside_callback(
    """function(tickers, date, lookback, mark_pos, mark_time, thresh_time,
               refresh, tab, product, expiry, strike,
               ref_px, ref_bps, vol_shift, vol_lookback,
               rv1p, rv1t, rv1d, rv2p, rv2t, rv2d, rv1space, rv2space) {
        var url = new URL(window.location);
        var p = url.searchParams;
        if (p.get('product') && !product) return window.dash_clientside.no_update;
        var def_tk = ['CDX-NAHY','CDX-NAIG','ITRAXX-Europe','ITRAXX-Xover'];

        function sd(k, v, d) {
            if (v != null && v !== '' && v !== d) p.set(k, String(v));
            else p.delete(k);
        }

        if (tickers && JSON.stringify(tickers.slice().sort()) !== JSON.stringify(def_tk.slice().sort()))
            p.set('tickers', tickers.join(','));
        else p.delete('tickers');

        var today = new Date().toISOString().slice(0, 10);
        sd('date', date ? date.slice(0, 10) : null, today);
        sd('lookback', lookback, '6m');

        var mp = (mark_pos || []).indexOf('mark') >= 0;
        if (!mp) p.set('mark_pos', 'false'); else p.delete('mark_pos');

        sd('mark_time', mark_time, '');
        sd('thresh_time', thresh_time, '4h');
        sd('refresh', refresh, 300);
        sd('tab', tab, 'vol-grids');

        sd('product', product, null);
        if (expiry && expiry.length) p.set('expiry', expiry.join(','));
        else p.delete('expiry');
        if (strike && strike.length) p.set('strike', strike.join(','));
        else p.delete('strike');

        sd('ref_px', ref_px, null);
        sd('ref_bps', ref_bps, null);
        sd('vol_shift', vol_shift, null);
        sd('vol_lookback', vol_lookback, '5b');
        sd('rv1_product', rv1p, null);
        sd('rv1_tenor', rv1t, '3m');
        sd('rv1_delta', rv1d, 0.25);
        sd('rv2_product', rv2p, null);
        sd('rv2_tenor', rv2t, '3m');
        sd('rv2_delta', rv2d, 0.25);
        sd('rv1_space', rv1space, 'delta');
        sd('rv2_space', rv2space, 'delta');

        var qs = p.toString();
        var newUrl = url.pathname + (qs ? '?' + qs : '');
        if (window.location.pathname + window.location.search !== newUrl)
            window.history.replaceState({}, '', newUrl);
        return Date.now();
    }""",
    Output(page.CDX_OPTIONS_URL_SYNC, 'data'),
    Input(page.CDX_OPTIONS_PRODUCT_ID, 'value'),
    Input(page.CDX_OPTIONS_DATE_ID, 'date'),
    Input(page.CDX_OPTIONS_LOOKBACK_ID, 'value'),
    Input(page.CDX_OPTIONS_MARK_POS_ID, 'value'),
    Input(page.CDX_OPTIONS_MARK_TIME_ID, 'value'),
    Input(page.CDX_OPTIONS_THRESH_TIME_ID, 'value'),
    Input(page.CDX_OPTIONS_REFRESH_FREQ, 'value'),
    Input(page.CDX_OPTIONS_TABS_ID, 'value'),
    Input(page.CDX_OPTIONS_ACTIVE_PRODUCT_ID, 'value'),
    Input(page.CDX_OPTIONS_EXPIRY_ID, 'value'),
    Input(page.CDX_OPTIONS_STRIKE_ID, 'value'),
    Input(page.CDX_OPTIONS_REF_PX_ID, 'value'),
    Input(page.CDX_OPTIONS_REF_BPS_ID, 'value'),
    Input(page.CDX_OPTIONS_VOL_SHIFT_ID, 'value'),
    Input(page.CDX_OPTIONS_VOL_LOOKBACK_ID, 'value'),
    Input(page.CDX_RV_OPT1_PRODUCT_ID, 'value'),
    Input(page.CDX_RV_OPT1_TENOR_ID, 'value'),
    Input(page.CDX_RV_OPT1_DELTA_ID, 'value'),
    Input(page.CDX_RV_OPT2_PRODUCT_ID, 'value'),
    Input(page.CDX_RV_OPT2_TENOR_ID, 'value'),
    Input(page.CDX_RV_OPT2_DELTA_ID, 'value'),
    Input(page.CDX_RV_OPT1_SPACE_ID, 'value'),
    Input(page.CDX_RV_OPT2_SPACE_ID, 'value'),
    prevent_initial_call=True,
)
