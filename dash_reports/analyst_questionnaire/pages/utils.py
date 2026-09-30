import pandas as pd
import datetime
import dash_bootstrap_components as dbc
import dash_core_components as dcc
import dash_html_components as html
import json

from dash.exceptions import PreventUpdate

from dash_reports.analyst_questionnaire.data.questions import get_section_questions
from common.utils.dtutil import boyear, dateadd
from common.utils.dtutil import today
from dash_reports.analyst_questionnaire.data.utils import get_analyst_answers
from dash_reports.report_common.utils import dfhelpers
from dash_reports.report_common.utils.components import get_datepicker_control, make_dash_table, create_link
from dash_reports.analyst_questionnaire import pages
from dash_reports.issuers_info import pages as restricted_issuers_pages
from data_access.utils import insert_df_to_db

from dash_reports.report_common.pages.link_and_headers import get_page_title
from dash_reports.report_common.elastic_search.api import default_es
from dash_reports.analyst_questionnaire.data.caching_const import ES_ISSUER_COVERAGE_INDEX

SPGLOBAL_LINK = 'https://www.capitaliq.com/CIQDotNet/Financial/CapStructureSummary.aspx'

DD_COMPANY_NAME_ID = 'company_name_dd'
ANALYST_SUBMIT_BUTTON_ID = 'analyst_ques_button'
ANALYST_RESPONSE = 'analyst_response'
ANALYST_QUESTIONS = 'analyst_questions'
METRICS_TABLE_ID = 'metrics_table'
PUSH_TO_DB_BUTTON_ID = 'push_to_db'
DB_INSERT_STATUS_ID = 'push-to-db-result-container'

markdown_style = {'font-size': '14px', 'color': 'red', 'font-weight': 'bold'}
ques_response_markdown_style = {'font-size': '14px', 'color': 'red'}
base_value_markdown_style = {'font-size': '14px'}
OVERRIDEN_CELL_COLOR = 'yellow'

ANALYST_RESPONSE_TBL = 'analyst_questionnaire_response'
ANALYST_DATA_OVERRIDE_TBL = 'analyst_data_override'

MARKDOWN_STYLE = {'font-size': '14px', 'color': 'red', 'font-weight': 'bold'}

MODAL_CANCEL_BUTTON_ID = 'modal-cancel-button'
MODAL_CONFRIM_BUTTON_ID = 'modal-confirm-button'
MODAL_WINDOW_ID = 'analyst-ques-modal-window'
MODAL_WINDOW_BODY_ID = 'analyst-ques-modal-body'

QUES_BASE_VALUE_DIV = 'ques_base_value'
QUES_CALC_DIV = 'ques_base_value_calc'
QUES_ASSUMPTION_VALUE_STORE_TYPE_ID = 'ques_assumption_value_store'

assumption_title = {
    'IQ_COMMON_DIV_CF': {'absolute': '6mo Dividend Paid (mm)', 'pct_change': '6mo Dividend Paid Change (%)'},
    'DIVIDEND_FORECAST': {'absolute': '6mo Dividend Forecast (mm)', 'pct_change': '6mo Dividend Forecast Change (%)'},
    'sharesOutstanding': {'absolute': 'Shares Outstanding (mm)', 'pct_change': '6mo Shares Outstanding Change (%)'},
    'IQ_TOTAL_DEBT': {'absolute': 'Total Debt (mm)', 'pct_change': '6mo Total Debt (%)'}
}


def create_questionnaire_specific_header_layout(page, id_bb_company):
    return html.Div(
        children=[
            dcc.Store(id="diff-store", storage_type='memory'),
            get_questionnaire_links(page, id_bb_company),
            get_company_name_dropdown(id_bb_company)]
    )


def get_company_name_dropdown(selected_value=None):
    options = None
    if selected_value:
        df = default_es.search_exact(ES_ISSUER_COVERAGE_INDEX, 'id_bb_company', selected_value)
        options = [{'label': x['id_bb_global_company_name'], 'value': int(x['id_bb_company'])}
                   for _, x in df.iterrows() if pd.notnull(x['id_bb_company'])]

    company_dd_header = html.Div(id='company_name_dd_container',
                                 className='fund_filter',
                                 children=[html.Div(style=dict(display='flex'),
                                                    children=[html.Div([html.Label("{:<25}".format("Company Name:"),
                                                                                   className='subtitle')],
                                                                       style=dict(width='20%')),
                                                              html.Div([dcc.Dropdown(value=selected_value,
                                                                                     multi=False,
                                                                                     id=DD_COMPANY_NAME_ID,
                                                                                     options=options),
                                                                        html.Div(id='fund_output_container')],
                                                                       style=dict(width='80%'))
                                                              ]),
                                           html.Div(
                                               [html.Button('Go', id=ANALYST_SUBMIT_BUTTON_ID,
                                                            className='submit-button')])])

    return company_dd_header


def get_questionnaire_headers(app=None):
    if app is not None:
        app.title = 'Analyst Questionnaire'
    layout = html.Div([get_page_title("Analyst Questionnaire"),
                       html.Br(),
                       ])
    return layout


def get_ques_specific_div(path, id_bb_company):
    period_type = 'LTM'
    id_bb_company = int(id_bb_company)
    to_date = today()
    from_date = dateadd(boyear(to_date, False), '-5y')
    if path == 'issuers':
        return pages.issuers.create_issuers_layout()
    elif path == 'company-info':
        return pages.company_info.create_layout(id_bb_company, path)
    elif path == 'bulk-load':
        return pages.bulk_load.bulk_load_layout()
    elif path == 'capital-structure':
        return restricted_issuers_pages.capital_structure.create_cap_struct_layout(id_bb_company,
                                                                                   show_corp_tree_df=True)
    elif path == 'debt-issuance-outlook':
        return pages.debt_issuance.result_debt_issuance_layout(id_bb_company, from_date, to_date, period_type, path)
    elif path == 'equity-issuance-outlook':
        return pages.equity_issuance.result_equity_issuance_layout(id_bb_company, from_date, to_date, period_type, path)
    elif path == 'acquirer-or-acquired':
        return pages.acquirer_or_acquired.result_acquisition_layout(id_bb_company, from_date, to_date, period_type,
                                                                    path)
    elif path == 'ratings-momentum':
        return pages.ratings_momentum.result_ratings_momentum_layout(id_bb_company, from_date, to_date, period_type,
                                                                     path)
    elif path == 'management-team-perception':
        rating_type = 'STDLONG'
        return pages.mgmnt_team_perception.result_management_perspective_layout(id_bb_company, from_date, to_date,
                                                                                period_type, path, rating_type)
    elif path == 'ownership-details':
        return pages.ownership_details.result_ownership_details_layout(id_bb_company, from_date, to_date, period_type,
                                                                       path)

    else:
        return []


def get_ques_specific_kids(path, id_bb_company):
    div_kids = []
    div_kids.append(get_ques_specific_div(path, id_bb_company))
    div_kids.append(html.Div(id='push-to-db-result-container2'))
    return div_kids


def get_ques_specific_layout(path, id_bb_company):
    page = path.split('/')[-1]
    static_html = [create_questionnaire_specific_header_layout(page, id_bb_company)]
    div_kids = get_ques_specific_kids(path, id_bb_company)
    return html.Div(id='questionnaire-header-container', children=static_html), \
        html.Div(id='questionnaire-result-container', children=div_kids)


def get_questionnaire_links(page, id_bb_company=-1):
    menu = html.Div([create_link('analyst-questionnaire', 'issuers', 'Issuers', f'id_bb_company={id_bb_company}', page),
                     create_link('analyst-questionnaire', 'company-info', 'Company Info',
                                 f'id_bb_company={id_bb_company}', page),
                     create_link('analyst-questionnaire', 'bulk-load', 'Bulk Load',
                                 f'id_bb_company={id_bb_company}', page),
                     create_link('analyst-questionnaire', 'capital-structure', 'Capital Structure',
                                 f'id_bb_company={id_bb_company}', page),
                     ], className="row all-tabs", )
    return menu


def get_label(string):
    return html.Div([html.Label(string,
                                className='subtitle')],
                    style={"margin-right": "1%"},
                    )


def get_date_section():
    dt = today()
    return html.Div(id="date-input-controls",
                    children=[get_label('From Date'),
                              get_datepicker_control(dt=dt, id='from_date_control', disabled=False),
                              get_label('To Date'),
                              get_datepicker_control(dt=dt, id='to_date_control', disabled=False),
                              ],
                    style={'display': 'flex'},
                    className='fund_filter')


PERIOD_TYPE = ['Quarterly', 'Annual']


def get_period_type(default_value='Quarterly', display='flex'):
    option_list = []

    for data in PERIOD_TYPE:
        option_list.append({'label': data, 'value': data})

    return html.Div(style=dict(display=display),
                    children=[get_label('Period'),
                              dcc.RadioItems(id='period_type',
                                             options=option_list,
                                             value=default_value,
                                             )],
                    className="fund_filter", )


def get_data_button():
    return html.Div(className='fund_filter',
                    children=[html.Button('Submit', id='analyst_ques_button',
                                          className='submit-button')],
                    style={'width': '50%'})


def get_push_to_db_button(button_key, status_container_key):
    return html.Div(className='fund_filter',
                    children=[
                        html.Button("Override Financials", id={'type': PUSH_TO_DB_BUTTON_ID, 'index': button_key},
                                    className='submit-button'),
                        html.Div(id={'type': DB_INSERT_STATUS_ID, 'index': status_container_key},
                                 style={'marginLeft': '60px'})
                    ],
                    style={'width': '100%'})


def diff_dashtable(data, data_previous, company_id, row_id_name=None):
    """Generate a diff of Dash DataTable data.

    Modified from: https://community.plotly.com/t/detecting-changed-cell-in-editable-datatable/26219/2

    Parameters
    ----------
    data: DataTable property (https://dash.plot.ly/datatable/reference)
        The contents of the table (list of dicts)
    data_previous: DataTable property
        The previous state of `data` (list of dicts).

    Returns
    -------
    A list of dictionaries in form of [{row_id_name:, column_name:, current_value:,
        previous_value:}]
    """
    df, df_previous = pd.DataFrame(data=data), pd.DataFrame(data_previous)

    if row_id_name is not None:
        # If using something other than the index for row id's, set it here
        for _df in [df, df_previous]:
            # Why do this?  Guess just to be sure?
            assert row_id_name in _df.columns

            _df = _df.set_index(row_id_name)
    else:
        row_id_name = "index"

    # Pandas/Numpy says NaN != NaN, so we cannot simply compare the dataframes.  Instead we can either replace the
    # NaNs with some unique value (which is fastest for very small arrays, but doesn't scale well) or we can do
    # (from https://stackoverflow.com/a/19322739/5394584):
    # Mask of elements that have changed, as a dataframe.  Each element indicates True if df!=df_prev
    df_mask = ~((df == df_previous) | ((df != df) & (df_previous != df_previous)))

    # ...and keep only rows that include a changed value
    df_mask = df_mask.loc[df_mask.any(axis=1)]

    changes = []

    # This feels like a place I could speed this up if needed
    for idx, row in df_mask.iterrows():
        row_id = row.name

        # Act only on columns that had a change
        row = row[row.eq(True)]

        for change in row.iteritems():
            changes.append(
                {
                    "company_id": int(company_id),
                    "metric": df.at[row_id, 'Metrics'],
                    "source_col": change[0],
                    "updated_value": df.at[row_id, change[0]],
                    "prev_value": df_previous.at[row_id, change[0]],
                }
            )

    return changes


def _convert_to_df(data):
    override_df = pd.DataFrame()
    for k, v in data.items():
        df = default_es.search_exact(ES_ISSUER_COVERAGE_INDEX, 'id_bb_company', v[0]['company_id'])
        v[0]['company_name'] = df.iloc[0]['id_bb_global_company_name']
        temp_df = pd.DataFrame(data=v, index=[datetime.datetime.strptime(k, '%Y-%m-%d %H:%M:%S:%f')])
        override_df = override_df.append(temp_df)
        del temp_df
    override_df.index.name = 'last_update_ts'
    return override_df.reset_index()


def get_curr_ts():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S:%f')


def _push_to_db(df, tbl_name):
    insert_df_to_db(tbl_name, df, method='sql')


def snp_financials_page(id_bb_company, page):
    df = default_es.search_exact(ES_ISSUER_COVERAGE_INDEX, 'id_bb_company', id_bb_company)
    company_id = int(df.iloc[0]['sp_company_id']) if pd.notnull(df.iloc[0]['sp_company_id']) else -1
    link = f'{SPGLOBAL_LINK}?CompanyId={company_id}'
    return html.A('SnP Financials Link',
                  href=link,
                  target='_blank',
                  style={'font-size': '12px', 'color': 'red', 'font-weight': 'bold',
                         'text-decoration': 'underline'}, )


def unpack_result_html(result_dict, heading):
    html_list = [html.H2([heading], className="subtitle padded"), html.Br()]

    '''
        Below variables are used in callback to capture table-diff and insert data by analyst.
        It is used in the end for defining keys of dash component. The integer is then used in callback as index to 
        get data from list. 
    '''

    tbl_num = 0
    fig_id = 0

    for key, value in result_dict.items():
        if not key == 'analyst_response':
            title = html.H2([key], className="subtitle padded")
            if result_dict[key] is None:
                temp_val = "No Data"
                temp_fig = []
                temp_push_to_db_button = []
            else:
                temp_val = []
                temp_fig = []
                temp_push_to_db_button = []

                if not result_dict[key]['company_stats']['df'] is None:
                    df = result_dict[key]['company_stats']['df']
                    dash_props = result_dict[key]['company_stats']['dash_props']
                    _, data_styling, conditional_tooltip = \
                        _unpack_dash_props(df, result_dict[key]['company_stats']['dash_props'])
                    df.columns = [dfhelpers.ui_column_name(x) for x in df.columns]
                    temp_val = make_dash_table(df, id={'type': METRICS_TABLE_ID, 'index': key + '-tbl-' + str(tbl_num)},
                                               txt_col_width='200px',
                                               conditional_tooltip=conditional_tooltip,
                                               data_styling=data_styling,
                                               dynamic_num_format=True,
                                               **{k: v for k, v in dash_props.items() if
                                                  k not in ['conditional_formatting', 'cell_style']}
                                               )
                    tbl_num += 1

                if not result_dict[key]['fig'] is None:
                    temp_fig = dcc.Graph(id=key + '-chart-' + str(fig_id), figure=result_dict[key]['fig'])
                    fig_id += 1

            html_list += [title, temp_fig, html.Br(), temp_val, temp_push_to_db_button, html.Br()]
            del temp_val, temp_fig, temp_push_to_db_button

    val = html.Div(children=html_list)

    return val


def _unpack_dash_props(df, dash_props):
    editable_flag = dash_props['editable']
    if editable_flag and 'conditional_formatting' in dash_props:
        cell_style, conditional_tool_tip = _get_cell_styling(df, dash_props['conditional_formatting']['cell_highlight'])
    else:
        cell_style = []
        conditional_tool_tip = []

    return editable_flag, cell_style, conditional_tool_tip


def _get_cell_styling(df, cell_highlight):
    cell_style = []
    conditional_tool_tip = []
    for key, value in cell_highlight.items():
        key_list = key.split('_')
        col = key_list[1]
        metric = key_list[2]
        if 'metrics' in df.columns:
            if metric in df['metrics'].to_list():
                row_index = df.loc[df['metrics'] == metric].index[0]
                cell_style.append({'if': {'row_index': row_index,
                                          'column_id': col},
                                   'backgroundColor': OVERRIDEN_CELL_COLOR,
                                   })

                conditional_tool_tip.append(
                    {'if': {'row_index': row_index,
                            'column_id': col},
                     'type': 'markdown',
                     'value': f"SnP Value: {value['snp']} \n Value by Analyst: {value['analyst_value']}"

                     }
                )

    return cell_style, conditional_tool_tip


def get_last_analyst_response_html(id_bb_company, qid):
    last_analyst_response = get_analyst_answers(id_bb_company, qid)
    response = ''
    if last_analyst_response is not None:
        response = f'Value: {last_analyst_response["value"]} ; ' \
                   f'Last updated by: {last_analyst_response["last_updated_by"]} ' \
                   f'on {last_analyst_response["last_update_ts"]}'

    return html.Div([dcc.Markdown(response, id='analyst_response_space', style=ques_response_markdown_style)],
                    style=dict(width='25%', display='inline-block'))


def get_component(id, input_type, input_properties={}):
    if input_type == 'radio':
        component = dcc.RadioItems(id=id,
                                   className='radio_type',
                                   **input_properties,
                                   )
    elif input_type == 'text':
        component = dcc.Input(id=id,
                              style={'lineHeight': '0.5px', 'height': '30px', 'borderWidth': '0.01px',
                                     'width': '250px'},
                              debounce=True,
                              **input_properties
                              )

    elif input_type == 'text_area':
        component = dbc.Textarea(id=id,
                                 style={'width': '20%', 'height': '800%'},
                                 spellCheck=True,
                                 debounce=True,
                                 **input_properties
                                 )
    else:
        component = []

    return component


def get_qid_base_value_html(section, assumptions):
    div_kids = [html.Br()]
    if assumptions is not None:
        div_kids += assumptions

    div_id = {'type': QUES_BASE_VALUE_DIV, 'index': f'{section}'}
    return html.Div(div_kids, id=div_id, style=base_value_markdown_style, loading_state=dict(is_loading=False))


def get_questions_html(section, id_bb_company, assumptions_list=None, default_input_values={}):
    children = [html.Div([html.H2(['Questions: '], className="subtitle padded")], className="six columns")]

    df_ques = get_section_questions(section)
    base_value_div = get_qid_base_value_html(section, assumptions_list)
    children.append(base_value_div)

    for _, qrow in df_ques.iterrows():
        qid = qrow["qid"]
        ques = f"({qrow['order']}) {qrow['description']}"
        input_properties = qrow['input_properties']

        last_analyst_response_div = get_last_analyst_response_html(id_bb_company, qid)

        if qrow['required'] == 'yes':
            class_name = 'li required'
        else:
            class_name = 'li'

        if pd.isnull(input_properties):
            input_properties = {}
        else:
            input_properties = json.loads(input_properties)

        if qid in default_input_values.keys():
            input_properties.update({'value': default_input_values[qid]})

        component = get_component({'type': ANALYST_RESPONSE, 'index': f'{section}|{qid}'}, qrow['input_type'],
                                  input_properties)

        ques_div = html.Div([html.Label(ques, className=class_name, style={'font-size': '14px'},
                                        id={'type': ANALYST_QUESTIONS, 'index': f'{section}|{qid}'})],
                            style={'width': '33%', 'display': 'inline-block'})

        component_div = html.Div([component], style={'width': '31%', 'display': 'inline-block'})

        ques_calc_div = html.Div(dcc.Markdown([], style={'font-size': '14px', 'color': 'red'}, mathjax=True,
                                              id={'type': QUES_CALC_DIV, 'index': f'{section}|{qid}'}))

        children.extend([html.Div([html.Div([ques_div, component_div, last_analyst_response_div],
                                            style=dict(width='100%'))]),
                         ques_calc_div])

    children.extend([html.Br(), get_analyst_response_button(section), html.Br()])

    return html.Div(children=children, style=dict(width='100%'))


def get_analyst_response_button(section):
    return html.Div([
        html.Div(className='fund_filter',
                 children=[
                     html.Button("Submit Response", id={'type': "analyst_response_button", 'index': section},
                                 className='submit-button'),
                     html.Div(id={'type': 'analyst-ques-response-output', 'index': section},
                              style={'marginLeft': '60px'}),

                 ],
                 style={'width': '100%', 'display': 'flex'}
                 ),
        html.Div(dbc.Modal(
            [dbc.ModalHeader(dbc.ModalTitle("Response Confirmation"), close_button=True),
             dbc.ModalBody([], id={'type': MODAL_WINDOW_BODY_ID, 'index': section}),
             dbc.ModalFooter(
                 [dbc.Button(
                     "Confirm",
                     id={'type': MODAL_CONFRIM_BUTTON_ID, 'index': section},
                     n_clicks=0,
                 ),
                     dbc.Button(
                         "Cancel",
                         id={'type': MODAL_CANCEL_BUTTON_ID, 'index': section},
                         n_clicks=0,
                     )])],
            id={'type': MODAL_WINDOW_ID, 'index': section},
            backdrop=True,
            scrollable=False,
            is_open=False
        ))
    ])
