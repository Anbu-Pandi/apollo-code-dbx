import dash_core_components as dcc
import dash_html_components as html
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from dash_reports.risk_report.data.index_static_dynamic_beta_chart import get_index_beta_data, INDEXES


def generate_mvng_avg_chart(df, index):
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    for col in df.columns:
        if col == f'78 per. Mov. Avg. ({index})':
            fig.add_trace(
                go.Scatter(x=df.index, y=df[col].values, name=col,
                           line=dict(width=2, dash='dot')))  # secondary_y=True
        else:
            fig.add_trace(go.Scatter(x=df.index, y=df[col].values, name=col, line=dict(width=2)))

        fig.update_layout({'plot_bgcolor': 'rgba(0, 0, 0, 0)'},
                          width=1200,
                          height=500,
                          yaxis=dict(dtick=0.3, showgrid=True),
                          xaxis=dict(tickformat="%e %b %Y"),
                          )
    return fig


def get_index_beta_chart():
    df = get_index_beta_data()

    charts = {}
    for index in INDEXES:
        temp_df = df[[x for x in df.columns if index in x]]
        charts[f'{index} 1.5y M-Avg Dynamic vs 1y Static Beta'] = generate_mvng_avg_chart(temp_df, index)

    return charts


def create_layout():
    beta_charts = get_index_beta_chart()
    children = []
    for key, value in beta_charts.items():
        children.append(
            html.Div(
                [
                    html.Div([html.H6([key], className="subtitle padded")]),
                    html.Div([dcc.Graph(figure=value)])
                ],
            )
        )

    return html.Div(children=html.Div(id='index_beta_chart', children=children, className="sub_page"), className="page")
