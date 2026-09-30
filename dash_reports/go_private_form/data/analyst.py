import logging

import dash_reports.report_common.data.utils as common_data_utils
from common.utils.dtutil import today
from datetime import datetime
import data_access.dbtables.analyst_questionnaire.analyst_response as analyst_response
from data_access.utils import insert_df_to_db

import dash_reports.go_private_form.data.utils as go_private_data_util


def check_null_mandatory_response(df):
    questions_df = go_private_data_util.get_analyst_questions_df()

    filter_condition = (questions_df['report_name'] == 'go_private') & (
            questions_df['section'] != 'compliance_info') & (questions_df['required'] == 'yes')

    required_qids = questions_df[filter_condition]['qid'].to_list()
    required_qids.append('user_id')
    df = df.loc[df.index.isin(required_qids)]
    return any(df['answer'].isnull())


def _insert_company_analyst_by_detail(df, analyst_company_detail_uuid, asof_date, company_name, last_updated_by,
                                      last_update_ts):
    website = df.loc['company_identifier_other_entity_website']['answer']
    address = df.loc['company_identifier_other_entity_address']['answer']
    region = df.loc['company_identifier_other_entity_region']['answer']

    data = {'sys_key': analyst_company_detail_uuid, 'asof_date': asof_date, 'company_name': company_name,
            'website': website, 'address': address, 'region': region, 'updated_by': last_updated_by,
            'last_update_ts': last_update_ts}

    logging.info('Go Private Form: Inserting Analyst Suggested Company and Website')
    go_private_data_util.insert_dict_to_table(data, tbl_name=go_private_data_util.TBL_ANALYST_COMPANY_DETAIL)


def insert_analyst_response(response, attachment_details, analyst_name, request_type):
    df = go_private_data_util.transform_response_to_df(response)
    display_string = 'User ID or mandatory information missing'
    if not check_null_mandatory_response(df):

        asof_date = today()
        last_update_ts = datetime.now()
        last_updated_by = df.loc['user_id'].iloc[-1]

        ult_company_id = None
        ult_company_name = None

        if 'company_identifier_security_id' in df.index:
            id = df.loc['company_identifier_security_id']['answer']
            company_name = go_private_data_util.get_bbg_company_name(id)
            ult_company_id, ult_company_name = go_private_data_util.get_ult_parent_company_details(id)

        else:
            company_name = df.loc['company_identifier_other_entity_name']['answer']
            id = analyst_response.get_analyst_company_id_by_name(company_name)

            if id is None:
                analyst_company_detail_uuid = common_data_utils.get_uuid()
                _insert_company_analyst_by_detail(df, analyst_company_detail_uuid, asof_date, company_name,
                                                  last_updated_by, last_update_ts)
                id = analyst_response.get_anayst_updated_company_by_key(sys_key=analyst_company_detail_uuid)

        if (ult_company_name is None) or (ult_company_id is None):
            ult_company_name = company_name
            ult_company_id = id

        logging.info(f'Go Private Form: Adding entry to analyst_request_tracker table')
        analyst_request_tracker_uuid = common_data_utils.get_uuid()
        go_private_data_util.insert_dict_to_table({'sys_key': analyst_request_tracker_uuid, 'request_type': request_type,
                                        'company_id': id, 'company_name': company_name,
                                        'requested_by': last_updated_by, 'last_requested': last_update_ts,
                                        'request_date': asof_date, 'status': 'pending',
                                        'ult_company_id': ult_company_id, 'ult_company_name': ult_company_name},
                                                  tbl_name=go_private_data_util.TBL_ANALYST_REQUEST_TRACKER)

        request_id = analyst_response.get_analyst_request_id_by_key(sys_key=analyst_request_tracker_uuid)

        df.index.name = 'qid'
        df['company_id'] = id
        df['company_name'] = company_name
        df['request_id'] = request_id

        df = df.loc[
            ~df.index.isin(['user_id', 'company_identifier_security_id', 'company_identifier_other_entity_name'])]

        df = df.reset_index()

        logging.info(f'Go-Pvt-Form: Inserting Analyst Response to Database for request_id {request_id}')
        insert_df_to_db(go_private_data_util.TBL_ANALYST_RESPONSE, df, method='sql')

        if not (all(item is None for item in attachment_details['attachment_name']) & (attachment_details[
                                                                                           'attachments'] is None)):
            logging.info(f'Go-Pvt-Form: Saving attachment on network drive for request_id {request_id}')
            go_private_data_util.save_attachment(attachment_details['attachment_name'], attachment_details['attachments'],
                                                 request_id)

        display_string = 'Your response recorded successfully!'
    else:
        logging.info('Go-Pvt-Form: User ID or mandatory information missing')

    return display_string
