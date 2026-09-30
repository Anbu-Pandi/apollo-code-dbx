import re

from dash_reports.cdx_options.constants import DEFAULT_TICKERS

PREFIXES = dict(zip(DEFAULT_TICKERS, ('CNHY5-', 'CNIG5-', 'IE5-', 'IECRO5-')))


def instrument_parts(twid):
    for family, prefix in PREFIXES.items():
        if re.fullmatch(re.escape(prefix) + r'\d+', str(twid)):
            return family, int(twid[len(prefix):])
    return None, None


def catalogue(twids, index_info=None):
    products = {}
    if index_info is not None and 'product' in index_info:
        for product in index_info['product'].dropna().unique():
            parts = str(product).rsplit('_', 2)
            if len(parts) == 3 and parts[0] in PREFIXES and parts[1].isdigit() and parts[2].isdigit():
                key = parts[0], int(parts[1])
                if key not in products or int(parts[2]) > int(products[key].rsplit('_', 1)[1]):
                    products[key] = product
    items = []
    for twid in set(twids):
        family, series = instrument_parts(twid)
        if family:
            items.append({'value': twid, 'family': family, 'series': series,
                          'label': products.get((family, series), f'{family} S{series} 5Y')})
    latest = {family: max((x['series'] for x in items if x['family'] == family), default=0)
              for family in DEFAULT_TICKERS}
    for item in items:
        item['is_otr'] = item['series'] == latest[item['family']]
    return sorted(items, key=lambda x: (not x['is_otr'], DEFAULT_TICKERS.index(x['family']), -x['series']))


def resolve_selection(selection, items):
    selected = []
    for value in selection or DEFAULT_TICKERS:
        if value in PREFIXES:
            selected.extend(x['value'] for x in items if x['family'] == value and x['is_otr'])
        elif any(x['value'] == value for x in items):
            selected.append(value)
    wanted = set(selected)
    return [x['value'] for x in items if x['value'] in wanted]


def initial_options():
    return [{'label': f'{family} · current series', 'value': family} for family in DEFAULT_TICKERS]
