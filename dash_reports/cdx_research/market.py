import copy
import logging
import threading
import time

import pandas as pd

from dash_reports.cdx_research.instruments import catalogue, resolve_selection


def market_items(current, prior, items):
    current = current.set_index('security_twid').to_dict('index')
    prior = prior.set_index('security_twid').to_dict('index')
    result = []
    for item in items:
        twid = item['value']
        now, before = current.get(twid, {}), prior.get(twid, {})
        value, previous = now.get('mid'), before.get('mid')
        value = float(value) if pd.notna(value) else None
        previous = float(previous) if pd.notna(previous) else None
        asof, prior_asof = now.get('asof'), before.get('asof')
        result.append({**item, 'price': value, 'previous': previous,
                       'change': value - previous if value is not None and previous is not None else None,
                       'unit': 'px' if item['family'] == 'CDX-NAHY' else 'bps',
                       'asof': asof.tz_convert('America/New_York').isoformat() if pd.notna(asof) else None,
                       'prior_asof': prior_asof.tz_convert('America/New_York').isoformat() if pd.notna(prior_asof) else None})
    return result


class MarketOverview:
    def __init__(self, refresh_seconds=30):
        self._refresh_seconds = refresh_seconds
        self._lock = threading.Lock()
        self._running = False
        self._last_attempt = 0
        self._snapshot = {'items': [], 'options': [], 'defaults': [], 'revision': 0, 'error': None}
        self._index_info = None
        self._prior_day = None
        self._prior = None

    def snapshot(self):
        with self._lock:
            if not self._running and time.time() - self._last_attempt >= self._refresh_seconds:
                self._running = True
                self._last_attempt = time.time()
                threading.Thread(target=self._refresh, daemon=True, name='CDXMarketOverview').start()
            return copy.deepcopy(self._snapshot | {'refreshing': self._running})

    def _refresh(self):
        try:
            from common.utils import dtutil
            from data_access.dbtables.market_data.credit.cdx_tradeweb import get_quote_snapshot
            from sdr.dtccpy.cdx_marker.sdr_mark_helpers import SDRMarkHelpers
            today = dtutil.today()
            prior_day = dtutil.dateadd(today, '-1b')
            if self._index_info is None:
                self._index_info = SDRMarkHelpers.get_index_info()
            current = get_quote_snapshot(today, use_live=True)
            if self._prior_day != prior_day:
                self._prior = get_quote_snapshot(prior_day)
                self._prior_day = prior_day
            names = set(current.security_twid) | set(self._prior.security_twid)
            items = catalogue(names, self._index_info)
            with self._lock:
                self._snapshot = {'items': market_items(current, self._prior, items),
                                  'options': [{'label': x['label'], 'value': x['value']} for x in items],
                                  'defaults': resolve_selection(None, items), 'catalogue': items,
                                  'revision': self._snapshot['revision'] + 1, 'error': None,
                                  'previous_date': str(prior_day), 'asof': time.time()}
        except Exception as exc:
            logging.exception('Market overview refresh failed')
            with self._lock:
                self._snapshot['error'] = str(exc)
        finally:
            with self._lock:
                self._running = False
