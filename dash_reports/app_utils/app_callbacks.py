# Central Plotly theme: importing registers the 'apollo' template and sets it as
# the process default, so every chart is themed consistently (no per-chart edits).
try:
    from dash_reports.report_common.pages import plotly_theme  # noqa: F401
except Exception:
    pass

# Register AG Grid at app startup so make_dash_table can return it from within
# callbacks (Dash forbids importing a component library for the first time during
# a callback). Guarded so envs without the package still start.
try:
    import dash_ag_grid  # noqa: F401
    from dash import Input, MATCH, Output, State, callback, clientside_callback
    from dash.exceptions import PreventUpdate

    # Global CSV export: any make_ag_grid table's ⭳ CSV button triggers a
    # client-side export (no server round-trip). dash-ag-grid resets the flag.
    clientside_callback(
        "function(n){ return n ? true : window.dash_clientside.no_update; }",
        Output({"type": "genie-ag", "index": MATCH}, "exportDataAsCsv"),
        Input({"type": "genie-ag-export", "index": MATCH}, "n_clicks"),
        prevent_initial_call=True,
    )

    # Server-paged (infinite) grids: fetch the requested block from the temp
    # Delta table (sort/filter pushed to DuckDB). Browser holds one block.
    @callback(
        Output({"type": "genie-ag-inf", "index": MATCH}, "getRowsResponse"),
        Input({"type": "genie-ag-inf", "index": MATCH}, "getRowsRequest"),
        State({"type": "genie-ag-meta", "index": MATCH}, "data"),
        prevent_initial_call=True,
    )
    def _genie_grid_rows(request, meta):
        if not request or not meta:
            raise PreventUpdate
        from dash_reports.report_common.utils import grid_cache
        rows, total = grid_cache.fetch_block(
            meta["tbl"], meta["cols"], meta["token"],
            request.get("startRow", 0), request.get("endRow", 100),
            request.get("sortModel"), request.get("filterModel"))
        return {"rowData": rows, "rowCount": total}
except Exception:
    pass

import dash_reports.go_private_form.pages.go_pvt_form_callbacks
import dash_reports.issuer_debt_in_index.pages.callbacks

import dash_reports.analyst_questionnaire.pages.callbacks
import dash_reports.issuers_info.pages.callbacks
import dash_reports.compliance.pages.callbacks
import dash_reports.macro_data.pages.callbacks
import dash_reports.trace_portfolio_analysis.pages.callbacks

import dash_reports.report_common.pages.pivot_page_helpers
import dash_reports.report_common.pages.ui_config
import dash_reports.benchmark_performance.pages.callbacks
import dash_reports.cdx_tranche_pricer.pages.callbacks
import dash_reports.cdx_options.pages.callbacks
import dash_reports.cdx_research.pages
import dash_reports.rate_vol_tracker.pages.callbacks
import dash_reports.fx_vol_tracker.pages.callbacks
import dash_reports.cdx_vol_tracker.pages.callbacks
import dash_reports.vol_market_dashboard.pages.callbacks
import dash_reports.cds_cash_basis.pages.callbacks
import dash_reports.vc_dashboard.pages.callbacks
import dash_reports.subscriptions.page
import dash_reports.ai_chat.pages.callbacks
import dash_reports.genai_conversations.pages.callbacks
import dash_reports.data_catalog.pages.callbacks
import dash_reports.data_viewer.pages.data_viewer_page
import dash_reports.etf_apo_funds_report.pages.etf_fund_comparison
import dash_reports.issuer_debt_in_index_movers.pages.movers
import dash_reports.portfolio_stats.pages.portfolio_stats_page
import dash_reports.risk_data_center.pages.risk_data_center
import dash_reports.risk_query.pages.query_page
import dash_reports.risk_report.pages.utils
import dash_reports.trace.pages.trades_by_date
