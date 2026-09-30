"""Interactivity for the Conversation Explorer. Imported by app_callbacks so the
callbacks register at app startup (global-callback pattern)."""

import logging

from dash import ALL, Input, Output, State, callback, ctx
from dash.exceptions import PreventUpdate

from dash_reports.genai_conversations.data import conversation_reader as store
from dash_reports.genai_conversations.pages.explorer import conversation_view, results_list

log = logging.getLogger(__name__)


@callback(
    Output("conv-results", "children"),
    Output("conv-mode", "children"),
    Input("conv-search-btn", "n_clicks"),
    Input("conv-q", "n_submit"),
    State("conv-q", "value"),
    prevent_initial_call=True,
)
def _search(_clicks, _submit, query):
    query = (query or "").strip()
    if not query:
        raise PreventUpdate
    try:
        df, mode = store.search_conversations(query, top_k=25)
        return results_list(df, mode), f"· {mode} search"
    except Exception as exc:
        log.warning("conv search callback failed: %s", exc)
        raise PreventUpdate


@callback(
    Output("conv-detail", "children"),
    Input({"type": "conv-open", "index": ALL}, "n_clicks"),
    Input("conv-id", "value"),
    prevent_initial_call=True,
)
def _open(_clicks, typed_id):
    trig = ctx.triggered_id
    # a result row was clicked
    if isinstance(trig, dict) and trig.get("type") == "conv-open":
        # ignore the phantom fire when rows first render (all n_clicks 0/None)
        if not any((c or 0) for c in (_clicks or [])):
            raise PreventUpdate
        sid = trig.get("index")
        if sid:
            return conversation_view(sid)
    # an id was typed/pasted (or arrived via deep-link)
    if trig == "conv-id" and typed_id:
        return conversation_view(typed_id.strip())
    raise PreventUpdate
