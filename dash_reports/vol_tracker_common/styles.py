import dash_core_components as dcc
import dash_html_components as html
import pandas as pd
import plotly.graph_objects as go

from dash_reports.report_common.utils.components import make_dash_table

ACCENT_COLOR = '#006D77'

APOLLO_COLORS = [
    '#006D77', '#267d53', '#2c3e50', '#d29922', '#58a6ff',
    '#f85149', '#3fb950', '#8b949e', '#1BAFDC', '#A6C773',
]

CHART_LAYOUT = dict(
    plot_bgcolor='rgba(0,0,0,0)',
    paper_bgcolor='white',
    font=dict(family='Helvetica, Arial, sans-serif', size=11, color='#3a3a3a'),
    hovermode='x unified',
    autosize=True,
    height=420,
    legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1,
                font=dict(size=10), bgcolor='rgba(255,255,255,0.8)'),
    margin=dict(l=60, r=30, t=50, b=50),
    xaxis=dict(tickformat='%b %Y', tickangle=-45, gridcolor='#eee',
               showline=True, linecolor='#ccc'),
    yaxis=dict(gridcolor='#eee', showline=True, linecolor='#ccc',
               zeroline=True, zerolinecolor=ACCENT_COLOR, zerolinewidth=1),
)

SUBMIT_BUTTON_STYLE = {
    'backgroundColor': ACCENT_COLOR, 'color': 'white', 'border': 'none',
    'padding': '8px 32px', 'borderRadius': '4px', 'cursor': 'pointer',
    'fontWeight': '600', 'fontSize': '12px', 'marginTop': '10px',
}

FILTER_BAR_STYLE = {
    'padding': '10px 1cm', 'backgroundColor': '#fafafa',
    'borderBottom': f'2px solid {ACCENT_COLOR}', 'borderRadius': '6px 6px 0 0',
}

SUMMARY_BOX_STYLE = {
    'backgroundColor': '#0d1117', 'color': '#c9d1d9', 'padding': '14px 20px',
    'borderRadius': '6px', 'fontFamily': 'Helvetica, Arial, sans-serif', 'fontSize': '12px',
    'border': '1px solid #30363d', 'marginBottom': '12px',
}

FOCUS_HEADER_STYLE = {
    'backgroundColor': '#0d1117', 'padding': '10px 16px',
    'borderRadius': '6px 6px 0 0', 'border': '1px solid #30363d',
    'borderBottom': 'none',
}

MATRIX_CONTAINER_STYLE = {
    'padding': '16px', 'backgroundColor': '#fafbfc', 'borderRadius': '8px',
    'border': '1px solid #e2e6ea', 'overflowX': 'auto',
}

SECTION_DIVIDER = {'borderTop': f'1px solid #e2e6ea', 'marginTop': '18px', 'paddingTop': '10px'}

TAB_BORDER = f'2px solid {ACCENT_COLOR}'
TAB_BORDER_THICK = f'3px solid {ACCENT_COLOR}'

_TAB_FONT = {'fontFamily': 'Helvetica, Arial, sans-serif', 'fontWeight': '600'}

_L1_ACCENT = '#7b8fa3'
MATRIX_TAB_STYLE = {
    **_TAB_FONT, 'fontSize': '12px', 'padding': '10px 20px',
    'backgroundColor': '#f4f6f8', 'border': '1px solid #dce1e6',
    'borderBottom': 'none', 'borderRadius': '6px 6px 0 0',
    'color': '#7b8fa3', 'marginRight': '2px',
}
MATRIX_TAB_SELECTED = {
    **_TAB_FONT, 'fontSize': '12px', 'padding': '10px 20px',
    'backgroundColor': '#e8edf2', 'color': '#3d5068',
    'border': '1px solid #c5cdd6', 'borderBottom': '2px solid #7b8fa3',
    'borderRadius': '6px 6px 0 0', 'marginRight': '2px',
}

_L2_ACCENT = '#c0976f'
ENTITY_TAB_STYLE = {
    **_TAB_FONT, 'fontSize': '11px', 'padding': '8px 14px',
    'backgroundColor': 'white', 'color': '#a89070',
    'borderBottom': '2px solid transparent', 'borderRadius': '4px 4px 0 0',
}
ENTITY_TAB_SELECTED = {
    **_TAB_FONT, 'fontSize': '11px', 'padding': '8px 14px',
    'backgroundColor': '#faf6f1', 'color': '#6b5540',
    'borderBottom': f'2px solid {_L2_ACCENT}', 'borderRadius': '4px 4px 0 0',
}

_L3_ACCENT = '#7aa07a'
PRODUCT_TAB_STYLE = {
    **_TAB_FONT, 'fontSize': '10px', 'padding': '6px 12px',
    'backgroundColor': 'white', 'color': '#8ea88e',
    'borderBottom': '2px solid transparent',
}
PRODUCT_TAB_SELECTED = {
    **_TAB_FONT, 'fontSize': '10px', 'padding': '6px 12px',
    'backgroundColor': '#f2f7f2', 'color': '#4a6b4a',
    'borderBottom': f'2px solid {_L3_ACCENT}',
}

SPACING_SM = {'height': '8px'}
SPACING_MD = {'height': '12px'}
SPACING_LG = {'height': '18px'}

STY = {
    'filter_row': {'display': 'flex', 'flexWrap': 'wrap', 'alignItems': 'center', 'gap': '14px'},
    'filter_label': {'fontSize': '11px', 'color': '#555', 'fontWeight': '600', 'marginRight': '4px',
                     'borderLeft': f'3px solid {ACCENT_COLOR}', 'paddingLeft': '6px'},
    'filter_group': {'display': 'flex', 'alignItems': 'center'},
    'matrix_cell': {
        'borderRadius': '6px', 'padding': '8px 10px',
        'fontFamily': 'Helvetica, Arial, sans-serif', 'fontSize': '11px',
        'cursor': 'pointer', 'transition': 'box-shadow 0.15s',
    },
    'matrix_header': {
        'fontSize': '10px', 'fontWeight': '700', 'color': '#2c3e50',
        'textTransform': 'uppercase', 'letterSpacing': '0.5px', 'padding': '6px 10px',
        'borderBottom': f'2px solid {ACCENT_COLOR}', 'textAlign': 'center',
    },
    'entity_label': {
        'fontWeight': '700', 'color': ACCENT_COLOR, 'fontSize': '12px',
        'display': 'flex', 'alignItems': 'center', 'justifyContent': 'center',
    },
    'regime_tag': {
        'display': 'inline-block', 'borderRadius': '3px', 'padding': '1px 6px',
        'fontSize': '9px', 'fontWeight': '700', 'letterSpacing': '0.3px', 'marginTop': '4px',
    },
    'watch': {'fontSize': '9px', 'color': '#58667e', 'marginTop': '3px', 'fontStyle': 'italic'},
    'no_data': {'padding': '20px', 'color': '#999'},
    'recent_header': {'color': '#2c3e50', 'borderLeft': f'4px solid {ACCENT_COLOR}',
                      'paddingLeft': '10px', 'fontSize': '0.85rem'},
}


def make_filter(label, component):
    return html.Div([
        html.Span(label, style=STY['filter_label']),
        component,
    ], style=STY['filter_group'])


def arrow(val):
    return '\u25b2' if val > 0 else ('\u25bc' if val < 0 else '\u25c6')


def delta_color(val):
    return '#d29922' if val > 0 else ('#006D77' if val < 0 else '#8b949e')


def make_ts_chart(df, title, yaxis_title='', colors=None, roll_dates=None):
    if df is None or df.empty:
        return html.Div('No data available.', style=STY['no_data'])

    colors = colors or APOLLO_COLORS
    numeric_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    cols = numeric_cols if numeric_cols else df.columns

    fig = go.Figure()
    for i, col in enumerate(cols):
        fig.add_trace(go.Scatter(
            x=df.index, y=df[col], mode='lines', name=col,
            line=dict(width=2, color=colors[i % len(colors)])
        ))

    if roll_dates:
        for rd in roll_dates:
            rd_ms = int(pd.Timestamp(rd).timestamp() * 1000)
            fig.add_shape(type='line', x0=rd_ms, x1=rd_ms, y0=0, y1=1,
                          xref='x', yref='paper',
                          line=dict(dash='dot', color='#aaa', width=1))
            fig.add_annotation(x=rd_ms, y=1, xref='x', yref='paper',
                               text='roll', showarrow=False,
                               font=dict(size=8, color='#999'), yshift=8)

    layout = {**CHART_LAYOUT, 'title': dict(text=title, font=dict(size=13, color='#2c3e50'))}
    if yaxis_title:
        layout['yaxis'] = {**layout.get('yaxis', {}), 'title': yaxis_title}
    fig.update_layout(**layout)
    return dcc.Graph(figure=fig, config={'displayModeBar': True, 'displaylogo': False})


def make_recent_table(df):
    if df is None or df.empty:
        return html.Div('No data available.', style=STY['no_data'])

    numeric_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    tbl_df = df[numeric_cols].copy() if numeric_cols else df.copy()
    tbl_df.index = pd.to_datetime(tbl_df.index).strftime('%Y-%m-%d')
    tbl_df = tbl_df.tail(30).iloc[::-1].reset_index()
    tbl_df.columns = ['Date'] + list(tbl_df.columns[1:])
    return make_dash_table(tbl_df, page_size=15, num_col_width='90px', txt_col_width='100px',
                           precision_2_format=True)


def build_summary_box(matrix_data):
    s = matrix_data['summary']
    lines = [
        ('Global Regime', s.get('dominant_regime', '')),
        ('Outlier', s.get('outlier', '')),
        ('Strongest Signal', s.get('strongest_signal', '')),
    ]
    return html.Div([
        html.Div([
            html.Span(label + ': ', style={'fontWeight': '700', 'color': '#58a6ff', 'marginRight': '4px'}),
            html.Span(value, style={'color': '#e6edf3'}),
        ], style={'marginBottom': '4px'})
        for label, value in lines
    ], style=SUMMARY_BOX_STYLE)


def build_focus_chart_wrapper(entity, inst, product_label, fig):
    return html.Div([
        html.Div([
            html.Span(f'\u25ce  {entity} {inst}', style={
                'fontWeight': '700', 'fontSize': '14px', 'color': '#e6edf3'}),
            html.Span(f'  \u2014  {product_label}', style={
                'color': '#8b949e', 'fontSize': '11px', 'marginLeft': '6px'}),
        ], style=FOCUS_HEADER_STYLE),
        dcc.Graph(figure=fig, config={'displayModeBar': True, 'displaylogo': False}),
    ], style={'marginTop': '14px', 'marginBottom': '6px'})


def build_grid_style(n_inst, entity_col_width='80px'):
    return {'display': 'grid', 'gridTemplateColumns': f'{entity_col_width} repeat({n_inst}, 1fr)', 'gap': '6px'}


def build_signal_matrix_html(grid_children, xccy_row):
    return html.Div([
        html.Div(grid_children, style={'overflowX': 'auto'}),
        xccy_row,
    ], style=MATRIX_CONTAINER_STYLE)


def build_xccy_item(label, value):
    return html.Div([
        html.Div(label, style={'fontSize': '8px', 'color': '#8b949e', 'fontWeight': '600',
                                'textTransform': 'uppercase', 'letterSpacing': '0.3px'}),
        html.Div(value, style={'fontSize': '11px', 'fontWeight': '700', 'color': '#2c3e50'}),
    ], style={'flex': '1', 'textAlign': 'center', 'padding': '6px 10px',
              'backgroundColor': '#f0f3f6', 'borderRadius': '4px',
              'borderTop': f'2px solid {ACCENT_COLOR}'})


def build_xccy_row(items, entity_col_width='80px'):
    return html.Div([
        html.Div('', style={'width': entity_col_width, 'flexShrink': '0'}),
        *[build_xccy_item(label, value) for label, value in items],
    ], style={'display': 'flex', 'gap': '6px', 'marginTop': '6px'})


def build_product_tab_content(chart, table_div):
    return html.Div([
        chart,
        html.Div(style=SPACING_MD),
        html.H6('Recent Values', style=STY['recent_header']),
        html.Div(table_div, style={'marginTop': '6px'}),
    ], style={'padding': '12px'})


def build_static_layout(primary_row, advanced_row, submit_id):
    advanced_section = html.Details([
        html.Summary('Advanced Filters', style={
            'cursor': 'pointer', 'fontSize': '11px', 'fontWeight': '600',
            'color': ACCENT_COLOR, 'padding': '6px 0', 'userSelect': 'none',
        }),
        advanced_row,
    ], style={'marginTop': '6px'}) if advanced_row else None

    filter_children = [primary_row]
    if advanced_section:
        filter_children.append(advanced_section)

    return html.Div([
        html.Br(),
        html.Div(filter_children, className='fund_filter', style=FILTER_BAR_STYLE),
        html.Div(
            html.Button('Submit', id=submit_id, className='submit-button', style=SUBMIT_BUTTON_STYLE),
            className='fund_filter'),
        html.Br(),
    ])
