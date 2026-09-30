import plotly.graph_objects as go


def _layout(fig, title, width=1100, height=420):
    fig.update_layout(
        title=title, width=width, height=height,
        plot_bgcolor='rgba(0,0,0,0)', hovermode='x unified',
        legend=dict(orientation='h', y=1.12),
    )
    return fig


def overview_basis_ts(hist_df, summary_df):
    fig = go.Figure()
    if hist_df is not None and not hist_df.empty:
        for label, g in hist_df.sort_values('asof_date').groupby('label'):
            fig.add_trace(go.Scatter(
                x=g['asof_date'], y=g['ew_basis_bps'], mode='lines', name=label,
            ))
    fig.update_yaxes(title_text='EW cash-CDS basis (bps)')
    fig.add_hline(y=0, line=dict(color='#888', dash='dot'))
    return _layout(fig, 'Cash-CDS basis history — equal-weight of constituents (CDS spread − bond Z-spread)')


def index_basis_ts(hist_df, label):
    fig = go.Figure()
    if hist_df is not None and not hist_df.empty:
        g = hist_df.sort_values('asof_date')
        fig.add_trace(go.Scatter(x=g['asof_date'], y=g['ew_basis_bps'],
                                 name='EW basis (bps)', mode='lines'))
        if 'n' in g.columns:
            fig.add_trace(go.Scatter(x=g['asof_date'], y=g['n'],
                                     name='# names', mode='lines', yaxis='y2',
                                     line=dict(color='#bbb', dash='dot'), opacity=0.6))
            fig.update_layout(yaxis2=dict(title='# constituents', overlaying='y', side='right', showgrid=False))
    fig.update_yaxes(title_text='Cash-CDS basis (bps)')
    fig.add_hline(y=0, line=dict(color='#888', dash='dot'))
    return _layout(fig, f'{label} — 5Y cash-CDS basis history (EW of constituents)')


def sector_bar(sector_df, label):
    fig = go.Figure()
    if sector_df is None or sector_df.empty:
        return _layout(fig, f'{label} — sector basis (no data)')
    fig.add_trace(go.Bar(x=sector_df['sector'], y=sector_df['basis_bps'], name='EW basis'))
    fig.update_layout(xaxis_tickangle=-45, yaxis_title='Basis (bps)')
    return _layout(fig, f'{label} — equal-weight basis by sector', height=480)


def names_scatter(names_df, label):
    fig = go.Figure()
    if names_df is None or names_df.empty:
        return _layout(fig, f'{label} — basis vs CDS (no data)')
    d = names_df.dropna(subset=['basis_bps', 'cds_spread_bps'])
    fig.add_trace(go.Scatter(
        x=d['cds_spread_bps'], y=d['basis_bps'], mode='markers+text', text=d['name'],
        textposition='top center', textfont=dict(size=8), marker=dict(size=8),
    ))
    fig.update_layout(xaxis_title='CDS spread (bps)', yaxis_title='Cash-CDS basis (bps)')
    return _layout(fig, f'{label} — single-name basis vs CDS spread', height=520)
