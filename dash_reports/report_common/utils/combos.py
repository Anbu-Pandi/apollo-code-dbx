from dash import dcc, html
from .components import make_dash_table


def get_dash_table_with_header(header, df, div_class_name=None, **dash_args):
    return html.Div([html.H6(header, className='subtitle'),
                     make_dash_table(df, **dash_args),
                     html.Br()],
                    className=div_class_name)


def get_labelled_component(label, component):
    return html.Div(style=dict(display='flex'),
                    children=[html.Label("{:<25}".format(label), className='subtitle', style=dict(width='20%')),
                              html.Div(component, style=dict(width='80%'))])
