import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


def chart_app_usage(df, cols_to_plot, x_axis_col):
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    hover_template = '%{hovertext}: %{y}'
    for col in cols_to_plot:
        fig.add_trace(go.Scatter(x=df[x_axis_col], y=df[col].values, name=col,
                                 hovertext=df[x_axis_col],
                                 hovertemplate=hover_template
                                 ),
                      secondary_y=True if 'Rolling 5 Day Avg' in col else False
                      )

    df[x_axis_col] = pd.to_datetime(df[x_axis_col])

    fig.update_layout({'plot_bgcolor': 'rgba(0, 0, 0, 0)'},
                      width=1300,
                      height=500,
                      hovermode='x',
                      yaxis_title=f"Page View",
                      yaxis2_title=f'Rolling 5 Day Avg',
                      xaxis_title='Date',
                      yaxis=dict(dtick=25),
                      yaxis2=dict(dtick=15)
                      )
    return fig
