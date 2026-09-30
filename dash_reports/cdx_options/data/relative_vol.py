from math import isfinite

from dash_reports.cdx_options.constants import TENOR_OPTIONS
from dash_reports.cdx_options.data.pricing_grid import is_price_based


def parse_rv_option(value, label='Leg 1'):
    if not isinstance(value, dict) or not value.get('product'):
        raise ValueError(f'{label}: select a product in the Relative Vol controls (the main Product selector is separate).')
    product, tenor = value['product'], value.get('tenor')
    if tenor not in TENOR_OPTIONS:
        raise ValueError(f'{label}: select a valid Relative Vol tenor.')
    space = value.get('strike_type') or 'delta'
    native = 'price_strike' if is_price_based(product) else 'spread_strike'
    if space not in ('delta', native):
        raise ValueError(f'{label}: use delta or {native.replace("_", " ")} for {product}.')
    try:
        strike = float(value.get('strike', value.get('delta')))
    except (TypeError, ValueError):
        raise ValueError(f'{label}: enter a delta or strike. Changing strike space clears the previous value.') from None
    if not isfinite(strike) or strike <= 0:
        raise ValueError(f'{label}: enter a positive, finite delta or strike.')
    if space == 'delta' and strike >= 1:
        raise ValueError(f'{label}: delta must be between 0 and 1 (enter 0.25 for 25 delta). '
                         'For a price or spread strike, change Strike space first.')
    return {'product': product, 'tenor': tenor, 'strike': strike, 'strike_type': space}
