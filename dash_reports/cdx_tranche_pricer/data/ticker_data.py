from dash_reports.cdx_tranche_pricer.data.calcs import get_libor_curve
from pricing_wrapper.market.members.cds_index import get_index_member_curves
from pricing_wrapper.market.curves.cds_curve import flatten_cds_curve_df

def get_ticker_constituents(dt, ticker):
    libor_curves = get_libor_curve(dt)
    df = get_index_member_curves(dt, dt, libor_curves, ticker, original=False, as_df=True, on_the_run=None,
                                 max_series_version_only=True, tenor='5Y')

    df = flatten_cds_curve_df(df)
    return df
