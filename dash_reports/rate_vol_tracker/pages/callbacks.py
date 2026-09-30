from dash import State

from dash_reports.vol_tracker_common.base_callbacks import register_extended_callbacks
import dash_reports.rate_vol_tracker.pages.rate_vol_page as page

register_extended_callbacks(
    page.page_instance,
    extra_states=[
        State(page.FWD_TERM_ID, 'value'),
        State(page.UND_TENOR_ID, 'value'),
        State(page.SPREAD_PAIR_ID, 'value'),
        State(page.FLY_COMBO_ID, 'value'),
    ],
    filter_builder=lambda rvol, vol_unit, fwd_terms, und_tenors, spreads, flies: (
        page.page_instance.build_filters(rvol, vol_unit, fwd_terms, und_tenors, spreads, flies)
    ),
)
