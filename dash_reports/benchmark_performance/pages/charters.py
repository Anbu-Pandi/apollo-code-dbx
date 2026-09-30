from dash import html
from dash import dcc
import plotly.graph_objs as go
from plotly.subplots import make_subplots


def adjust_fig(fig, title, y_title, secondary_y=False):
    fig.update_layout(title_text=f'<span style="font-size: 12px;font-weight: bold;">{title}</span>',
                      title_x=0.35,
                      autosize=False,
                      width=900,
                      height=500, )
    fig.update_xaxes(title_text="Date")

    # Set y-axes titles
    fig.update_yaxes(title_text=f"<b>{y_title}</b>", secondary_y=False)
    if secondary_y:
        fig.update_yaxes(title_text="<b>Beta 1.5y moving avg.</b>", secondary_y=True)


def create_rel_chart(plot_df, y_title):
    if plot_df is None:
        return html.Div([html.Label('No charts to display!')])

    fig = make_subplots(specs=[[{'secondary_y': True}]])

    col = plot_df.columns[0]
    title = col
    fig.add_trace(go.Scatter(x=plot_df.index.to_numpy(), y=plot_df[col],
                             line=dict(color="#000000", width=3),
                             name=col))

    for i, col in enumerate(plot_df.columns[1:]):
        fig.add_trace(go.Scatter(x=plot_df.index.to_numpy(), y=plot_df[col], line=dict(width=1), name=col))

    adjust_fig(fig, title, y_title)
    return html.Div(dcc.Graph(figure=fig))


def create_beta_chart(df, y_title):
    if df is None:
        return html.Div()

    colors = ['#007D55', '#3867B0', '#FDAE2A']

    assert len(df.columns) == 3, f'cannot create beta chart with df shape: {df.shape}. needs three columns.'
    fig = make_subplots(specs=[[{'secondary_y': True}]])

    seconary_dict = {2: True}
    line_style = {2: 'dash'}
    for i, col in enumerate(df.columns):
        fig.add_trace(go.Scatter(x=df.index.to_numpy(), y=df[col],
                                 line=dict(color=colors[i], dash=line_style.get(i, 'solid'), width=1),
                                 name=col), secondary_y=seconary_dict.get(i, False))

    title = f"{df.columns[0]} v/s {df.columns[1]}"
    adjust_fig(fig, title, y_title, secondary_y=True)
    return html.Div(dcc.Graph(figure=fig))
