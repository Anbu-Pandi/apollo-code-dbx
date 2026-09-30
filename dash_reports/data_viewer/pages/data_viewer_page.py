import logging
from dash import callback, Input, Output, State, dcc, html, ALL

from common.utils import dtutil
from dash_reports.report_common.pages.link_and_headers import get_page_title
from dash_reports.report_common.utils.components import make_dash_table
from dash_reports.risk_report.pages.utils import get_datepicker_control
from ..data.table_data import get_available_tables, get_table_data, get_table_config, get_filter_options

DATA_VIEWER_TABLE_DD_ID = 'data-viewer-table-dropdown'
DATA_VIEWER_DATE_CONTROL_ID = 'data-viewer-date-control'
DATA_VIEWER_SUBMIT_ID = 'data-viewer-submit'
DATA_VIEWER_RESULT_ID = 'data-viewer-results'
DATA_VIEWER_FILTERS_CONTAINER_ID = 'data-viewer-filters-container'


def get_data_viewer_header():
    return html.Div([get_page_title("Data Viewer"),
                     html.Br(), ])


def create_filter_control(filter_config, index):
    filter_type = filter_config['type']
    label = filter_config['label']
    column = filter_config['column']
    
    label_div = html.Div([html.Label(f"{label}:", className='subtitle')],
                         style=dict(width='12.5%'))
    
    control_id = {'type': 'filter-control', 'index': index, 'column': column}
    
    if filter_type == 'dropdown':
        options = filter_config.get('options', [])
        control = dcc.Dropdown(
            id=control_id,
            options=[{'label': 'All', 'value': 'All'}] + [{'label': opt, 'value': opt} for opt in options],
            value='All',
            clearable=False
        )
    
    elif filter_type == 'multi_dropdown':
        options = filter_config.get('options', [])
        default = filter_config.get('default', [])
        control = dcc.Dropdown(
            id=control_id,
            options=[{'label': opt, 'value': opt} for opt in options],
            value=default,
            multi=True
        )
    
    elif filter_type == 'text':
        placeholder = filter_config.get('placeholder', f'Enter {label}...')
        control = dcc.Input(
            id=control_id,
            type='text',
            placeholder=placeholder,
            value='',
            style={'width': '100%'}
        )
    
    elif filter_type == 'date_range':
        control = html.Div([
            dcc.DatePickerSingle(
                id={'type': 'filter-control', 'index': f'{index}_start', 'column': column},
                placeholder='Start Date',
                display_format='YYYY-MM-DD'
            ),
            html.Span(' to ', style={'margin': '0 10px'}),
            dcc.DatePickerSingle(
                id={'type': 'filter-control', 'index': f'{index}_end', 'column': column},
                placeholder='End Date',
                display_format='YYYY-MM-DD'
            )
        ], style={'display': 'flex', 'alignItems': 'center'})
    
    elif filter_type == 'numeric_range':
        min_val = filter_config.get('min', 0)
        max_val = filter_config.get('max', 100)
        control = html.Div([
            dcc.Input(
                id={'type': 'filter-control', 'index': f'{index}_min', 'column': column},
                type='number',
                placeholder=f'Min ({min_val})',
                style={'width': '45%'}
            ),
            html.Span(' to ', style={'margin': '0 10px'}),
            dcc.Input(
                id={'type': 'filter-control', 'index': f'{index}_max', 'column': column},
                type='number',
                placeholder=f'Max ({max_val})',
                style={'width': '45%'}
            )
        ], style={'display': 'flex', 'alignItems': 'center'})
    
    else:
        control = html.Div()
    
    return html.Div(
        style=dict(display='flex'),
        children=[label_div, html.Div([control], style=dict(width='50%'))]
    )


def create_layout():
    dt = dtutil.dateadd(dtutil.today(), '-1b')
    tables = get_available_tables()
    
    table_dropdown = html.Div(
        style=dict(display='flex'),
        children=[
            html.Div([html.Label("{:<25}".format("Table:"), className='subtitle')],
                     style=dict(width='12.5%')),
            html.Div([dcc.Dropdown(id=DATA_VIEWER_TABLE_DD_ID, 
                                   options=[{'label': t, 'value': t} for t in tables],
                                   value=tables[0] if tables else None,
                                   clearable=False)],
                     style=dict(width='50%'))
        ])
    
    dates = html.Div(
        style=dict(display='flex'),
        children=[
            html.Div([html.Label("{:<25}".format("Date:"), className='subtitle')],
                     style=dict(width='12.5%')),
            html.Div([get_datepicker_control(DATA_VIEWER_DATE_CONTROL_ID, dt=dt, disabled=False)],
                     style=dict(width='50%', height='35px'))
        ])

    filters_container = html.Div(id=DATA_VIEWER_FILTERS_CONTAINER_ID, children=[])

    submit_btn = html.Div([html.Button('Run', id=DATA_VIEWER_SUBMIT_ID, className='submit-button')])

    search_results = html.Div(id=DATA_VIEWER_RESULT_ID, children=[], className='page')

    return html.Div([
        html.Div([
            table_dropdown,
            dates,
            filters_container,
            submit_btn,
        ], className='fund_filter'),
        search_results
    ])


@callback(
    Output(DATA_VIEWER_FILTERS_CONTAINER_ID, 'children'),
    Input(DATA_VIEWER_TABLE_DD_ID, 'value')
)
def update_filters(table_name):
    if not table_name:
        return []
    
    config = get_table_config(table_name)
    filters_config = config.get('filters', [])
    
    if not filters_config:
        return []
    
    filter_controls = []
    for idx, filter_cfg in enumerate(filters_config):
        if 'options_query' in filter_cfg:
            filter_cfg['options'] = get_filter_options(table_name, filter_cfg)
        filter_controls.append(create_filter_control(filter_cfg, idx))
    
    return filter_controls


def collect_filter_values(filter_states, table_name):
    config = get_table_config(table_name)
    filters_config = config.get('filters', [])
    
    filters_dict = {}
    
    for idx, filter_cfg in enumerate(filters_config):
        column = filter_cfg['column']
        filter_type = filter_cfg['type']
        
        if filter_type in ['dropdown', 'multi_dropdown', 'text']:
            value = filter_states[idx] if idx < len(filter_states) else None
            if value and value != 'All' and value != '':
                filters_dict[column] = value
        
        elif filter_type == 'date_range':
            start_idx = idx * 2
            end_idx = start_idx + 1
            if start_idx < len(filter_states) or end_idx < len(filter_states):
                filters_dict[column] = {
                    'start': filter_states[start_idx] if start_idx < len(filter_states) else None,
                    'end': filter_states[end_idx] if end_idx < len(filter_states) else None
                }
        
        elif filter_type == 'numeric_range':
            min_idx = idx * 2
            max_idx = min_idx + 1
            if min_idx < len(filter_states) or max_idx < len(filter_states):
                filters_dict[column] = {
                    'start': filter_states[min_idx] if min_idx < len(filter_states) else None,
                    'end': filter_states[max_idx] if max_idx < len(filter_states) else None
                }
    
    return filters_dict


def create_result_layout(table_name, dt, filters=None):
    logging.info(f"Data Viewer getting results for table: {table_name}, date: {dt}, filters: {filters}")

    df = get_table_data(table_name, dt, filters)
    
    if df.empty:
        return html.Div([html.H6('No data found', style={'color': 'red'})], className='fund_filter')
    
    filter_info = f" (Filtered)" if filters else ""
    row_label = f"first {len(df):,} rows (truncated)" if df.attrs.get("truncated") else f"{len(df):,} rows"
    title = f"{table_name} - {dt}{filter_info} - {row_label}"
    output_table = html.Div(
        children=[
            html.H6(title, className='subtitle'),
            make_dash_table(df)
        ], 
        className="fund_filter")

    return html.Div(children=[output_table], className='fund_filter')


@callback(
    [Output(DATA_VIEWER_RESULT_ID, "children")],
    [Input(DATA_VIEWER_SUBMIT_ID, "n_clicks")],
    [State(DATA_VIEWER_TABLE_DD_ID, 'value'),
     State(DATA_VIEWER_DATE_CONTROL_ID, 'date'),
     State({'type': 'filter-control', 'index': ALL, 'column': ALL}, 'value')]
)
def on_data_viewer_submit(n_clicks, table_name, dt, filter_values):
    if n_clicks and table_name:
        logging.info(f'Data Viewer submit: table={table_name}, date={dt}, filter_values={filter_values}')
        
        filters_dict = collect_filter_values(filter_values, table_name) if filter_values else {}
        
        return [create_result_layout(table_name, dt, filters_dict)]
    return [html.Div()]
