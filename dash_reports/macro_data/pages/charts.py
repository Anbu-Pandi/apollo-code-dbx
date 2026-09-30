import plotly.graph_objects as go
from plotly.subplots import make_subplots

COLOR_DICT = {'mkt_px': 'blue', 'conv_spd_bps': 'blue', 'model_px': 'red', 'model_spd_bps': 'red',
              'prev_mkt_px': 'purple', 'prev_conv_spd_bps': 'purple', 'prev_model_px': 'red'}


def _chart_price_spread(df, cols_to_plot, x_axis_col, title):
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    hover_template = '%{hovertext}: %{y:.2f}'
    for col in cols_to_plot:
        if 'skew' in col:
            fig.add_trace(
                go.Bar(x=df[x_axis_col], y=df[col].values, name=col, opacity=0.5 if 'prev' in col else 0.7,
                       marker=dict(color='grey' if 'prev' in col else 'green'), hovertext=df['hover_text'],
                       hovertemplate=hover_template),
                secondary_y=True)
        else:
            fig.add_trace(go.Scatter(x=df[x_axis_col], y=df[col].values, name=col, marker=dict(color=COLOR_DICT[col]),
                                     line=dict(width=2, dash='dash' if 'prev' in col else 'solid'),
                                     hovertext=df['hover_text'], hovertemplate=hover_template
                                     )
                          )

    yaxis_title = 'Price' if 'Price' in title else 'Spread'

    fig.update_layout({'plot_bgcolor': 'rgba(0, 0, 0, 0)'},
                      width=900,
                      height=500,
                      xaxis=dict(tickformat="%e %b %Y", tickvals=df[x_axis_col],
                                 ticktext=df[x_axis_col].dt.strftime('%b %Y'), tickangle=270),
                      barmode='overlay',
                      title=title,
                      hovermode='x',
                      yaxis_title=f"{yaxis_title}",
                      yaxis2_title=f'{yaxis_title} Skew',
                      xaxis_title='Maturity'
                      )
    return fig


def _chart_volume(df, cols_to_plot, x_axis_col, title):
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    hover_template = '%{hovertext}: %{y}'
    for col in cols_to_plot:
        if col == 'daily_gross_notional':
            fig.add_trace(
                go.Bar(x=df[x_axis_col], y=df[col].values, opacity=0.5, marker=dict(color='orange'), name=col,
                       hovertext=df['hover_text'], hovertemplate=hover_template),
            )
        elif col == f'5d_avg_daily_gross_notional':
            fig.add_trace(go.Scatter(x=df[x_axis_col], y=df[col].values, name=col, mode='markers',
                                     marker=dict(color='black', size=50, symbol='line-ew-open'),
                                     line=dict(width=5),
                                     hovertext=df['hover_text'],
                                     hovertemplate=hover_template
                                     ),
                          )
        elif col == 'default_probability':
            fig.add_trace(go.Scatter(x=df[x_axis_col], y=df[col].values, name=col, mode='markers',
                                     marker=dict(color='red', size=10),
                                     # line=dict(width=5),
                                     hovertext=df['hover_text'],
                                     hovertemplate=hover_template
                                     ), secondary_y=True)

    fig.update_layout({'plot_bgcolor': 'rgba(0, 0, 0, 0)'},
                      width=1800,
                      height=500,
                      xaxis=dict(tickformat="%e %b %Y", tickvals=df[x_axis_col],
                                 ticktext=df[x_axis_col].dt.strftime('%b %Y'), tickangle=270),
                      barmode='overlay',
                      title=title,
                      hovermode='x',
                      yaxis_title='Gross Notional',
                      yaxis2_title=f'Default Probability (%)',
                      xaxis_title='Maturity'
                      )

    return fig


def generate_price_spread_trnxn_chart(df, price_plot_cols, spread_plot_cols, vol_plot_cols, x_axis_col, idx_name):
    df['hover_text'] = df['idx_short_name'] + df['series'].astype(str)

    price_fig = _chart_price_spread(df, price_plot_cols, x_axis_col, title=f'{idx_name} Price')
    spread_fig = _chart_price_spread(df, spread_plot_cols, x_axis_col, title=f'{idx_name} Spread')
    vol_fig = _chart_volume(df, vol_plot_cols, x_axis_col, title=f'{idx_name} Volume')

    chart_dict = {'price_spread': [price_fig, spread_fig],
                  'volume': [vol_fig]}

    return chart_dict


def generate_sprd_vol_ts_chart(df, ticker, cols_to_plot, title):
    required_cols = ['asof_date', 'index_ticker', 'idx_short_name', 'series'] + cols_to_plot
    chart_df = df.loc[df['idx_short_name'] == ticker, required_cols]
    chart_df['hover_text'] = df['idx_short_name'] + df['series'].astype(str)

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        vertical_spacing=0.05, subplot_titles=('Spread TS', 'Volume TS'),
                        row_width=[0.2, 0.7],
                        specs=[[{"secondary_y": True}], [{"secondary_y": False}]])
    hover_template = '%{hovertext}: %{y:.2f}'

    for col in cols_to_plot:
        if col != 'daily_gross_notional':
            fig.add_trace(
                go.Scatter(x=chart_df['asof_date'], y=chart_df[col].values, name=col,
                           # marker=dict(color=COLOR_DICT[col]),
                           line=dict(width=2),
                           hovertext=chart_df['hover_text'], hovertemplate=hover_template),
                secondary_y=True if 'skew' in col else False,
                row=1, col=1
            )

    chart_df['daily_gross_notional'] = chart_df['daily_gross_notional'].div(1e6)

    fig.add_trace(
        go.Bar(x=chart_df['asof_date'], y=chart_df['daily_gross_notional'].values, name='daily_gross_notional',
               showlegend=True, hovertext=chart_df['hover_text'], hovertemplate='%{hovertext}: %{y:.2f} mm'),
        row=2, col=1)

    fig.update_layout({'plot_bgcolor': 'rgba(0, 0, 0, 0)'},
                      width=1800,
                      height=500,
                      xaxis=dict(tickformat="%e %b %Y", tickangle=270),
                      title=title,
                      hovermode='x'
                      )
    fig.update_yaxes(title_text="Spread (bps)", row=1, col=1)
    fig.update_yaxes(title_text="Spread Skew (bps)", row=1, col=1, secondary_y=True)
    fig.update_yaxes(title_text="Volume (millions)", row=2, col=1)
    fig.update_xaxes(title_text="Date", row=2, col=1)

    return [fig]


def generate_cdx_ex_default_charts(df, title):
    chart_df = df.pivot_table(index='asof_date', columns='apo_ex_default_ticker', values='apo_implied_spd_bps')

    fig = make_subplots(specs=[[{'secondary_y': True}]])
    for col in chart_df.columns:
        if '_0' not in col:
            trace = go.Scatter(x=chart_df.index, y=chart_df[col], mode='lines',
                               name=col)  # , fill='tonexty', fillcolor='rgba(0, 176, 246, 0.2)')
            fig.add_trace(trace, secondary_y=False)

    index_spread_df = df[['asof_date', 'mkt_conv_spd_bps']].dropna().drop_duplicates('asof_date').set_index('asof_date')
    index_spread_df.sort_index(inplace=True)

    for col in index_spread_df.columns:
        fig.add_trace(go.Scatter(x=index_spread_df.index, y=index_spread_df[col], mode='lines', name=col),
                      secondary_y=False)

    fig.update_layout({'plot_bgcolor': 'rgba(0, 0, 0, 0)'},
                      title=f'{title} Ex Default Implied Spread',
                      xaxis_title='Date',
                      yaxis_title='Implied Spread (bps)',
                      yaxis=dict(range=[chart_df.min().min(), chart_df.max().max() + 50]),  # dtick=20
                      width=1800,
                      height=500,
                      xaxis=dict(tickformat="%e %b %Y", tickangle=270),
                      showlegend=True)

    return fig
