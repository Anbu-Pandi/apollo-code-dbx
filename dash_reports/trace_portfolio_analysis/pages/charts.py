import plotly.graph_objects as go
from plotly.subplots import make_subplots
import plotly.express as px
import numpy as np

import dash_reports.trace_portfolio_analysis.data.portfolio_trades as data


def get_net_dv01_chart(df):
    bar_chart_df = df.groupby(['effective_date', 'aggregate_level'], dropna=False).agg({'apo_dv01': 'sum'})
    bar_chart_df = bar_chart_df.div(1e6)
    bar_chart_df = bar_chart_df.unstack(['aggregate_level'])
    bar_chart_df.columns = bar_chart_df.columns.to_flat_index()

    bar_chart_df[('apo_dv01', 'Net')] = bar_chart_df.sum(axis=1)
    bar_chart_df[('apo_dv01', '21d_rolling_cum_sum')] = bar_chart_df[('apo_dv01', 'Net')].rolling(window=21).sum()

    fig = make_subplots(specs=[[{"secondary_y": True}]])

    fig.add_trace(go.Bar(
        x=bar_chart_df.index,
        y=bar_chart_df[('apo_dv01', 'HY')],
        name='HY',
        marker_color='orange',
        hoverinfo='y',
        hovertemplate='HY: %{y:.2f}<extra></extra>'
    ))

    fig.add_trace(go.Bar(
        x=bar_chart_df.index,
        y=bar_chart_df[('apo_dv01', 'IG')],
        name='IG',
        marker_color='green',
        hoverinfo='y',
        hovertemplate='IG: %{y:.2f}<extra></extra>'
    ))

    fig.add_trace(go.Bar(
        x=bar_chart_df.index,
        y=bar_chart_df[('apo_dv01', 'other')],
        name='Other',
        marker_color='grey',
        hoverinfo='y',
        hovertemplate='Others: %{y:.2f}<extra></extra>'
    ))

    fig.add_trace(go.Scatter(
        x=bar_chart_df.index,
        y=bar_chart_df[('apo_dv01', 'Net')],
        mode='markers',
        name='Net',
        marker=dict(color='black', symbol='line-ew-open'),
        line=dict(width=10),
        hoverinfo='y',
        hovertemplate='Net: %{y:.2f}<extra></extra>'
    ))

    fig.add_trace(go.Scatter(
        x=bar_chart_df.index,
        y=bar_chart_df[('apo_dv01', '21d_rolling_cum_sum')],
        mode='lines',
        name='21d_rolling_net_cum_sum',
        line=dict(color='blue', width=2),
        hoverinfo='y',
        hovertemplate='21d_rolling_cum_sum: %{y:.2f}<extra></extra>'
    ), secondary_y=True)

    fig.update_layout({'plot_bgcolor': 'rgba(0, 0, 0, 0)'},
                      title=f"HY / IG DV01",
                      xaxis_title='Date',
                      showlegend=True,
                      hovermode='x',
                      width=1800,
                      height=500,
                      barmode='relative',
                      yaxis=dict(
                          title='DV01 (in millions)',
                          zeroline=False,
                          tickformat=',',
                          showgrid=False
                      ),
                      yaxis2=dict(
                          title='21d_rolling_net_cum_sum',
                          overlaying='y',
                          side='right',
                          zeroline=False,
                          showgrid=False,
                          tickformat=','
                      )
                      )

    return fig


def get_hy_dv01_chart(df):
    bar_chart_df = df.groupby(['effective_date', 'aggregate_level'], dropna=False).agg({'apo_dv01': 'sum'})
    bar_chart_df = bar_chart_df.div(1e6)
    bar_chart_df = bar_chart_df.unstack(['aggregate_level'])
    bar_chart_df.columns = bar_chart_df.columns.to_flat_index()

    bar_chart_df[('apo_dv01', 'Net')] = bar_chart_df.sum(axis=1)
    bar_chart_df[('apo_dv01', '21d_rolling_cum_sum')] = bar_chart_df[('apo_dv01', 'Net')].rolling(window=21).sum()

    fig = make_subplots(specs=[[{"secondary_y": True}]])

    fig.add_trace(go.Bar(
        x=bar_chart_df.index,
        y=bar_chart_df[('apo_dv01', 'HY')],
        name='HY',
        marker_color='orange',
        hoverinfo='y',
        hovertemplate='HY: %{y:.2f}<extra></extra>'
    ))

    fig.add_trace(go.Scatter(
        x=bar_chart_df.index,
        y=bar_chart_df[('apo_dv01', '21d_rolling_cum_sum')],
        mode='lines',
        name='21d_rolling_net_cum_sum',
        line=dict(color='blue', width=2),
        hoverinfo='y',
        hovertemplate='21d_rolling_cum_sum: %{y:.2f}<extra></extra>'
    ), secondary_y=True)

    fig.update_layout({'plot_bgcolor': 'rgba(0, 0, 0, 0)'},
                      title=f"HY DV01",
                      xaxis_title='Date',
                      showlegend=True,
                      hovermode='x',
                      width=1800,
                      height=500,
                      barmode='relative',
                      yaxis=dict(
                          title='DV01 (in millions)',
                          zeroline=False,
                          tickformat=',',
                          showgrid=False
                      ),
                      yaxis2=dict(
                          title='21d_rolling_net_cum_sum',
                          overlaying='y',
                          side='right',
                          zeroline=False,
                          showgrid=False,
                          tickformat=','
                      )
                      )

    return fig


def get_ig_dv01_chart(df):
    bar_chart_df = df.groupby(['effective_date', 'aggregate_level'], dropna=False).agg({'apo_dv01': 'sum'})
    bar_chart_df = bar_chart_df.div(1e6)
    bar_chart_df = bar_chart_df.unstack(['aggregate_level'])
    bar_chart_df.columns = bar_chart_df.columns.to_flat_index()

    bar_chart_df[('apo_dv01', 'Net')] = bar_chart_df.sum(axis=1)
    bar_chart_df[('apo_dv01', '21d_rolling_cum_sum')] = bar_chart_df[('apo_dv01', 'Net')].rolling(window=21).sum()

    fig = make_subplots(specs=[[{"secondary_y": True}]])

    fig.add_trace(go.Bar(
        x=bar_chart_df.index,
        y=bar_chart_df[('apo_dv01', 'IG')],
        name='IG',
        marker_color='green',
        hoverinfo='y',
        hovertemplate='HY: %{y:.2f}<extra></extra>'
    ))

    fig.add_trace(go.Scatter(
        x=bar_chart_df.index,
        y=bar_chart_df[('apo_dv01', '21d_rolling_cum_sum')],
        mode='lines',
        name='21d_rolling_net_cum_sum',
        line=dict(color='blue', width=2),
        hoverinfo='y',
        hovertemplate='21d_rolling_cum_sum: %{y:.2f}<extra></extra>'
    ), secondary_y=True)

    fig.update_layout({'plot_bgcolor': 'rgba(0, 0, 0, 0)'},
                      title=f"IG DV01",
                      xaxis_title='Date',
                      showlegend=True,
                      hovermode='x',
                      width=1800,
                      height=500,
                      barmode='relative',
                      yaxis=dict(
                          title='DV01 (in millions)',
                          zeroline=False,
                          tickformat=',',
                          showgrid=False
                      ),
                      yaxis2=dict(
                          title='21d_rolling_net_cum_sum',
                          overlaying='y',
                          side='right',
                          zeroline=False,
                          showgrid=False,
                          tickformat=','
                      )
                      )

    return fig


def get_ice_index_oas_yield_chart(df, title, width=900):
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    df = df.sort_values('asof_date')
    df = df.set_index('asof_date')

    for col in df.columns:
        secondary_axis = False
        if 'oas' in col:
            secondary_axis = True
        fig.add_trace(
            go.Scatter(x=df.index, y=df[col].values, name=col),
            secondary_y=secondary_axis)

    fig.update_layout({'plot_bgcolor': 'rgba(0, 0, 0, 0)'},
                      title=title,
                      xaxis_title='Date',
                      yaxis_title='Yield (in %)',
                      yaxis2_title='OAS (in bps)',
                      showlegend=True,
                      hovermode='x',
                      width=width,
                      height=500,
                      )

    return fig


def get_metric_scatter_plot(df, title, metric='apo_dv01'):
    df = df.dropna(subset=['spread_bins', 'duration_bins'], how='any')

    df_aggregated = df.groupby(['spread_bins', 'duration_bins'], dropna=False, as_index=False).agg({metric: 'sum'})

    df_aggregated[metric] = df_aggregated[metric].fillna(0).div(1e6)

    fig = px.scatter(df_aggregated, x='duration_bins', y='spread_bins', color=metric,
                     labels={'duration_bins': 'Duration (in yrs)', 'spread_bins': 'Spread (in bps)'},
                     title=title,
                     custom_data=[metric],
                     )

    color_scale_min = df_aggregated[metric].min()
    color_scale_max = df_aggregated[metric].max()

    fig.update_traces(marker=dict(symbol='circle', line=dict(color='black', width=1), opacity=0.7),
                      selector=dict(type='scatter', mode='markers'),
                      hovertemplate=
                      "<b>Value (in millions):</b> %{customdata[0]:,.4f}<br>" +
                      "<extra></extra>"
                      )

    fig.update_yaxes(categoryorder='array', categoryarray=data.spread_bins_labels)
    fig.update_xaxes(categoryorder='array', categoryarray=data.duration_bins_labels)

    color_tick_vals = np.linspace(color_scale_min, color_scale_max, 10)

    fig.update_layout({'plot_bgcolor': 'rgba(0, 0, 0, 0)'},
                      xaxis_title='Duration',
                      yaxis_title='OAS (in bps)',
                      showlegend=True,
                      height=600,
                      width=800,
                      coloraxis_colorbar=dict(
                          title=f'{data.col_rename_dict[metric]} (in millions)',
                          tickvals=color_tick_vals,
                          ticktext=[f'{v:,.0f}' for v in color_tick_vals],
                          thickness=15,
                          len=0.75,
                          xanchor='left',
                          yanchor='middle',
                          title_side='right'

                      )
                      )

    return fig


def get_metric_rating_duration_scatter_plot(df, title, metric='apo_dv01'):
    rating_label = data.rating_label.copy()

    rating_label.reverse()

    df = df.dropna(subset=['apo_warf_rating', 'duration_bins'], how='any')

    df['duration_bins'] = df['duration_bins'].astype(str)

    df_aggregated = df.groupby(['apo_warf_rating', 'duration_bins'], dropna=False, as_index=False).agg(
        {metric: 'sum'})

    df_aggregated[metric] = df_aggregated[metric].fillna(0).div(1e6)

    fig = px.scatter(df_aggregated, x='duration_bins', y='apo_warf_rating', color=metric,
                     labels={'duration_bins': 'Duration (in yrs)', 'apo_warf_rating': 'Ratings'},
                     title=title,
                     custom_data=[metric]
                     )

    color_scale_min = df_aggregated[metric].min()
    color_scale_max = df_aggregated[metric].max()

    fig.update_traces(marker=dict(symbol='circle', line=dict(color='black', width=1), opacity=0.7),
                      selector=dict(type='scatter', mode='markers'),
                      hovertemplate=
                      "<b>Value (in millions):</b> %{customdata[0]:,.4f}<br>" +
                      "<extra></extra>"
                      )

    fig.add_trace(go.Scatter(x=data.duration_bins_labels, y=['Ba1'] * len(data.duration_bins_labels), mode='lines',
                             line=dict(color='red', width=2, dash='dash'),
                             showlegend=False
                             ))

    fig.update_xaxes(categoryorder='array', categoryarray=data.duration_bins_labels)
    fig.update_yaxes(categoryorder='array', categoryarray=rating_label)

    color_tick_vals = np.linspace(color_scale_min, color_scale_max, 10)
    fig.update_layout({'plot_bgcolor': 'rgba(0, 0, 0, 0)'},
                      xaxis_title='Duration',
                      yaxis_title='Rating',
                      showlegend=True,
                      height=600,
                      width=800,
                      coloraxis_colorbar=dict(
                          title=f'{data.col_rename_dict[metric]} (in millions)',
                          tickvals=color_tick_vals,
                          ticktext=[f'{v:,.0f}' for v in color_tick_vals],
                          thickness=15,
                          len=0.75,
                          xanchor='left',
                          yanchor='middle',
                          title_side='right'

                      )
                      )

    return fig
