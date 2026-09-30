import os
import base64
import pandas as pd
import logging
from dash_reports.report_common.data.data_model import datastore
from dash_reports.report_common.elastic_search.api import default_es
from data_access.utils import insert_df_to_db
from data_access.dbtables.analyst_questionnaire import analyst_response
from dash_reports.issuers_info.data.caching import ES_BBG_ISSUERS_INDEX, ES_BBG_BONDS_INDEX, ES_WSO_LOANS_INDEX

SECTION = ['entity_identifier', 'request_type', 'deal_information', 'public_request_form',
           'issuer_affiliates_information', 'other_information', 'compliance_info']

TBL_ANALYST_RESPONSE = 'analyst_response'
TBL_ANALYST_REQUEST_TRACKER = 'analyst_request_tracker'
TBL_ANALYST_COMPANY_DETAIL = 'analyst_company_detail'

TBL_COMPLIANCE_RESTRICTION = 'compliance_restrictions'
TBL_COMPLIANCE_RESTRICTION_LOG = 'compliance_restriction_log'
TBL_REQUEST_RESTRICTION_ID_MAPPING = 'analyst_request_compliance_restrictions_map'

NETWORK_DIR_PATH = datastore.get_network_dir_path()


def get_analyst_questions_df():
    key = 'analyst_questions'
    df = datastore.get_app_key(key)
    if df is None:
        df = analyst_response.get_analyst_questions()
        datastore.set_app_key(key, df)
    return df


def get_form_questions(section):
    df = get_analyst_questions_df()
    df = df[df['section'] == section].sort_values('order')
    return df


def get_analyst_issuer_coverage(identifier, search_key='id_bb_company'):
    df = default_es.search_exact(ES_BBG_ISSUERS_INDEX, search_key, identifier)
    return df


def get_bbg_company_name(identifier):
    company_detail_df = get_analyst_issuer_coverage(identifier, search_key='id_bb_company')

    if not company_detail_df.empty:
        company_name = company_detail_df['id_bb_global_company_name'].iloc[0]
    else:
        df = analyst_response.get_analyst_company_detail()
        df = df[df['company_id'] == identifier]
        company_name = df['company_name'].iloc[0]

    return company_name


def get_ult_parent_company_details(identifier):
    company_detail_df = get_analyst_issuer_coverage(identifier, search_key='id_bb_company')
    if company_detail_df.empty:
        return None, None
    return company_detail_df['id_bb_ultimate_parent_co'].iloc[-1], \
           company_detail_df['long_ult_parent_comp_name'].iloc[-1]


def transform_response_to_df(analyst_response):
    logging.info('Go-Pvt-Form: Convert Response to Dataframe')
    df = pd.DataFrame()
    for _, val in analyst_response.items():
        col_names = [value['index'] for value in val['ids']]
        response = val['response']
        temp_df = pd.DataFrame(response, index=col_names, columns=['answer'])
        df = pd.concat(sor[df, temp_df], sort=False)

        del temp_df
    return df


def insert_dict_to_table(data_dict, tbl_name):
    df = pd.DataFrame(data_dict, index=[0])
    insert_df_to_db(tbl_name, df)


def get_analyst_company_attribs(request_type):
    df = analyst_response.get_analyst_company_detail()
    if request_type == 'company_name':
        data = df['company_name'].drop_duplicates().to_list()
    elif request_type == 'company_website':
        data = df['website'].drop_duplicates().to_list()
    elif request_type == 'company_region':
        data = df['region'].drop_duplicates().to_list()
    elif request_type == 'company_address':
        data = df['address'].drop_duplicates().to_list()
    else:
        data = []
    return data


def get_requested_company_details_dict():
    df = analyst_response.get_request_tracker_data('go_private')
    df = df[['ult_company_name', 'ult_company_id']]
    df = df.drop_duplicates()
    df = df.dropna(how='all')
    df = df.rename(columns={'ult_company_name': 'label', 'ult_company_id': 'value'})

    name_id_dict = df.to_dict(orient='records')
    return name_id_dict


def decode_attachment(name, content, request_id):
    """Decode and store a file uploaded with Plotly Dash."""
    data = content.encode("utf8").split(b";base64,")[1]
    output_path = os.path.join(NETWORK_DIR_PATH, 'go_private_form', str(request_id))
    if not os.path.exists(output_path):
        os.makedirs(output_path)
    with open(os.path.join(output_path, name), "wb") as fp:
        fp.write(base64.decodebytes(data))


def save_attachment(uploaded_filenames, uploaded_file_contents, request_id):
    """Save uploaded files and regenerate the file list."""
    file_upload_status = []
    if uploaded_filenames is not None and uploaded_file_contents is not None:
        if isinstance(uploaded_filenames, list):
            for name, data in zip(uploaded_filenames, uploaded_file_contents):
                decode_attachment(name, data, request_id)
        else:
            decode_attachment(uploaded_filenames, uploaded_file_contents)

    return file_upload_status
