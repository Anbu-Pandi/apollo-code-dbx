import logging

import pandas as pd
import plotly.graph_objects as go

from dash_reports.cdx_research.pipeline import figure_panel, table_panel
from dash_reports.cdx_research.instruments import catalogue, resolve_selection
from dash_reports.report_common.pages.plotly_theme import APOLLO_COLORWAY, MUTED, SANS, TEXT
from sdr.dtccpy.credit_utils import _is_px_index


def latest_quotes(frame):
    """Keep the latest *row* per dealer/stream, including withdrawn sides."""
    keys = ['security_twid', 'security_qdlr', 'security_strmtyp']
    return frame.sort_values('msg_time').drop_duplicates(keys, keep='last')


def default_twids(frame):
    return resolve_selection(None, catalogue(frame.security_twid.dropna().unique()))


def et_naive(values):
    times = pd.to_datetime(pd.Series(values))
    if times.dt.tz is not None:
        return times.dt.tz_convert('America/New_York').dt.tz_localize(None)
    return times


def ny_time(values):
    return et_naive(pd.to_datetime(values, utc=True, errors='coerce'))


def trade_notional(trades):
    qty = pd.to_numeric(trades['notional'], errors='coerce')
    if 'original_notional' in trades:
        qty = pd.to_numeric(trades['original_notional'], errors='coerce').fillna(qty)
    return qty


def session_rangebreaks(times, min_gap='4h'):
    stamp = et_naive(times).dropna().sort_values().drop_duplicates()
    pad = pd.Timedelta(milliseconds=1)
    gap = pd.Timedelta(min_gap)
    return [dict(bounds=[(left + pad).isoformat(sep=' '), (right - pad).isoformat(sep=' ')])
            for left, right in zip(stamp.iloc[:-1], stamp.iloc[1:]) if right - left >= gap]


def session_ticks(times):
    stamp = et_naive(times).dropna().sort_values()
    if stamp.empty:
        return [], []
    hours = stamp.dt.floor('h').drop_duplicates()
    if len(hours) > 24:
        hours = stamp.dt.floor('4h').drop_duplicates()
    elif len(hours) > 12:
        hours = stamp.dt.floor('2h').drop_duplicates()
    return hours.tolist(), hours.dt.strftime('%H:%M<br>%d %b').tolist()


def flat_series_rangebreaks(times, values, min_flat='2h'):
    grid = pd.DataFrame({'t': et_naive(times), 'v': pd.Series(values).reset_index(drop=True)}).dropna(subset=['t'])
    grid = grid.sort_values('t')
    if grid.empty:
        return []
    grid['quiet'] = grid['v'].ffill().eq(grid['v'].ffill().shift(1))
    flat = pd.Timedelta(min_flat)
    pad = pd.Timedelta(milliseconds=1)
    out, start = [], None
    for _, row in grid.iterrows():
        if row['quiet'] and start is None:
            start = row['t']
        if not row['quiet'] and start is not None:
            if row['t'] - start >= flat:
                out.append(dict(bounds=[(start + pad).isoformat(sep=' '), (row['t'] - pad).isoformat(sep=' ')]))
            start = None
    return out


def flat_rangebreaks(frame, min_flat='2h'):
    if frame is None or frame.empty:
        return []
    wide = frame.pivot_table(index='msg_time', columns=['security_qdlr', 'security_strmtyp'],
                             values=['bid', 'ask'])
    if wide.empty:
        return []
    prev = wide.ffill().shift(1)
    updated = (wide.notna() & wide.ne(prev)).any(axis=1)
    quoted = frame.dropna(subset=['bid', 'ask'], how='all')
    contributors = quoted.groupby('msg_time')[['security_qdlr', 'security_strmtyp']].apply(
        lambda group: group.drop_duplicates().shape[0])
    grid = pd.DataFrame({'t': wide.index}).sort_values('t')
    grid['quiet'] = ~updated.reindex(grid['t']).to_numpy()
    grid['contributors'] = contributors.reindex(grid['t']).fillna(0).to_numpy()
    flat = pd.Timedelta(min_flat)
    pad = pd.Timedelta(milliseconds=1)
    out, start, peak = [], None, 0
    for _, row in grid.iterrows():
        if row['quiet'] and start is None:
            start, peak = row['t'], row['contributors']
        if row['quiet']:
            peak = max(peak, row['contributors'])
        if not row['quiet'] and start is not None:
            if row['t'] - start >= flat and peak <= 1:
                out.append(dict(bounds=[(start + pad).isoformat(sep=' '), (row['t'] - pad).isoformat(sep=' ')]))
            start, peak = None, 0
    return out


def merge_breaks(*break_lists):
    spans = sorted(
        (pd.Timestamp(b['bounds'][0]), pd.Timestamp(b['bounds'][1]))
        for breaks in break_lists for b in (breaks or []) if b.get('bounds'))
    merged = []
    for start, end in spans:
        if merged and start <= merged[-1][1] + pd.Timedelta(minutes=5):
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [dict(bounds=[s.isoformat(sep=' '), e.isoformat(sep=' ')]) for s, e in merged]


STALE_QUOTE_SECONDS = 1200


def ffill_recent(series, interval_seconds, stale_seconds=STALE_QUOTE_SECONDS):
    limit = max(1, round(stale_seconds / interval_seconds))
    return series.groupby(series.index.date).ffill(limit=limit)


def break_sessions(times, values, min_gap='4h'):
    x = et_naive(times).reset_index(drop=True)
    y = pd.Series(values).reset_index(drop=True)
    gap = pd.Timedelta(min_gap)
    xs, ys = [], []
    prev = None
    for xi, yi in zip(x, y):
        if prev is not None and xi - prev >= gap:
            xs.append(None)
            ys.append(None)
        xs.append(xi)
        ys.append(None if pd.isna(yi) else yi)
        prev = xi
    return xs, ys


def quote_chart(twid, frame, interval_seconds=300):
    fig = go.Figure()
    price = _is_px_index(twid)
    axis, decimals = ('Price', '.3f') if price else ('Spread (bps)', '.2f')
    quoted = frame.loc[frame[['bid', 'ask']].notna().any(axis=1)] if not frame.empty else frame
    silence_gap = pd.Timedelta(seconds=max(6 * interval_seconds, 1800))
    for i, ((dealer, stream), group) in enumerate(quoted.groupby(['security_qdlr', 'security_strmtyp'], sort=True)):
        color = APOLLO_COLORWAY[i % len(APOLLO_COLORWAY)]
        name = f'{dealer} | {stream}'
        for side in ('bid', 'ask'):
            keep = group[side].notna()
            x, y = break_sessions(group.loc[keep, 'msg_time'], group.loc[keep, side], min_gap=silence_gap)
            fig.add_scatter(x=x, y=y, mode='lines', connectgaps=False,
                            name=name, legendgroup=name, showlegend=side == 'bid',
                            line=dict(color=color, width=2.2 if side == 'bid' else 1.6,
                                      dash='dot' if side == 'ask' else 'solid'),
                            hovertemplate='%{x|%d %b %H:%M} ET<br>' + side.title() + ': %{y:' + decimals +
                                          '}<extra>%{fullData.name}</extra>')
    fig.update_layout(height=560, hovermode='x unified',
                      font=dict(family=SANS, size=12, color=TEXT),
                      margin=dict(l=72, r=28, t=24, b=132),
                      legend=dict(title_text='Dealer · solid bid / dotted ask', orientation='h',
                                  yanchor='top', y=-0.08, x=0, font=dict(size=11, color=MUTED),
                                  groupclick='togglegroup', itemwidth=110, tracegroupgap=8),
                      yaxis_title=axis, xaxis_title='New York time (ET)',
                      hoverlabel=dict(namelength=-1, font_size=12))
    ticks, labels = session_ticks(quoted.msg_time) if not quoted.empty else ([], [])
    breaks = merge_breaks(session_rangebreaks(quoted.msg_time, min_gap=silence_gap), flat_rangebreaks(quoted)) \
        if not quoted.empty else []
    fig.update_xaxes(tickformat='%H:%M<br>%d %b', automargin=True,
                     tickmode='array', tickvals=ticks, ticktext=labels,
                     rangebreaks=breaks,
                     showspikes=True, spikemode='across', spikethickness=1, spikedash='dot', spikecolor=MUTED)
    fig.update_yaxes(tickformat=decimals, automargin=True, nticks=8, zeroline=False)
    return fig


def run_quotes(params, market=None):
    from common.utils import dtutil
    from data_access.dbtables.market_data.credit import cdx_tradeweb
    from data_access.dbtables.flow_data import sdr_db
    from credit_strategy.cdx_bbg_msg.cdx_spot import classify_trades

    start, end = params['start'], params['end']
    universe = cdx_tradeweb.get_quote_universe(start, end)
    if universe.empty:
        yield table_panel('Tradeweb quotes', pd.DataFrame(), 'No quotes in the selected date range.')
        return
    index_info = getattr(market, '_index_info', None)
    items = catalogue(universe.security_twid.dropna().unique(), index_info)
    twids = resolve_selection(params['twids'], items)
    available = [{'label': x['label'], 'value': x['value']} for x in items]
    labels = {x['value']: x['label'] for x in items}
    trades = None
    imbalance = None
    from dash_reports.cdx_research.flow_charts import run_dashboard
    for twid in twids:
        label = labels[twid]
        try:
            quotes = cdx_tradeweb.get_cdx_prices(start, end, use_live=True, twids=[twid], compact=True)
            if quotes.empty:
                yield {'title': label, 'note': 'No quotes in the selected range.', 'instruments': available}
                continue
            for col in ('msg_time', 'asof_datetime'):
                quotes[col] = ny_time(quotes[col])
            displayed = latest_quotes(quotes).copy()
            displayed['instrument'] = label
            panel = table_panel(f'{label} — latest dealer quotes', displayed[[
                'instrument', 'security_qdlr', 'security_strmtyp', 'msg_time', 'bid', 'ask', 'bid_qty', 'ask_qty']],
                'New York time. Bid/ask retain the Tradeweb price or spread convention; quantities are raw notionals.')
            panel['instruments'] = available
            sampled = cdx_tradeweb.resample_dataframe(quotes, params['interval']).dropna(subset=['bid', 'ask'], how='all')
            chart = quote_chart(twid, sampled, interval_seconds=params['interval'])
            panel.update(figure_panel(f'{label} — dealer quotes', chart,
                                      f'Last quote: {quotes.msg_time.max()}. Data tab shows latest dealer quotes; quantities are raw notionals.'))
            yield panel


            if trades is None:
                trades = sdr_db.get_cdx_trades(start, end, use_live=True, drop_rolls=True)
                if not trades.empty:
                    trades['notional'] = trade_notional(trades)
                    trades['red9mat'] = trades.red9 + pd.to_datetime(trades.expiration_date).dt.strftime('%Y%m%d')
                    trades['execution_timestamp'] = ny_time(trades.execution_timestamp)
            matching = trades[trades.red9mat.isin(quotes.security_redidmat.unique())] if not trades.empty else trades
            if matching.empty:
                yield {'title': f'{label} — SDR trades', 'note': 'No matching SDR trades.'}
                continue
            classified = classify_trades(quotes, matching[[
                'execution_timestamp', 'red9mat', 'product', 'notional', 'traded_price_or_spread',
                'notional_estimated_flag']], max_lookback_minutes=60)
            if imbalance is None:
                prior_day = dtutil.dateadd(start, '-1b')
                try:
                    imbalance = sdr_db.get_cdx_imbalance(prior_day, prior_day, days=7).set_index('product').to_dict('index')
                    imbalance_note = 'Includes the prior 7-day net notional; intraday notionals use the notebook scaling.'
                except Exception:
                    logging.exception('Prior imbalance unavailable')
                    imbalance = {}
                    imbalance_note = 'Prior 7-day imbalance unavailable; cumulative flow begins at zero.'
            for product, group in classified.groupby('product'):
                one = sampled[sampled.security_redidmat.isin(group.red9mat.unique())].copy()
                one['venue'] = one.security_qdlr + ' | ' + one.security_strmtyp
                series = one.pivot_table(index='msg_time', columns='venue', values=['bid', 'ask']).sort_index().dropna(how='all')
                series = ffill_recent(series, params['interval'])
                fig = run_dashboard(product, series, group.sort_values('execution_timestamp'),
                                    cum_imbalance_begin=imbalance.get(product, {}).get('rolling_net_notional', 0))
                panel = table_panel(f'{product} — price, volume and imbalance', group.sort_values('execution_timestamp', ascending=False))
                panel.update(figure_panel(panel['title'], fig, imbalance_note + ' Data tab shows classified SDR trades.'))
                yield panel
        except Exception as exc:
            logging.exception('Quote analytics failed for %s', twid)
            yield {'title': f'{label} — analytics', 'error': str(exc), 'instruments': available}
        finally:

            quotes = None
            sampled = None
