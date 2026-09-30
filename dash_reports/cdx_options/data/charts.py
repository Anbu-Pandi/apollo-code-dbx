import logging
import hashlib

import matplotlib.colors as mcolors
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from dash_reports.report_common.pages import plotly_theme  # noqa: F401 (registers 'apollo')
from sdr.dtccpy.cdx_marker.sdr_cdx_opt_pos_mark import SDRCDXOptPosMark
from sdr.dtccpy.credit_utils import _is_px_index
from common.utils.dfutil import tolist
from common.utils import dtutil

from dash_reports.cdx_options.data.quote_adjust import delta_adjust_to_spot, compute_dealer_vols_at_common_spot


BANK_COLORS = {
    'BANK OF AMERICA': '#b44848', 'BARCLAYS CAPITAL': '#327fa8', 'BNP PARIBAS': '#327b5a',
    'CITIGROUP': '#258794', 'DEUTSCHE BANK': '#395878', 'GOLDMAN SACHS': '#7c6398',
    'JP MORGAN': '#a26c3d', 'MORGAN STANLEY': '#597aa3', 'ROYAL BANK OF CANADA': '#99752b',
}

VOL_COLOR_MAP = {'mid': '#006d77', 'bid': '#395878', 'ask': '#c27637'}
CHART_COLORS = ['#006d77', '#395878', '#c27637', '#7c6398', '#447f60', '#b44848']
SERIES_LABELS = {
    'vol_bid_ann': 'Bid', 'vol_ask_ann': 'Ask', 'vol_mid_ann': 'Mid',
    'gamma_usd': 'Gamma (USD)', 'vega_ann_usd': 'Annualized vega (USD)',
    'delta_usd': 'Delta (USD)', 'theta': 'Theta',
    'prob_wt_notional': 'Probability-weighted notional',
    'prob_wt_estimated_notional': 'Probability-weighted estimated notional',
    'notional': 'Notional', 'estimated_notional': 'Estimated notional',
}


def _series_label(value):
    return SERIES_LABELS.get(str(value), str(value).replace('_', ' ').capitalize())


def _finish_chart(fig, title=None, height=440, timeseries=False):
    fig.update_layout(
        template='plotly_white+apollo', autosize=True, width=None, height=height,
        title=dict(text=title or '', font=dict(size=15), x=0, xanchor='left'),
        font=dict(family='Segoe UI, system-ui, sans-serif', size=12, color='#172033'),
        paper_bgcolor='#ffffff', plot_bgcolor='#ffffff', colorway=CHART_COLORS,
        margin=dict(l=60, r=38, t=55 if title else 40, b=100),
        legend=dict(title=None, orientation='h', x=0, y=-0.22 if height <= 500 else -0.10,
                    xanchor='left', yanchor='top', font=dict(size=11),
                    bgcolor='rgba(0,0,0,0)', groupclick='togglegroup'),
        hovermode='x unified' if timeseries else 'closest',
        hoverlabel=dict(bgcolor='#ffffff', font=dict(size=12)),
    )
    fig.update_xaxes(automargin=True, title_font_size=11, tickfont_size=11,
                     showgrid=False, linecolor='#d9e2ec')
    fig.update_yaxes(automargin=True, title_font_size=11, tickfont_size=11,
                     gridcolor='#edf1f5', zerolinecolor='#d9e2ec')
    fig.update_annotations(font=dict(family='Segoe UI, system-ui, sans-serif', size=12, color='#52657a'))
    if timeseries:
        fig.update_xaxes(tickformat='%d %b', hoverformat='%d %b %Y %H:%M')
    seen = set()
    for trace in fig.data:
        if trace.name:
            trace.legendgroup = trace.name
            if trace.name in seen:
                trace.showlegend = False
            elif trace.showlegend is not False:
                seen.add(trace.name)
    return fig


def _stable_color(name):
    return CHART_COLORS[int(hashlib.sha256(str(name).encode()).hexdigest()[:8], 16) % len(CHART_COLORS)]


def _bank_color(bank):
    return BANK_COLORS.get(bank, _stable_color(bank))


def generate_color_wheel(num_colors):
    colors = list(mcolors.TABLEAU_COLORS.values())
    return colors[:num_colors] if num_colors <= len(colors) else list(mcolors.CSS4_COLORS.values())


def adjust_brightness(color, factor):
    r, g, b = mcolors.to_rgb(color)
    return (int(r * 255 * factor), int(g * 255 * factor), int(b * 255 * factor))


def build_vol_hist_figure(title, vol_ts_df, plot_cols):
    if vol_ts_df is None or vol_ts_df.empty:
        return _finish_chart(go.Figure().add_annotation(
            text='No volatility history for this selection', x=.5, y=.5,
            xref='paper', yref='paper', showarrow=False), title)

    cols_to_plot = [c for c in vol_ts_df.columns if any(k in c for k in VOL_COLOR_MAP)]
    fig = px.line(vol_ts_df, x='asof_date_time', y=cols_to_plot, template='plotly_white+apollo')

    for trace in fig.data:
        trace.mode = 'lines'
        color = next((c for k, c in VOL_COLOR_MAP.items() if k in trace.name), _stable_color(trace.name))
        trace.line.color = color
        trace.line.width = 2
        trace.name = _series_label(trace.name)
        trace.hovertemplate = '%{x|%d %b %Y %H:%M}<br>%{y:,.2f}<extra>%{fullData.name}</extra>'

    fig.update_layout(
        title=title, title_font_size=16,
        legend_title_text='Series', legend_title_font_size=11,
        font=dict(family='Arial, sans-serif', size=11, color='#333'),
        template='plotly_white+apollo'
    )
    fig.update_yaxes(title_text='Normal vol (bps, annualized)', tickformat=',.1f')
    return _finish_chart(fig, title, timeseries=True)


def build_vol_hist_n_current_figure(title, vol_ts_dfs, plot_cols,
                                     curr_vol_dfs=None, sender_vol_dfs=None, trades_dfs=None):
    hist_fig, _ = build_vol_hist_figure_split(title, vol_ts_dfs, plot_cols,
                                               curr_vol_dfs, sender_vol_dfs, trades_dfs)
    return hist_fig


def build_vol_hist_figure_split(title, vol_ts_dfs, plot_cols,
                                 curr_vol_dfs=None, sender_vol_dfs=None, trades_dfs=None):
    curr_vol_dfs = curr_vol_dfs or {}
    sender_vol_dfs = sender_vol_dfs or {}
    trades_dfs = trades_dfs or {}

    se_combos = list(vol_ts_dfs.keys())
    if not se_combos:
        return go.Figure().update_layout(title=title), None

    fixed_height = 440
    total_height = fixed_height * len(se_combos)

    fig = make_subplots(
        rows=len(se_combos), cols=1, shared_xaxes=True, vertical_spacing=0.02,
        specs=[[{}]] * len(se_combos),
        row_heights=[fixed_height / total_height] * len(se_combos)
    )

    vol_ts_df = None
    for chart_num, se in enumerate(se_combos, start=1):
        vol_ts_df = vol_ts_dfs.get(se, pd.DataFrame())
        curr_vol_df = curr_vol_dfs.get(se, pd.DataFrame())
        sender_vol_df = sender_vol_dfs.get(se, pd.DataFrame())
        trades_df = trades_dfs.get(se, pd.DataFrame())

        parts = [vol_ts_df, curr_vol_df] if not vol_ts_df.empty else [curr_vol_df]
        combined = pd.concat(parts, ignore_index=True)
        if 'asof_date_time' in combined.columns:
            combined['asof_date_time'] = pd.to_datetime(combined['asof_date_time'], utc=True).dt.tz_localize(None)
            combined = combined.sort_values('asof_date_time')
        hist_fig = build_vol_hist_figure(title, combined, plot_cols)
        for trace in hist_fig.data:
            fig.add_trace(trace, row=chart_num, col=1)

        if not sender_vol_df.empty:
            _add_sender_traces(fig, sender_vol_df, plot_cols, chart_num)

        if not trades_df.empty:
            _add_trade_traces(fig, trades_df, plot_cols, chart_num)

        fig.update_yaxes(title_text=str(se), row=chart_num, col=1)

    _finish_chart(fig, None, height=total_height, timeseries=True)
    fig.update_yaxes(title_text='Normal vol (bps, annualized)', tickformat=',.1f')

    spot_ts_fig = None
    if vol_ts_df is not None and 'spot_spread' in vol_ts_df.columns and 'spot_price' in vol_ts_df.columns:
        spot_ts_fig = make_subplots(specs=[[{'secondary_y': True}]])
        spot_ts_fig.add_trace(go.Scatter(
            x=vol_ts_df['asof_date_time'], y=vol_ts_df['spot_spread'],
            mode='lines', name='Spread (bps)', line=dict(color='#006d77', width=2)
        ), secondary_y=False)
        spot_ts_fig.add_trace(go.Scatter(
            x=vol_ts_df['asof_date_time'], y=vol_ts_df['spot_price'],
            mode='lines', name='Price', line=dict(color='#395878', width=2)
        ), secondary_y=True)
        spot_ts_fig.update_layout(
            title='Spot Timeseries', title_font_size=16, template='plotly_white+apollo',
            height=350, font=dict(family='Arial, sans-serif', size=11, color='#333')
        )
        spot_ts_fig.update_yaxes(title_text='Spread (bps)', secondary_y=False)
        spot_ts_fig.update_yaxes(title_text='Price', secondary_y=True)
        _finish_chart(spot_ts_fig, None, timeseries=True)

    return fig, spot_ts_fig


def _add_sender_traces(fig, sender_vol_df, plot_cols, row):
    for col in plot_cols:
        if 'bid' in col:
            top = sender_vol_df.groupby('asof_date_time').apply(
                lambda x: x.nlargest(2, col)
            ).reset_index(drop=True)
            for bank, bdf in top.groupby('sender_bank'):
                fig.add_trace(go.Scatter(
                    x=bdf['asof_date_time'], y=bdf[col], mode='markers', name=bank,
                    marker=dict(color=_bank_color(bank), size=7, line=dict(color='white', width=.7))
                ), row=row, col=1)
        elif 'ask' in col:
            top = sender_vol_df.groupby('asof_date_time').apply(
                lambda x: x.nsmallest(2, col)
            ).reset_index(drop=True)
            for bank, bdf in top.groupby('sender_bank'):
                fig.add_trace(go.Scatter(
                    x=bdf['asof_date_time'], y=bdf[col], mode='markers', name=bank,
                    marker=dict(color=_bank_color(bank), size=7, line=dict(color='white', width=.7))
                ), row=row, col=1)


def _add_trade_traces(fig, trades_df, plot_cols, row):
    col = [x for x in plot_cols if 'bid' not in x and 'ask' not in x][0]
    col = col.replace('_mid', '')
    if col not in trades_df.columns:
        return

    hover_labels = {'dissemination_identifier': 'id', 'notional': 'notnl_mm', 'execution_timestamp': 'time'}
    hover_text = []
    vol_label = 'implied_vol' if col == 'implied_vol' else f'implied_vol ({col})'
    for _, r in trades_df.iterrows():
        parts = [f'{hover_labels[c]}: {r[c]}' for c in hover_labels if c in r.index]
        hover_text.append('<br>'.join(parts))

    fig.add_trace(go.Scatter(
        x=trades_df['execution_timestamp'], y=trades_df[col],
        mode='markers', name='Reported trades',
        marker=dict(color='#b44848', size=7, symbol='diamond', line=dict(color='white', width=.7)),
        text=hover_text, hovertemplate=f'%{{text}}<br>{vol_label}: %{{y:.4g}}<extra></extra>'
    ), row=row, col=1)


def _strike_frame(df, plot_cols, reducer=None, label='frame'):
    empty = pd.DataFrame(index=pd.Index([], name='strike'), columns=plot_cols, dtype=float)
    if df is None or df.empty:
        return empty
    if 'strike' not in df.columns:
        logging.warning(f'build_exp_vols_figure: {label} missing strike column')
        return empty
    work = df.copy()
    for col in plot_cols:
        if col not in work.columns:
            work[col] = np.nan
    if reducer is None:
        return work.drop_duplicates('strike', keep='last').set_index('strike')
    return getattr(work.groupby('strike')[plot_cols], reducer)()


def _live_fwd_for_expiry(live_fwd_bps, expiry_dt):
    if isinstance(live_fwd_bps, dict):
        exp_key = str(pd.Timestamp(expiry_dt).normalize().date())
        return live_fwd_bps.get(exp_key)
    return live_fwd_bps


def build_exp_vols_figure(title, sender_mark, curr_mark, product, expiries, plot_cols,
                          agg=None, live_spot_bps=None, live_fwd_bps=None, grid=None):
    expiries = tolist(expiries)
    if not expiries:
        return go.Figure().update_layout(title=title)

    fixed_height = 440
    total_height = fixed_height * len(expiries)

    fig = make_subplots(
        rows=len(expiries), cols=1, shared_xaxes=True, vertical_spacing=0.05,
        specs=[[{}]] * len(expiries), subplot_titles=[pd.Timestamp(e).strftime('%d %b %Y') for e in expiries],
        row_heights=[fixed_height / total_height] * len(expiries)
    )

    for chart_num, expiry in enumerate(expiries, start=1):
        expiry_dt = pd.to_datetime(expiry).normalize()
        filtered_sender = sender_mark[
            (sender_mark['product'] == product) &
            (pd.to_datetime(sender_mark['option_end_date']).dt.normalize() == expiry_dt)
        ] if not sender_mark.empty and 'option_end_date' in sender_mark.columns else pd.DataFrame()
        filtered_curr = curr_mark[
            (curr_mark['product'] == product) &
            (pd.to_datetime(curr_mark['option_end_date']).dt.normalize() == expiry_dt)
        ] if not curr_mark.empty and 'option_end_date' in curr_mark.columns else pd.DataFrame()

        if filtered_sender.empty and filtered_curr.empty:
            logging.warning(f'build_exp_vols_figure: no data for {product}/{expiry}')
            continue

        min_data = _strike_frame(filtered_sender, plot_cols, reducer='min', label='sender_mark')
        max_data = _strike_frame(filtered_sender, plot_cols, reducer='max', label='sender_mark')
        mid_data = _strike_frame(filtered_curr, plot_cols, label='curr_mark')

        all_strikes = sorted(set(min_data.index.tolist() + max_data.index.tolist() + mid_data.index.tolist()))
        if not all_strikes:
            logging.warning(f'build_exp_vols_figure: no strike data for {product}/{expiry}')
            continue
        min_data = min_data.reindex(all_strikes).interpolate(method='linear')
        max_data = max_data.reindex(all_strikes).interpolate(method='linear')

        is_price = _is_px_index(product)
        spot_col = 'spot_price' if is_price else 'spot_spread'
        cm_product = curr_mark[curr_mark['product'] == product]
        spot_val = cm_product[spot_col].dropna().iloc[0] if (
            not cm_product.empty and spot_col in cm_product.columns and cm_product[spot_col].notna().any()
        ) else (max(all_strikes) if is_price else min(all_strikes))
        strike_range = SDRCDXOptPosMark.get_strike_range(is_price, tight_strike=spot_val)
        if is_price:
            ceil_spot = np.ceil(spot_val * 2) / 2
            above = np.arange(ceil_spot + 0.5, ceil_spot + 0.5 * 7, 0.5)
            strike_range = np.sort(np.unique(np.concatenate([strike_range, above])))
        else:
            floor_spot = np.floor(spot_val / 2.5) * 2.5
            below = np.arange(floor_spot - 2.5, floor_spot - 2.5 * 5, -2.5)
            strike_range = np.sort(np.unique(np.concatenate([strike_range, below[below > 0]])))

        mid_data = mid_data.reindex(strike_range)
        min_data = min_data.reindex(strike_range)
        max_data = max_data.reindex(strike_range)

        for col in plot_cols:
            if 'bid' in col or 'ask' in col:
                line_color = ('#3867B0', 'lightblue') if 'bid' in col else ('#FDAE2A', 'moccasin')
                fig.add_trace(go.Scatter(
                    x=min_data.index, y=min_data[col], line=dict(width=0),
                    showlegend=False, mode='lines', name=f'Min {col}'
                ), row=chart_num, col=1)
                fig.add_trace(go.Scatter(
                    x=max_data.index, y=max_data[col], fill='tonexty', mode='lines',
                    name=f'Max {col}', showlegend=False,
                    line=dict(width=0), fillcolor=line_color[1]
                ), row=chart_num, col=1)
                fig.add_trace(go.Scatter(
                    x=mid_data.index, y=mid_data[col], mode='lines+markers',
                    name=_series_label(col), line=dict(color=line_color[0], width=2)
                ), row=chart_num, col=1)

        expiry_fwd_bps = _live_fwd_for_expiry(live_fwd_bps, expiry_dt)
        _add_bank_markers_to_exp(fig, product, expiry_dt, strike_range, chart_num,
                                 agg=agg, live_spot_bps=live_spot_bps, live_fwd_bps=expiry_fwd_bps, grid=grid,
                                 filtered_sender=filtered_sender, plot_cols=plot_cols)
        fig.update_yaxes(title_text='Normal vol (bps, annualized)', tickformat=',.1f', row=chart_num, col=1)

    fig.update_layout(
        title=title, xaxis_title='Strike Price', legend_title='Data',
        template='plotly_white+apollo', height=total_height,
        margin=dict(l=80, r=80, t=80, b=80)
    )
    _finish_chart(fig, title, height=total_height)
    fig.update_xaxes(title_text='Price strike' if _is_px_index(product) else 'Spread strike (bps)')
    return fig


def _add_bank_markers_to_exp(fig, product, expiry_dt, strike_range, row,
                             agg=None, live_spot_bps=None, live_fwd_bps=None, grid=None,
                             filtered_sender=None, plot_cols=None):
    if agg is not None and hasattr(agg, 'quotes') and not agg.quotes.empty:
        _add_bank_markers_from_quotes(fig, agg, product, expiry_dt, strike_range, live_spot_bps, row,
                                      live_fwd_bps=live_fwd_bps, grid=grid)
        return
    if filtered_sender is not None and plot_cols is not None:
        _add_bank_markers_from_sender(fig, filtered_sender, plot_cols, strike_range, row)


def _add_bank_markers_from_quotes(fig, agg, product, expiry_dt, strike_range, live_spot_bps, row, live_fwd_bps=None, grid=None):
    q = agg.quotes[agg.quotes['product'] == product].copy()
    if q.empty:
        return
    exp_date = expiry_dt.date() if hasattr(expiry_dt, 'date') else pd.to_datetime(expiry_dt).date()
    q = q[pd.to_datetime(q['expiry']).dt.date == exp_date]
    if q.empty:
        return

    strike_set = set(float(s) for s in strike_range)
    q['strike_display'] = pd.to_numeric(q['strike_display'], errors='coerce')
    q = q[q['strike_display'].apply(lambda s: any(abs(s - sr) < 0.01 for sr in strike_set))]
    if q.empty:
        return

    is_price = _is_px_index(product)
    spot_val = pd.to_numeric(q['spot'], errors='coerce').dropna().iloc[-1] if q['spot'].notna().any() else np.nan

    if is_price and 'par_spread_spot_calc' in q.columns:
        pss = pd.to_numeric(q['par_spread_spot_calc'], errors='coerce')
        spot_bps = pss.dropna().iloc[-1] * 1e4 if pss.notna().any() else (spot_val if pd.notna(spot_val) else np.nan)
    else:
        spot_bps = pd.to_numeric(q['spot'], errors='coerce').dropna().iloc[-1] if q['spot'].notna().any() else np.nan
    use_live = live_spot_bps if pd.notna(live_spot_bps) else spot_bps
    use_fwd = live_fwd_bps if pd.notna(live_fwd_bps) and live_fwd_bps > 0 else use_live
    if not pd.notna(use_live) or use_live <= 0:
        return

    q['asof_date_time'] = pd.to_datetime(q['asof_date_time'])
    for c in ['pay_vol_bid_calc', 'pay_vol_ask_calc', 'rec_vol_bid_calc', 'rec_vol_ask_calc']:
        if c in q.columns:
            q[c] = pd.to_numeric(q[c], errors='coerce')
    latest = q.sort_values('asof_date_time').groupby(['sender_bank', 'strike_display']).last().reset_index()

    rows_out = []
    for _, r in latest.iterrows():
        stk = float(r['strike_display'])
        payer_otm = (stk < spot_val if is_price else stk > spot_val) if pd.notna(spot_val) else True
        vb_col = 'pay_vol_bid_calc' if payer_otm else 'rec_vol_bid_calc'
        va_col = 'pay_vol_ask_calc' if payer_otm else 'rec_vol_ask_calc'
        bid_prem_col = 'payer_bid' if payer_otm else 'receiver_bid'
        ask_prem_col = 'payer_ask' if payer_otm else 'receiver_ask'
        solve_measure = 'payer_premium' if payer_otm else 'receiver_premium'
        t_str = r['asof_date_time'].strftime('%H:%M') if pd.notna(r.get('asof_date_time')) else ''
        bank = r['sender_bank']
        label = f'{bank}@{t_str}' if t_str else bank

        bid_v = pd.to_numeric(pd.Series([r.get(vb_col)]), errors='coerce').iloc[0]
        ask_v = pd.to_numeric(pd.Series([r.get(va_col)]), errors='coerce').iloc[0]
        if grid is not None:
            dealer_spot_dec = np.nan
            if is_price and pd.notna(r.get('spot')):
                dealer_spot_dec = grid.price_to_spread(float(r['spot']))
            elif pd.notna(r.get('par_spread_spot_calc')):
                dealer_spot_dec = float(r['par_spread_spot_calc'])
            elif pd.notna(r.get('spot')):
                spot_raw = float(r['spot'])
                dealer_spot_dec = spot_raw / 1e4 if spot_raw > 1 else spot_raw
            if pd.notna(dealer_spot_dec):
                bid_prem = pd.to_numeric(pd.Series([r.get(bid_prem_col)]), errors='coerce').iloc[0]
                ask_prem = pd.to_numeric(pd.Series([r.get(ask_prem_col)]), errors='coerce').iloc[0]
                if pd.notna(bid_prem) and bid_prem > 0:
                    solved_bid = grid.solve_implied_vol(r.get('expiry'), stk, dealer_spot_dec, float(bid_prem), solve_measure)
                    if pd.notna(solved_bid) and solved_bid > 0:
                        bid_v = solved_bid * 100
                if pd.notna(ask_prem) and ask_prem > 0:
                    solved_ask = grid.solve_implied_vol(r.get('expiry'), stk, dealer_spot_dec, float(ask_prem), solve_measure)
                    if pd.notna(solved_ask) and solved_ask > 0:
                        ask_v = solved_ask * 100

        if pd.notna(bid_v):
            rows_out.append({'strike': stk, 'vol_ann': round(bid_v / 100 * use_fwd, 1),
                             'bank': bank, 'label': label, 'side': 'bid'})
        if pd.notna(ask_v):
            rows_out.append({'strike': stk, 'vol_ann': round(ask_v / 100 * use_fwd, 1),
                             'bank': bank, 'label': label, 'side': 'ask'})
    if not rows_out:
        return

    df = pd.DataFrame(rows_out)
    top_bids = df[df['side'] == 'bid'].groupby('strike', group_keys=False).apply(
        lambda x: x.nlargest(2, 'vol_ann')) if (df['side'] == 'bid').any() else pd.DataFrame()
    top_asks = df[df['side'] == 'ask'].groupby('strike', group_keys=False).apply(
        lambda x: x.nsmallest(2, 'vol_ann')) if (df['side'] == 'ask').any() else pd.DataFrame()
    top = pd.concat([top_bids, top_asks], ignore_index=True)
    if top.empty:
        return

    for label in top['label'].unique():
        bdf = top[top['label'] == label]
        fig.add_trace(go.Scatter(
            x=bdf['strike'], y=bdf['vol_ann'], mode='markers',
            marker=dict(color=_bank_color(bdf['bank'].iloc[0]), size=7, line=dict(color='white', width=.7)),
            name=label,
        ), row=row, col=1)


def _add_bank_markers_from_sender(fig, filtered_sender, plot_cols, strike_range, row):
    bid_col = [x for x in plot_cols if 'bid' in x]
    ask_col = [x for x in plot_cols if 'ask' in x]
    if not bid_col or not ask_col:
        return
    bid_col, ask_col = bid_col[0], ask_col[0]
    if (filtered_sender is None or filtered_sender.empty or 'strike' not in filtered_sender.columns
            or 'sender_bank' not in filtered_sender.columns):
        return
    if bid_col not in filtered_sender.columns or ask_col not in filtered_sender.columns:
        return
    top_bids = filtered_sender.groupby('strike').apply(
        lambda x: x.nlargest(2, bid_col)
    ).dropna().reset_index(drop=True).rename(columns={bid_col: 'best_vol'})
    top_asks = filtered_sender.groupby('strike').apply(
        lambda x: x.nsmallest(2, ask_col)
    ).dropna().reset_index(drop=True).rename(columns={ask_col: 'best_vol'})
    top_df = pd.concat([top_bids, top_asks], ignore_index=True)
    top_df = top_df[top_df['strike'].isin(strike_range)]
    for bank in top_df['sender_bank'].unique():
        bdf = top_df[top_df['sender_bank'] == bank]
        fig.add_trace(go.Scatter(
            x=bdf['strike'], y=bdf['best_vol'], mode='markers',
            marker=dict(color=_bank_color(bank), size=7, line=dict(color='white', width=.7)),
            name=bank,
        ), row=row, col=1)


def build_multi_gby_figure(df, vol_ref_spot_spread, vol_ref_spot_price, is_price_based,
                           max_columns=2, ref_spot=None, x_range=None):
    if df.empty:
        return go.Figure()

    num_columns = len(df.columns)
    color_wheel = generate_color_wheel(num_columns)
    column_colors = {col: c for col, c in zip(df.columns, color_wheel)}

    cols_per_row = min(num_columns, max_columns)
    num_rows = (num_columns + cols_per_row - 1) // cols_per_row

    fig = make_subplots(rows=num_rows, cols=cols_per_row,
                        subplot_titles=[_series_label(c) for c in df.columns],
                        horizontal_spacing=0.14, vertical_spacing=0.22 if num_rows > 1 else 0.1)

    index_is_spread = bool(df.index.names) and 'spot_spread' in str(df.index.names[0])
    scenario_values = df.index.get_level_values(0).astype(float).unique()
    scenario_values = sorted(v for v in scenario_values if pd.notna(v))
    if len(scenario_values) >= 2:
        gaps = [b - a for a, b in zip(scenario_values, scenario_values[1:]) if (b - a) > 0]
        median_gap = float(pd.Series(gaps).median()) if gaps else 1.0
        bar_width = max(median_gap * 0.8, (scenario_values[-1] - scenario_values[0]) / 200.0)
    else:
        bar_width = None

    col_counter = 1
    row_counter = 1
    expiry_colors = {}
    if 'expiration_date' in df.index.names:
        expiry_colors = {expiry: plotly_theme.APOLLO_COLORWAY[i % len(plotly_theme.APOLLO_COLORWAY)]
                         for i, expiry in enumerate(sorted(df.index.get_level_values('expiration_date').unique()))}

    for column in df.columns:
        base_color = column_colors[column]
        has_exp = 'expiration_date' in df.index.names

        if has_exp:
            unique_dates = df.index.get_level_values('expiration_date').unique()
            for exp_date in unique_dates:
                slice_ = df.xs(exp_date, level='expiration_date')
                color_str = expiry_colors[exp_date]
                x_vals = slice_.index.get_level_values(0).tolist()
                y_vals = slice_[column].tolist()
                fig.add_trace(go.Bar(
                    x=x_vals, y=y_vals, name=pd.Timestamp(exp_date).strftime('%d %b %Y'),
                    marker_color=color_str, showlegend=(column == df.columns[0]),
                    width=bar_width,
                ), row=row_counter, col=col_counter)
        else:
            x_vals = df.index.get_level_values(0).tolist()
            y_vals = df[column].tolist()
            fig.add_trace(go.Bar(
                x=x_vals, y=y_vals, name=_series_label(column),
                marker_color=f'rgb({base_color[0]},{base_color[1]},{base_color[2]})',
                showlegend=False, width=bar_width,
            ), row=row_counter, col=col_counter)

        ref_val = vol_ref_spot_spread if index_is_spread else vol_ref_spot_price
        if ref_val is not None:
            fig.add_vline(x=ref_val, line_width=2, line_dash='dash', line_color='black',
                          annotation_text='spot', annotation_position='top left',
                          row=row_counter, col=col_counter)

        if ref_spot is not None and not index_is_spread:
            fig.add_vline(x=ref_spot, line_width=2, line_dash='dot', line_color='#e74c3c',
                          annotation_text='ref', annotation_position='top right',
                          row=row_counter, col=col_counter)

        col_counter += 1
        if col_counter > cols_per_row:
            col_counter = 1
            row_counter += 1

    _finish_chart(fig, height=340 * num_rows + 100)
    fig.update_layout(barmode='stack')
    fig.update_xaxes(title_text='Spot spread (bps)' if index_is_spread else
                     ('Spot price' if 'scenario' in str(df.index.names[0]) else
                      ('Price strike' if is_price_based else 'Spread strike (bps)')))
    fig.update_yaxes(tickformat='~s', zeroline=True)
    if x_range is not None:
        fig.update_xaxes(range=list(x_range))
    return fig


def build_option_ladder(agg, product, expiry=None, strike=None, grid=None, live_spot_bps=None, live_fwd_bps=None):
    all_products = agg.quotes['product'].unique().tolist() if 'product' in agg.quotes.columns else []
    q = agg.quotes[agg.quotes['product'] == product].copy()
    logging.info(f'[option_ladder] {product}: agg.quotes rows={len(agg.quotes)}, '
                 f'products_in_agg={all_products[:10]}, matched={len(q)}, '
                 f'has_expiry={"expiry" in q.columns}, has_strike_display={"strike_display" in q.columns}, '
                 f'cols={list(agg.quotes.columns)}')
    if q.empty:
        return pd.DataFrame()
    initial_count = len(q)
    if 'asof_date_time' in q.columns and not q['asof_date_time'].isna().all():
        min_t = pd.to_datetime(q['asof_date_time']).min()
        max_t = pd.to_datetime(q['asof_date_time']).max()
        logging.info(f'[option_ladder] quotes time range: {min_t} to {max_t} ({initial_count} quotes)')
    if expiry is not None:
        exp_date = pd.to_datetime(expiry).date()
        q = q[pd.to_datetime(q['expiry']).dt.date == exp_date]
        logging.info(f'[option_ladder] after expiry filter ({exp_date}): {len(q)}/{initial_count}')
    if strike is not None:
        strike_f = float(strike)
        before_strike = len(q)
        q_exact = q[np.abs(q['strike_display'] - strike_f) < 0.01]
        if len(q_exact) == 0 and before_strike > 0:
            avail = q['strike_display'].dropna().unique()
            if len(avail):
                nearest = avail[np.argmin(np.abs(avail - strike_f))]
                gap = abs(nearest - strike_f)
                if gap < 1.0:
                    logging.info(f'[option_ladder] strike {strike_f} not exact — snapping to nearest {nearest} (gap={gap:.3f})')
                    q = q[np.abs(q['strike_display'] - nearest) < 0.01]
                    strike = nearest
                else:
                    logging.warning(f'[option_ladder] strike {strike_f} no match, nearest={nearest} gap={gap:.1f} too far, '
                                    f'available={sorted(avail)[:10]}')
                    q = q_exact
            else:
                q = q_exact
        else:
            q = q_exact
    if q.empty:
        return pd.DataFrame()

    is_price = _is_px_index(product)
    common_spot_px = np.nan
    if grid is not None and pd.notna(live_spot_bps):
        try:
            common_spot_px = grid.spread_to_price(float(live_spot_bps) / 1e4)
        except Exception:
            common_spot_px = np.nan

    if is_price:
        if pd.notna(common_spot_px):
            spot_val = float(common_spot_px)
        elif 'spot' in q.columns and q['spot'].notna().any():
            spot_val = q['spot'].dropna().iloc[-1]
        else:
            spot_val = np.nan
    else:
        if pd.notna(live_spot_bps):
            spot_val = float(live_spot_bps)
        elif 'spread_spot' in q.columns and q['spread_spot'].notna().any():
            spot_val = q['spread_spot'].dropna().iloc[-1]
        else:
            spot_val = np.nan

    if is_price:
        payer_otm = float(strike) < spot_val if pd.notna(spot_val) and strike is not None else True
    else:
        strike_num = float(strike) if strike is not None else np.nan
        payer_otm = strike_num > spot_val if pd.notna(spot_val) and pd.notna(strike_num) else True

    pay_vol_bid_col = 'pay_vol_bid_calc'
    pay_vol_ask_col = 'pay_vol_ask_calc'
    rec_vol_bid_col = 'rec_vol_bid_calc'
    rec_vol_ask_col = 'rec_vol_ask_calc'
    otm_vol_bid_col = pay_vol_bid_col if payer_otm else rec_vol_bid_col
    otm_vol_ask_col = pay_vol_ask_col if payer_otm else rec_vol_ask_col

    all_vol_cols = [pay_vol_bid_col, pay_vol_ask_col, rec_vol_bid_col, rec_vol_ask_col]
    delta_cols = ['payer_delta', 'receiver_delta', 'risky_pv01_spot_calc']
    keep = ['sender_bank', 'asof_date_time', 'expiry', 'strike_display',
            'spot', 'par_spread_spot_calc', 'payer_bid', 'payer_ask', 'receiver_bid', 'receiver_ask',
            *all_vol_cols, *delta_cols]
    keep = [c for c in keep if c in q.columns]
    q = q[keep].copy()
    q['asof_date_time'] = pd.to_datetime(q['asof_date_time'])

    q_sorted = q.sort_values('asof_date_time')
    latest = q_sorted.groupby('sender_bank').last().reset_index()

    for c in ['payer_bid', 'payer_ask', 'receiver_bid', 'receiver_ask',
              *all_vol_cols, *delta_cols, 'spot', 'par_spread_spot_calc']:
        if c in latest.columns:
            latest[c] = pd.to_numeric(latest[c], errors='coerce')

    for vc in [*all_vol_cols, *delta_cols]:
        if vc not in latest.columns:
            continue
        missing = latest[vc].isna()
        if not missing.any():
            continue
        vol_last = q_sorted.dropna(subset=[vc]).groupby('sender_bank')[vc].last()
        for bank in latest.loc[missing, 'sender_bank']:
            if bank in vol_last.index:
                latest.loc[latest['sender_bank'] == bank, vc] = float(vol_last[bank])

    def _dealer_spot_dec(row):
        if is_price:
            if grid is not None and pd.notna(row.get('spot')):
                spot_dec = grid.price_to_spread(float(row['spot']))
                if pd.notna(spot_dec):
                    return spot_dec
            if 'par_spread_spot_calc' in latest.columns and pd.notna(row.get('par_spread_spot_calc')):
                return float(row['par_spread_spot_calc'])
            return np.nan
        if 'par_spread_spot_calc' in latest.columns and pd.notna(row.get('par_spread_spot_calc')):
            return float(row['par_spread_spot_calc'])
        if pd.notna(row.get('spot')):
            s = float(row['spot'])
            return (s / 1e4) if s > 1 else s
        return np.nan

    if grid is not None:
        solved = {'pay_bid': 0, 'pay_ask': 0, 'rec_bid': 0, 'rec_ask': 0}
        for idx in latest.index:
            row = latest.loc[idx]
            exp_v = row.get('expiry')
            stk_v = row.get('strike_display')
            if pd.isnull(exp_v) or pd.isnull(stk_v):
                continue
            dlr_spot_dec = _dealer_spot_dec(row)
            if pd.isna(dlr_spot_dec):
                continue
            sides = [
                (pay_vol_bid_col, 'payer_bid', 'payer_premium', 'pay_bid'),
                (pay_vol_ask_col, 'payer_ask', 'payer_premium', 'pay_ask'),
                (rec_vol_bid_col, 'receiver_bid', 'receiver_premium', 'rec_bid'),
                (rec_vol_ask_col, 'receiver_ask', 'receiver_premium', 'rec_ask'),
            ]
            for vc, pc, measure, key in sides:
                if vc not in latest.columns or pc not in latest.columns:
                    continue
                if pd.notna(latest.at[idx, vc]):
                    continue
                prem = float(row[pc]) if pd.notna(row.get(pc)) and float(row[pc]) > 0 else np.nan
                if pd.isna(prem):
                    continue
                vol = grid.solve_implied_vol(exp_v, stk_v, dlr_spot_dec, prem, measure)
                if pd.notna(vol) and vol > 0:
                    latest.at[idx, vc] = vol * 100
                    solved[key] += 1
        nonzero = {k: v for k, v in solved.items() if v}
        if nonzero:
            logging.info(f'[option_ladder] local vol solve: {nonzero}')

    n_dealers = len(latest)

    def _notna_count(col):
        return int(latest[col].notna().sum()) if col in latest.columns else 0

    logging.info(
        f'[option_ladder] {n_dealers} dealers, payer_otm={payer_otm}, '
        f'pay_bid={_notna_count(pay_vol_bid_col)}, pay_ask={_notna_count(pay_vol_ask_col)}, '
        f'rec_bid={_notna_count(rec_vol_bid_col)}, rec_ask={_notna_count(rec_vol_ask_col)}, '
        f'is_price={is_price}, spot_val={spot_val}, strike={strike}'
    )

    def _fmt_prem(pay, rec):
        p = f'{pay:.2f}' if pd.notna(pay) else '--'
        r = f'{rec:.2f}' if pd.notna(rec) else '--'
        return f'{p} | {r}'

    pay_vol_bid_adj_col = 'pay_vol_bid_at_ref_pct'
    pay_vol_ask_adj_col = 'pay_vol_ask_at_ref_pct'
    rec_vol_bid_adj_col = 'rec_vol_bid_at_ref_pct'
    rec_vol_ask_adj_col = 'rec_vol_ask_at_ref_pct'
    use_adj_vols = grid is not None and pd.notna(live_spot_bps) and (not is_price or pd.notna(common_spot_px))
    if use_adj_vols:
        common_spot_dec = float(live_spot_bps) / 1e4
        adj_vols = compute_dealer_vols_at_common_spot(
            latest, grid, product, is_price,
            common_spot_dec,
            common_spot_px=common_spot_px if is_price else None,
            common_spot_bps=live_spot_bps,
        )
        if not adj_vols.empty:
            adj_vols = adj_vols.rename(columns={
                'pay_vol_bid_at_ref': pay_vol_bid_adj_col,
                'pay_vol_ask_at_ref': pay_vol_ask_adj_col,
                'rec_vol_bid_at_ref': rec_vol_bid_adj_col,
                'rec_vol_ask_at_ref': rec_vol_ask_adj_col,
            })
            for c in (pay_vol_bid_adj_col, pay_vol_ask_adj_col, rec_vol_bid_adj_col, rec_vol_ask_adj_col):
                adj_vols[c] = adj_vols[c] * 100.0
            latest = latest.merge(
                adj_vols[['sender_bank', 'expiry', 'strike_display',
                          pay_vol_bid_adj_col, pay_vol_ask_adj_col,
                          rec_vol_bid_adj_col, rec_vol_ask_adj_col]],
                on=['sender_bank', 'expiry', 'strike_display'], how='left',
            )
            logging.info(
                f'[option_ladder] adj-vols at common spot bps={live_spot_bps:.1f}: '
                f'pay_bid={latest[pay_vol_bid_adj_col].notna().sum()}, '
                f'pay_ask={latest[pay_vol_ask_adj_col].notna().sum()}, '
                f'rec_bid={latest[rec_vol_bid_adj_col].notna().sum()}, '
                f'rec_ask={latest[rec_vol_ask_adj_col].notna().sum()}'
            )

    pay_vol_bid_disp = pay_vol_bid_adj_col if pay_vol_bid_adj_col in latest.columns else pay_vol_bid_col
    pay_vol_ask_disp = pay_vol_ask_adj_col if pay_vol_ask_adj_col in latest.columns else pay_vol_ask_col
    rec_vol_bid_disp = rec_vol_bid_adj_col if rec_vol_bid_adj_col in latest.columns else rec_vol_bid_col
    rec_vol_ask_disp = rec_vol_ask_adj_col if rec_vol_ask_adj_col in latest.columns else rec_vol_ask_col
    otm_vol_bid_disp = pay_vol_bid_disp if payer_otm else rec_vol_bid_disp
    otm_vol_ask_disp = pay_vol_ask_disp if payer_otm else rec_vol_ask_disp

    def _delta_adjust(raw_prem, dealer_spot, delta_val, rpv01):
        return delta_adjust_to_spot(
            raw_prem, dealer_spot,
            common_spot_px if is_price else None,
            delta_val, rpv01, is_price,
            common_spot_bps=live_spot_bps,
        )

    spot_bps = np.nan
    for idx in reversed(latest.index.tolist()):
        spot_dec = _dealer_spot_dec(latest.loc[idx])
        if pd.notna(spot_dec):
            spot_bps = spot_dec * 1e4
            break

    use_live_spot = live_spot_bps if pd.notna(live_spot_bps) else spot_bps

    bid_keep = [c for c in ['sender_bank', 'asof_date_time', 'payer_bid', 'receiver_bid',
                             'payer_delta', 'receiver_delta', 'risky_pv01_spot_calc',
                             'spot', 'spread_spot', 'par_spread_spot_calc',
                             pay_vol_bid_col, rec_vol_bid_col, otm_vol_bid_col,
                             pay_vol_bid_adj_col, rec_vol_bid_adj_col, otm_vol_bid_disp,
                             'expiry', 'strike_display'] if c in latest.columns]
    bid_keep = list(dict.fromkeys(bid_keep))
    bids = latest[bid_keep].copy()
    if otm_vol_bid_disp in bids.columns:
        bids = bids.dropna(subset=[otm_vol_bid_disp])
    else:
        bids = bids.iloc[:0]
    if not bids.empty:
        bids = bids.sort_values(otm_vol_bid_disp, ascending=False).reset_index(drop=True)
    bids['asof_date_time'] = pd.to_datetime(bids['asof_date_time']).dt.strftime('%H:%M')
    bids['_bid_cum'] = range(1, len(bids) + 1)

    can_adjust = grid is not None and pd.notna(live_spot_bps) and len(bids) > 0
    if can_adjust:
        spot_col_for_adj = 'spot' if is_price else 'par_spread_spot_calc'
        rpv01_col = 'risky_pv01_spot_calc' if 'risky_pv01_spot_calc' in bids.columns else None
        dealer_spots = bids[spot_col_for_adj] if spot_col_for_adj in bids.columns else pd.Series([np.nan] * len(bids))
        rpv01s = bids[rpv01_col] if rpv01_col else pd.Series([1.0] * len(bids))
        pay_deltas = bids['payer_delta'] if 'payer_delta' in bids.columns else pd.Series([np.nan] * len(bids))
        rec_deltas = bids['receiver_delta'] if 'receiver_delta' in bids.columns else pd.Series([np.nan] * len(bids))
        raw_payer = bids['payer_bid'] if 'payer_bid' in bids.columns else pd.Series([np.nan] * len(bids))
        raw_receiver = bids['receiver_bid'] if 'receiver_bid' in bids.columns else pd.Series([np.nan] * len(bids))
        pb = pd.Series([_delta_adjust(p, s, d, r) for p, s, d, r in zip(raw_payer, dealer_spots, pay_deltas, rpv01s)])
        rb = pd.Series([_delta_adjust(p, s, d, r) for p, s, d, r in zip(raw_receiver, dealer_spots, rec_deltas, rpv01s)])
        logging.info(f'[option_ladder] delta-adj {pb.notna().sum()}/{len(pb)} payer_bid + {rb.notna().sum()}/{len(rb)} receiver_bid at common spot={live_spot_bps:.1f}')
    else:
        pb = bids.get('payer_bid', pd.Series(dtype=float))
        rb = bids.get('receiver_bid', pd.Series(dtype=float))
    bids['B Prem'] = [_fmt_prem(p, r) for p, r in zip(pb, rb)]
    fwd_for_ann = live_fwd_bps if pd.notna(live_fwd_bps) and live_fwd_bps > 0 else use_live_spot
    if pd.notna(fwd_for_ann) and fwd_for_ann > 0 and otm_vol_bid_disp in bids.columns:
        bids['B Vol Ann'] = (bids[otm_vol_bid_disp] / 100 * fwd_for_ann).round(1)
    bids = bids.rename(columns={
        'asof_date_time': 'B Time', '_bid_cum': 'B#', 'sender_bank': 'B Dlr',
        otm_vol_bid_disp: 'B Vol',
    })

    ask_keep = [c for c in ['sender_bank', 'asof_date_time', 'payer_ask', 'receiver_ask',
                             'payer_delta', 'receiver_delta', 'risky_pv01_spot_calc',
                             'spot', 'spread_spot', 'par_spread_spot_calc',
                             pay_vol_ask_col, rec_vol_ask_col, otm_vol_ask_col,
                             pay_vol_ask_adj_col, rec_vol_ask_adj_col, otm_vol_ask_disp,
                             'expiry', 'strike_display'] if c in latest.columns]
    ask_keep = list(dict.fromkeys(ask_keep))
    asks = latest[ask_keep].copy()
    if otm_vol_ask_disp in asks.columns:
        asks = asks.dropna(subset=[otm_vol_ask_disp])
    else:
        asks = asks.iloc[:0]
    if not asks.empty:
        asks = asks.sort_values(otm_vol_ask_disp, ascending=True).reset_index(drop=True)
    asks['asof_date_time'] = pd.to_datetime(asks['asof_date_time']).dt.strftime('%H:%M')
    asks['_ask_cum'] = range(1, len(asks) + 1)

    can_adjust_asks = grid is not None and pd.notna(live_spot_bps) and len(asks) > 0
    if can_adjust_asks:
        spot_col_for_adj = 'spot' if is_price else 'par_spread_spot_calc'
        rpv01_col = 'risky_pv01_spot_calc' if 'risky_pv01_spot_calc' in asks.columns else None
        dealer_spots = asks[spot_col_for_adj] if spot_col_for_adj in asks.columns else pd.Series([np.nan] * len(asks))
        rpv01s = asks[rpv01_col] if rpv01_col else pd.Series([1.0] * len(asks))
        pay_deltas = asks['payer_delta'] if 'payer_delta' in asks.columns else pd.Series([np.nan] * len(asks))
        rec_deltas = asks['receiver_delta'] if 'receiver_delta' in asks.columns else pd.Series([np.nan] * len(asks))
        raw_payer = asks['payer_ask'] if 'payer_ask' in asks.columns else pd.Series([np.nan] * len(asks))
        raw_receiver = asks['receiver_ask'] if 'receiver_ask' in asks.columns else pd.Series([np.nan] * len(asks))
        pa = pd.Series([_delta_adjust(p, s, d, r) for p, s, d, r in zip(raw_payer, dealer_spots, pay_deltas, rpv01s)])
        ra = pd.Series([_delta_adjust(p, s, d, r) for p, s, d, r in zip(raw_receiver, dealer_spots, rec_deltas, rpv01s)])
        logging.info(f'[option_ladder] delta-adj {pa.notna().sum()}/{len(pa)} payer_ask + {ra.notna().sum()}/{len(ra)} receiver_ask at common spot={live_spot_bps:.1f}')
    else:
        pa = asks.get('payer_ask', pd.Series(dtype=float))
        ra = asks.get('receiver_ask', pd.Series(dtype=float))
    asks['A Prem'] = [_fmt_prem(p, r) for p, r in zip(pa, ra)]
    if pd.notna(fwd_for_ann) and fwd_for_ann > 0 and otm_vol_ask_disp in asks.columns:
        asks['A Vol Ann'] = (asks[otm_vol_ask_disp] / 100 * fwd_for_ann).round(1)
    asks = asks.rename(columns={
        'asof_date_time': 'A Time', '_ask_cum': 'A#', 'sender_bank': 'A Dlr',
        otm_vol_ask_disp: 'A Vol',
    })

    n = max(len(bids), len(asks))
    bids = bids.reindex(range(n))
    asks = asks.reindex(range(n))

    bid_cols = ['B Time', 'B#', 'B Dlr', 'B Prem', 'B Vol Ann']
    ask_cols = ['A Vol Ann', 'A Prem', 'A Dlr', 'A#', 'A Time']
    bid_cols = [c for c in bid_cols if c in bids.columns]
    ask_cols = [c for c in ask_cols if c in asks.columns]

    ladder = pd.concat([bids[bid_cols], asks[ask_cols]], axis=1)

    for c in ['B Vol Ann', 'A Vol Ann']:
        if c in ladder.columns:
            ladder[c] = ladder[c].round(1)
    for c in ['B#', 'A#']:
        if c in ladder.columns:
            ladder[c] = ladder[c].astype('Int64')

    return ladder


def build_rv_figure(rv_data, title='Relative Vol', series_labels=None):
    if rv_data is None or rv_data.empty:
        return _finish_chart(go.Figure().update_layout(annotations=[dict(
            text='No volatility history for the selected products, tenors and strikes/deltas.',
            x=.5, y=.5, xref='paper', yref='paper', showarrow=False,
        )]), title)

    fig = go.Figure()
    columns = [c for c in ('vol_mid_ann', 'vol_mid_ann_1', 'vol_mid_ann_2', 'vol_mid_ann_diff')
               if c in rv_data.columns]
    for col in columns:
        label = (series_labels or {}).get(col, {'vol_mid_ann': 'Normal vol',
                  'vol_mid_ann_1': 'Leg 1', 'vol_mid_ann_2': 'Leg 2',
                  'vol_mid_ann_diff': 'Difference (1 − 2)'}[col])
        fig.add_trace(go.Scatter(
            x=rv_data.index, y=rv_data[col], mode='lines', name=label,
            line=dict(width=2, dash='dot' if col.endswith('_diff') else 'solid'),
            hovertemplate='%{x|%d %b %Y %H:%M}<br>%{y:,.2f}<extra>%{fullData.name}</extra>',
        ))

    fig.update_yaxes(title_text='Normal vol (bps, annualized)', tickformat=',.1f')
    return _finish_chart(fig, title, timeseries=True)
