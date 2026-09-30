import pandas as pd
from common.utils import dtutil
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from dash import dcc
from time import time_ns
import plotly.express as px
from common.utils.creditutil import vec_calc_tenor

import numpy as np
import re


def parse_rgb(color_str):
    """
    Convert an RGB string ('rgb(r, g, b)') to a tuple of normalized R, G, B values (between 0 and 1).
    """
    r, g, b = map(int, re.match(r'rgb\((\d+),\s*(\d+),\s*(\d+)\)', color_str).groups())
    return r / 255, g / 255, b / 255


def interpolate_colors(colors, n_colors):
    """
    Interpolate between the provided colors to create a larger color palette.

    Args:
    - colors (list): A list of RGB color strings.
    - n_colors (int): The total number of colors needed.

    Returns:
    - list: A list of interpolated color strings in rgb format.
    """

    # Parse RGB strings to RGB values
    colors_rgb = [parse_rgb(color) for color in colors]

    # Interpolate
    r = np.interp(np.linspace(0, 1, n_colors), np.linspace(0, 1, len(colors_rgb)), [color[0] for color in colors_rgb])
    g = np.interp(np.linspace(0, 1, n_colors), np.linspace(0, 1, len(colors_rgb)), [color[1] for color in colors_rgb])
    b = np.interp(np.linspace(0, 1, n_colors), np.linspace(0, 1, len(colors_rgb)), [color[2] for color in colors_rgb])

    # Convert RGB values to RGB strings
    interpolated_colors = [f"rgb({int(r[i] * 255)}, {int(g[i] * 255)}, {int(b[i] * 255)})" for i in range(n_colors)]

    return interpolated_colors


def create_estimate_chart(df, fe_item):
    if len(df) == 0:
        return dcc.Graph()

    df['CONS_START_DATE'] = pd.to_datetime(df['CONS_START_DATE'])
    df['FE_FP_END'] = pd.to_datetime(df['FE_FP_END'])
    df['CONS_END_DATE'] = pd.to_datetime(df['CONS_END_DATE'])

    rows_with_null_end_date = df[df['CONS_END_DATE'].isna()].copy()
    rows_with_null_end_date['CONS_START_DATE'] = rows_with_null_end_date['FE_FP_END']

    df_with_null_end_date = pd.concat([df, rows_with_null_end_date], ignore_index=True)
    ex_df = df_with_null_end_date.copy()

    unique_prediction_dates = ex_df['FE_FP_END'].unique()

    colors = interpolate_colors(px.colors.sequential.Greens, len(unique_prediction_dates))
    max_colors = len(colors)

    subplot_titles = (
        f"Time Series {fe_item} Estimates vs Actual ({df['FE_FP_END'].dt.date.min()} Onwards)",
        'Difference Between Actual and Most Recent Prediction & Number of Estimates'
    )

    fig = make_subplots(rows=2, cols=1, subplot_titles=subplot_titles, vertical_spacing=0.05, row_heights=[0.75, 0.25],
                        shared_xaxes=True, specs=[[{}], [{"secondary_y": True}]])

    for idx, (prediction_date, group_data) in enumerate(ex_df.groupby('FE_FP_END')):
        prediction_date = dtutil.todate(prediction_date)
        forecasted_time = vec_calc_tenor(pd.to_datetime(group_data['FE_FP_END'], errors='coerce'),
                                         pd.to_datetime(group_data['CONS_START_DATE'], errors='coerce'))
        group_data = group_data[forecasted_time <= 2.0]
        if (dtutil.dateadd(dtutil.today(), '-2y-3m') <= prediction_date) and \
                (prediction_date <= dtutil.dateadd(dtutil.today(), '1y3m')):
            fig.add_trace(go.Scatter(x=group_data['CONS_START_DATE'],
                                     y=group_data['FE_MEAN'],
                                     mode='lines',
                                     marker=dict(color=colors[min(idx, max_colors - 1)]),
                                     name=f'{prediction_date}'),
                          row=1, col=1)

    fig.add_trace(go.Scatter(x=ex_df['FE_FP_END'],
                             y=ex_df['ACTUAL_VALUE'],
                             mode='markers',
                             marker=dict(color='red', size=10),
                             name='Actual'),
                  row=1, col=1)

    extended_sales_data_with_null_end_date_filter = \
        ex_df[(ex_df['FE_FP_END'] == ex_df['CONS_START_DATE']) & ex_df['CONS_END_DATE'].isna()]

    fig.add_trace(go.Scatter(x=extended_sales_data_with_null_end_date_filter['FE_FP_END'],
                             y=extended_sales_data_with_null_end_date_filter['FE_MEAN'],
                             mode='markers',
                             marker=dict(color='white', size=10, line=dict(color='red', width=2)),
                             name='Forecast Date'),
                  row=1, col=1)

    diff_df = ex_df.groupby('FE_FP_END').agg(
        {'ACTUAL_VALUE': 'first', 'FE_MEAN': 'last', 'FE_NUM_EST': 'last'}).reset_index()
    diff_df['DIFFERENCE'] = diff_df['ACTUAL_VALUE'] - diff_df['FE_MEAN']

    fig.add_trace(go.Bar(x=diff_df['FE_FP_END'],
                         y=diff_df['DIFFERENCE'],
                         name='Difference (Actual - Prediction)',
                         marker_color='skyblue'),
                  row=2, col=1, secondary_y=False)

    fig.add_trace(go.Scatter(x=diff_df['FE_FP_END'],
                             y=diff_df['FE_NUM_EST'],
                             mode='lines+markers',
                             name='Number of Estimates',
                             marker_color='orange'),
                  row=2, col=1, secondary_y=True)

    # Layout updates
    fig.update_layout(height=1200, width=1600, legend_title_text='Legend')
    fig.update_yaxes(title_text='Value', row=1, col=1)
    fig.update_yaxes(title_text='Difference', row=2, col=1, secondary_y=False)
    fig.update_yaxes(title_text='Num of Est.', row=2, col=1, secondary_y=True)

    return dcc.Graph(figure=fig)
