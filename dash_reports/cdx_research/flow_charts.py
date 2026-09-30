"""Tradeweb HK notebook flow charts, extracted for server-side rendering."""
import pandas as pd
import numpy as np
from scipy import stats
from sdr.dtccpy import credit_utils
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from dash_reports.cdx_research.quotes import (
    break_sessions, et_naive, flat_series_rangebreaks, merge_breaks, session_rangebreaks, session_ticks,
)
from dash_reports.report_common.pages.plotly_theme import GREEN, MUTED, ORANGE, SAXE, TEXT

def create_price_distribution(df, price_col, qty_col, cat_col, bin_width=0.001):
    df = df.dropna(subset=[price_col, qty_col]).copy()
    if df.empty:
        return 0, None, pd.DataFrame(columns=['price_bin', qty_col]), pd.DataFrame(columns=['price_bin', qty_col])

    price_min, price_max = df[price_col].min(), df[price_col].max()
    bin_width = max(bin_width, (price_max - price_min) / 1000)
    bins = np.arange(price_min, price_max + 2 * bin_width, bin_width)

    df_copy = df.copy()
    df_copy['price_bin'] = pd.cut(df_copy[price_col], bins=bins, labels=bins[:-1] + bin_width / 2, include_lowest=True)

    is_str_cat = 'buy' in str(df_copy[cat_col].unique()).lower() or 'sell' in str(df_copy[cat_col].unique()).lower()
    buy_val = 'buy' if is_str_cat else 1
    sell_val = 'sell' if is_str_cat else -1

    if isinstance(df_copy[cat_col].iloc[0], str):
        buy_df = df_copy[df_copy[cat_col].str.upper() == str(buy_val).upper()]
        sell_df = df_copy[df_copy[cat_col].str.upper() == str(sell_val).upper()]
    else:
        buy_df = df_copy[df_copy[cat_col] == buy_val]
        sell_df = df_copy[df_copy[cat_col] == sell_val]

    buy_price_qty = pd.DataFrame(columns=['price_bin', qty_col])
    if len(buy_df) > 0:
        buy_price_qty = buy_df.groupby('price_bin', observed=True)[qty_col].sum().reset_index()
        buy_price_qty['price_bin'] = buy_price_qty['price_bin'].astype(float)

    sell_price_qty = pd.DataFrame(columns=['price_bin', qty_col])
    if len(sell_df) > 0:
        sell_price_qty = sell_df.groupby('price_bin', observed=True)[qty_col].sum().reset_index()
        sell_price_qty['price_bin'] = sell_price_qty['price_bin'].astype(float)

    max_qty = max(buy_price_qty[qty_col].max() if not buy_price_qty.empty else 0,
                 sell_price_qty[qty_col].max() if not sell_price_qty.empty else 0)

    kde_result = None
    if df[price_col].nunique() > 1:
        y_range = np.linspace(price_min, price_max, 200)
        weights = df[qty_col].values
        if weights.max() > 1000:
            weights = weights / weights.sum() * 1000
        weighted_samples = np.repeat(df[price_col].values, np.maximum(1, weights.astype(int)))
        kde = stats.gaussian_kde(weighted_samples, bw_method='scott')
        kde_result = y_range, kde(y_range)

    return max_qty, kde_result, buy_price_qty, sell_price_qty


def prepare_volumes(trade_df, time_col, price_col, qty_col, cat_col, cat_values, interval_mins):
    if trade_df.empty:
        return pd.DataFrame()
    df = trade_df.copy()
    df['time_bin'] = et_naive(df[time_col]).dt.floor(f'{interval_mins}min')
    parts = []
    for cat in cat_values:
        cat_df = df[df[cat_col].astype(str).str.upper() == str(cat).upper()]
        parts.append(cat_df.groupby('time_bin')[qty_col].sum().rename(f'{cat}_volume'))
    volume_df = pd.concat(parts, axis=1).fillna(0).sort_index().reset_index()
    buy_vol = next((f'{cat}_volume' for cat in cat_values if str(cat).lower() == 'buy' or cat == 1), None)
    sell_vol = next((f'{cat}_volume' for cat in cat_values if str(cat).lower() == 'sell' or cat == -1), None)
    if buy_vol in volume_df.columns and sell_vol in volume_df.columns:
        volume_df['net_volume'] = volume_df[buy_vol] - volume_df[sell_vol]
        volume_df['cum_imbalance'] = volume_df['net_volume'].cumsum()
    return volume_df


def price_bounds(values, is_price):
    values = pd.to_numeric(pd.Series(values), errors='coerce').replace([np.inf, -np.inf], np.nan).dropna()
    values = values[values > 0]
    if is_price:
        priced = values[(values > 50) & (values < 200)]
        if len(priced) >= 3:
            values = priced
    if values.empty:
        return None
    lo, hi = values.quantile(.01), values.quantile(.99)
    pad = max((hi - lo) * .12, .05 if is_price else .25)
    if lo == hi:
        pad = max(abs(lo) * .002, pad)
    return [float(lo - pad), float(hi + pad)]


def calculate_best_prices(time_series_df, product):
    if time_series_df is None or time_series_df.empty:
        return pd.DataFrame()

    result_df = pd.DataFrame(index=time_series_df.index)

    if credit_utils._is_px_index(product):
        bid_col='bid'
        ask_col='ask'
    else:
        bid_col='ask'
        ask_col='bid'

    if isinstance(time_series_df.columns, pd.MultiIndex):

        if bid_col in time_series_df.columns.levels[0]:
            bids = time_series_df[bid_col].copy()

            if credit_utils._is_px_index(product):
                bids_for_max = bids.replace(np.nan, -np.inf)
                result_df['best_bid'] = bids_for_max.max(axis=1)
            else:
                bids_for_max = bids.replace(np.nan, np.inf)
                result_df['best_bid'] = bids_for_max.min(axis=1)

            result_df.loc[result_df['best_bid'].abs() == np.inf, 'best_bid'] = np.nan

        if ask_col in time_series_df.columns.levels[0]:
            asks = time_series_df[ask_col].copy()

            if credit_utils._is_px_index(product):
                asks_for_min = asks.replace(np.nan, np.inf)
                result_df['best_ask'] = asks_for_min.min(axis=1)
            else:
                asks_for_min = asks.replace(np.nan, -np.inf)
                result_df['best_ask'] = asks_for_min.max(axis=1)

            result_df.loc[result_df['best_ask'].abs() == np.inf, 'best_ask'] = np.nan
    else:

        bid_cols = [col for col in time_series_df.columns if 'bid' in str(col).lower()]
        ask_cols = [col for col in time_series_df.columns if 'ask' in str(col).lower()]

        if bid_cols:
            bids = time_series_df[bid_cols].copy()
            if credit_utils._is_px_index(product):
                bids_for_max = bids.replace(np.nan, -np.inf)
                result_df['best_bid'] = bids_for_max.max(axis=1)
            else:
                bids_for_max = bids.replace(np.nan, np.inf)
                result_df['best_bid'] = bids_for_max.min(axis=1)
            result_df.loc[result_df['best_bid'].abs() == np.inf, 'best_bid'] = np.nan

        if ask_cols:
            asks = time_series_df[ask_cols].copy()
            if credit_utils._is_px_index(product):
                asks_for_min = asks.replace(np.nan, np.inf)
                result_df['best_ask'] = asks_for_min.min(axis=1)
            else:
                asks_for_min = asks.replace(np.nan, -np.inf)
                result_df['best_ask'] = asks_for_min.max(axis=1)
            result_df.loc[result_df['best_ask'].abs() == np.inf, 'best_ask'] = np.nan

    return result_df


def run_dashboard(product, time_series_df, trade_df, config=None, cum_imbalance_begin=0):
    from dash_reports.cdx_options.pages.formatting.styles import THEME

    settings = dict(time_col='execution_timestamp', price_col='traded_price_or_spread',
                    qty_col='notional', cat_col='trade_direction', cat_values=['BUY', 'SELL'],
                    interval_mins=5, price_bin_width=None,
                    fig_height=820, show_individual_markets=False)
    settings.update(config or {})
    time_col, price_col, qty_col, cat_col = [settings[k] for k in ('time_col', 'price_col', 'qty_col', 'cat_col')]
    cats = settings['cat_values']
    interval = settings['interval_mins']
    colors = dict(buy=GREEN, sell='#C0392B', unclassified=MUTED, best_bid=GREEN,
                  best_ask=SAXE, vwap=ORANGE, imbalance=TEXT)
    colors.update(settings.get('colors', {}))
    is_price = credit_utils._is_px_index(product)
    label, decimals = ('Price', '.3f') if is_price else ('Spread (bps)', '.2f')
    if time_series_df is not None and not time_series_df.empty:
        time_series_df = time_series_df.copy()
        time_series_df.index = et_naive(time_series_df.index)
        time_series_df = time_series_df.dropna(how='all')
    trade_df = trade_df.copy()
    if not trade_df.empty:
        trade_df[time_col] = et_naive(trade_df[time_col])
        side = trade_df[cat_col].astype(str).str.upper()
        trade_df[cat_col] = np.where(side.isin(['BUY', '1']), 'BUY',
                                     np.where(side.isin(['SELL', '-1']), 'SELL', 'UNCLASSIFIED'))
        cats = ['BUY', 'SELL'] + (['UNCLASSIFIED'] if (trade_df[cat_col] == 'UNCLASSIFIED').any() else [])
    best = calculate_best_prices(time_series_df, product)
    volume = prepare_volumes(trade_df, time_col, price_col, qty_col, cat_col, cats, interval)
    valid = trade_df.dropna(subset=[price_col, qty_col]).copy()
    valid = valid[valid[qty_col] > 0]
    values = [valid[price_col]]
    if not best.empty:
        values.append(best.stack())
    if settings['show_individual_markets'] and time_series_df is not None:
        values.append(pd.Series(time_series_df.to_numpy().ravel()))
    price_range = price_bounds(pd.concat(values, ignore_index=True), is_price)

    fig = make_subplots(rows=3, cols=2, specs=[[{}, {}], [{}, None], [{}, None]],
                        shared_xaxes=True, column_widths=[.76, .24], row_heights=[.56, .22, .22],
                        horizontal_spacing=.06, vertical_spacing=.085,
                        subplot_titles=[label + ' & executions', 'Volume by ' + ('price' if is_price else 'spread'),
                                        'Traded volume', 'Cumulative imbalance'])
    time_hover = '%{x|%d %b %H:%M} ET'
    quote_hover = time_hover + '<br>' + label + ': %{y:' + decimals + '}<extra>%{fullData.name}</extra>'
    if settings['show_individual_markets'] and time_series_df is not None:
        for column in time_series_df:
            x, y = break_sessions(time_series_df.index, time_series_df[column])
            fig.add_trace(go.Scatter(x=x, y=y, connectgaps=False,
                                    name=' | '.join(map(str, column)) if isinstance(column, tuple) else str(column),
                                    mode='lines', line=dict(width=1), opacity=.45, showlegend=False,
                                    hovertemplate=quote_hover), row=1, col=1)
    for field, name in [('best_bid', 'Best Bid'), ('best_ask', 'Best Ask')]:
        if field in best:
            x, y = break_sessions(best.index, best[field])
            fig.add_trace(go.Scatter(x=x, y=y, name=name, mode='lines', connectgaps=False,
                                    line=dict(color=colors[field], width=1.6),
                                    hovertemplate=quote_hover), row=1, col=1)

    for cat in cats:
        key = str(cat).upper()
        if key in ('BUY', '1'):
            side, color = 'Buy', colors['buy']
        elif key in ('SELL', '-1'):
            side, color = 'Sell', colors['sell']
        else:
            side, color = 'Unclassified', colors['unclassified']
        mask = valid[cat_col].astype(str).str.upper() == key
        part = valid[mask]
        if not part.empty:
            sizes = 6 + 12 * np.sqrt(part[qty_col] / valid[qty_col].max())
            symbol = 'triangle-up' if side == 'Buy' else ('triangle-down' if side == 'Sell' else 'circle')
            fig.add_trace(go.Scatter(x=part[time_col], y=part[price_col], mode='markers',
                                    name=side + ' Trades', legendgroup=side,
                                    marker=dict(color=color, size=sizes, opacity=.8,
                                                symbol=symbol,
                                                line=dict(color='white', width=.7)),
                                    customdata=part[qty_col] / 1e6,
                                    hovertemplate=time_hover + '<br>' + label + ': %{y:' + decimals +
                                    '}<br>Notional: %{customdata:,.2f} M<extra>' + side + '</extra>'),
                          row=1, col=1)
        volume_col = f'{cat}_volume'
        if volume_col in volume:
            fig.add_trace(go.Bar(x=volume.time_bin, y=volume[volume_col] / 1e6,
                                name=side + ' Volume', legendgroup=side, showlegend=False,
                                marker_color=color, width=interval * 60000 * .85,
                                hovertemplate=time_hover + '<br>Notional: %{y:,.2f} M<extra>' + side + '</extra>'),
                          row=2, col=1)

    if not valid.empty:
        bins = pd.to_datetime(valid[time_col]).dt.floor(f'{interval}min')
        weighted = valid.assign(time_bin=bins, weighted_price=valid[price_col] * valid[qty_col])
        grouped = weighted.groupby('time_bin')[[qty_col, 'weighted_price']].sum()
        vwap = grouped.weighted_price / grouped[qty_col]
        x, y = break_sessions(vwap.index, vwap)
        fig.add_trace(go.Scatter(x=x, y=y, name='VWAP', mode='lines', connectgaps=False,
                                line=dict(color=colors['vwap'], width=2, dash='dot'),
                                hovertemplate=quote_hover), row=1, col=1)

    if 'cum_imbalance' in volume:
        x, y = break_sessions(volume.time_bin, (volume.cum_imbalance + cum_imbalance_begin) / 1e6)
        fig.add_trace(go.Scatter(x=x, y=y, name='Cumulative Imbalance', mode='lines', connectgaps=False,
                                line=dict(color=colors['imbalance'], width=2, shape='hv'),
                                hovertemplate=time_hover + '<br>Net notional: %{y:+,.2f} M<extra></extra>'),
                      row=3, col=1)
        fig.add_hline(y=cum_imbalance_begin / 1e6, line_dash='dot', line_color=THEME['faint'], row=3, col=1)

    bin_width = settings['price_bin_width'] or .01
    windowed = valid
    if price_range and not valid.empty:
        windowed = valid[valid[price_col].between(*price_range)]
    if not windowed.empty:
        bin_width = settings['price_bin_width'] or max((windowed[price_col].max() - windowed[price_col].min()) / 30,
                                                      .001 if is_price else .01)
    _, density, buy_dist, sell_dist = create_price_distribution(windowed, price_col, qty_col, cat_col, bin_width)
    for side, distribution in [('Buy', buy_dist), ('Sell', sell_dist)]:
        if not distribution.empty:
            fig.add_trace(go.Bar(x=distribution[qty_col] / 1e6, y=distribution.price_bin,
                                orientation='h', name=side + ' Distribution', legendgroup=side,
                                showlegend=False, marker_color=colors[side.lower()], width=bin_width * .9,
                                hovertemplate=label + ': %{y:' + decimals +
                                '}<br>Notional: %{x:,.2f} M<extra>' + side + '</extra>'), row=1, col=2)
    if density is not None:
        grid, weights = density
        totals = pd.concat([buy_dist, sell_dist]).groupby('price_bin')[qty_col].sum()
        maximum = totals.max() / 1e6 if not totals.empty else 0
        fig.add_trace(go.Scatter(x=weights / weights.max() * maximum, y=grid, mode='lines',
                                name='Volume Density', showlegend=False,
                                line=dict(color=MUTED, width=1.4), hoverinfo='skip'), row=1, col=2)

    session_times = []
    if time_series_df is not None and not time_series_df.empty:
        session_times.append(pd.Series(time_series_df.index))
    if not trade_df.empty:
        session_times.append(pd.to_datetime(trade_df[time_col], errors='coerce'))
    if session_times:
        all_times = pd.concat(session_times, ignore_index=True)
        time_breaks = session_rangebreaks(all_times)
        flat_breaks = []
        if not best.empty:
            mid = best[['best_bid', 'best_ask']].mean(axis=1)
            flat_breaks = flat_series_rangebreaks(mid.index, mid)
        rangebreaks = merge_breaks(time_breaks, flat_breaks)
    else:
        rangebreaks = []
    ticks, labels = session_ticks(pd.concat(session_times, ignore_index=True)) if session_times else ([], [])
    for row in (1, 2, 3):
        fig.update_xaxes(type='date', tickformat='%H:%M<br>%d %b', hoverformat='%d %b %Y %H:%M',
                         rangeslider_visible=False, showticklabels=row == 3, rangebreaks=rangebreaks,
                         tickmode='array', tickvals=ticks, ticktext=labels, row=row, col=1)
    fig.update_xaxes(title_text='New York time (ET)', row=3, col=1)
    fig.update_xaxes(title_text='Notional (M)', rangemode='tozero', tickformat=',.0f', nticks=4, row=1, col=2)
    fig.update_yaxes(title_text=label, range=price_range, tickformat=decimals, row=1, col=1)
    fig.update_yaxes(matches='y', range=price_range, showticklabels=False, row=1, col=2)
    fig.update_yaxes(title_text='Notional (M)', rangemode='tozero', tickformat=',.0f', row=2, col=1)
    fig.update_yaxes(title_text='Net notional (M)', tickformat='+,.0f', zeroline=True,
                     zerolinecolor=THEME['border_strong'], row=3, col=1)
    fig.update_xaxes(automargin=True, gridcolor=THEME['border'], showline=True,
                     linecolor=THEME['border'], zeroline=False, title_standoff=10, tickfont_size=11)
    fig.update_yaxes(automargin=True, gridcolor=THEME['border'], showline=False,
                     title_standoff=12, tickfont_size=11, nticks=6)
    fig.update_annotations(font=dict(size=12, color=THEME['muted']), xanchor='left')
    for annotation in fig.layout.annotations:
        annotation.x = fig.layout.xaxis2.domain[0] if annotation.text.startswith('Volume by') else 0
    buy_qty = valid.loc[valid[cat_col] == 'BUY', qty_col].sum() / 1e6
    sell_qty = valid.loc[valid[cat_col] == 'SELL', qty_col].sum() / 1e6
    unclassified = int((valid[cat_col] == 'UNCLASSIFIED').sum())
    summary = (f'<b>Classified trades</b><br>{len(valid):,} executions<br><br>'
               f'Buy: {buy_qty:,.1f} M<br>Sell: {sell_qty:,.1f} M<br>'
               f'Net: {buy_qty - sell_qty:+,.1f} M')
    if unclassified:
        summary += f'<br>Unclassified: {unclassified}'
    summary += '<br><br>Notional in millions'
    fig.add_annotation(x=fig.layout.xaxis2.domain[0], y=.36, xref='paper', yref='paper',
                       xanchor='left', yanchor='top', showarrow=False, align='left',
                       font=dict(size=12, color=THEME['muted']), text=summary)
    fig.update_layout(template='plotly_white', autosize=True, width=None, height=settings['fig_height'],
                      barmode='stack', bargap=.12, hovermode='x',
                      paper_bgcolor='white', plot_bgcolor='white',
                      font=dict(family='Segoe UI, Arial, sans-serif', size=12, color=THEME['ink']),
                      margin=dict(l=80, r=30, t=100, b=65),
                      legend=dict(orientation='h', x=0, y=1.16, yanchor='bottom', font_size=11,
                                  groupclick='togglegroup', tracegroupgap=4))
    return fig
