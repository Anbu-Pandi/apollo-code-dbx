import pandas as pd
import logging
from itertools import product

import dash_reports.report_common.data.utils
import data_access.dbtables.analyst_questionnaire.compliance as compliance_queries
import data_access.dbtables.analyst_questionnaire.analyst_response as analyst_queries

import dash_reports.go_private_form.data.utils as go_private_data_util

from data_access.utils import insert_df_to_db


def _replace_issuer_id_by_company_name(row):
    if row['qid'] in ['issuer_affiliate_public_debt_instrument_id', 'issuer_affiliate_public_equity_issuer_name']:
        value = go_private_data_util.get_bbg_company_name(row['answer'])
    else:
        value = row['answer']
    return value


def _get_display_order():
    df = go_private_data_util.get_analyst_questions_df()
    df = df[(df['report_name'] == 'go_private') & (df['disable'] != 1)]
    df = df[['qid', 'display_order']].dropna(subset=['display_order'])
    sort_order = dict(zip(df['qid'], df['display_order']))
    return sort_order


def get_request_tracker_id_by_company(id, checked_status):
    df = analyst_queries.get_go_private_response(id, checked_status, 'go_private')
    if not df.empty:
        df = df[['qid', 'description', 'company_name', 'answer', 'requested_by',
                 'request_id', 'last_requested', 'status']]
        df['answer'] = df.apply(lambda row: _replace_issuer_id_by_company_name(row), axis=1)
        df = df.set_index(
            ['qid', 'description', 'company_name', 'requested_by', 'request_id', 'last_requested', 'status'])
        df = df.unstack(['company_name', 'requested_by', 'request_id', 'last_requested', 'status'])
        df = df.droplevel(None, axis=1)
        df = df.sort_index(axis=1, level='request_id', ascending=False)
        df = df.reset_index()

        sort_order = _get_display_order()

        df['sort_order'] = df['qid'].apply(lambda x: sort_order[x] if x in sort_order.keys() else None)

        df = df.sort_values('sort_order')
        df = df.drop(['sort_order', 'qid'], axis=1)
        df = df.rename(columns={'': 'Analyst'}, level='requested_by')
        df = df.rename(columns={'': 'Request ID'}, level='request_id')
        df = df.rename(columns={'description': 'Company'}, level='company_name')
        df = df.rename(columns={pd.NaT: 'Requested On'}, level='last_requested')
        df = df.rename(columns={'': 'Status'}, level='status')

    else:
        df = pd.DataFrame()
    return df


def _enrich_compliance_response_df(df):
    df = df.reset_index()
    df[['index', 'row']] = df['index'].str.split(':', expand=True)
    df = df.pivot(columns='index', values='answer', index='row')
    df['id_bb_global_company_name'] = df['node'].map(lambda x: go_private_data_util.get_bbg_company_name(x))

    col_rename = {'apollo_fund': 'fund_entity_code', 'restriction_end_dt': 'end_date',
                  'restriction_start_dt': 'start_date', 'node': 'id_bb_company', 'compliance_comment': 'comments'}
    df = df.rename(columns=col_rename)
    df = df.reset_index(drop=True)
    return df


def insert_request_restriction_id_mapping(response_id, restriction_id, tbl_name):
    mapped_data = [element for element in product(response_id, restriction_id)]
    df = pd.DataFrame(mapped_data, columns=['request_id', 'restriction_id'])
    insert_df_to_db(tbl_name, df)


def insert_compliance_restriction(df, selected_request_ids, last_updated, status, analyst_name):
    if ~df.empty:
        last_updated_by = df.loc['user_id'][-1]
        ult_parent_id = int(float(df.loc['compliance_selection'][-1]))
        ult_parent_name = go_private_data_util.get_bbg_company_name(ult_parent_id)
        df = df.loc[~df.index.isin(['compliance_selection', 'user_id', 'compliance_request_id_selection'])]

        if status == 'approved':
            enrich_response_df = _enrich_compliance_response_df(df)
        else:
            enrich_response_df = df.T.reset_index(drop=True)
            enrich_response_df = enrich_response_df.rename(columns={'compliance_comment': 'comments'})

        enrich_response_df['last_updated_by'] = last_updated_by
        enrich_response_df['ult_parent_id_bb_company'] = int(float(ult_parent_id))
        enrich_response_df['ult_parent_id_bb_global_company_name'] = ult_parent_name
        enrich_response_df['last_updated'] = last_updated
        enrich_response_df['status'] = status

        compliance_restriction_uuid = dash_reports.report_common.data.utils.get_uuid()

        enrich_response_df['sys_key'] = compliance_restriction_uuid

        logging.info(f'Go-Pvt-Form: Inserting Compliance Restrictions for company: {ult_parent_name}')
        insert_df_to_db(go_private_data_util.TBL_COMPLIANCE_RESTRICTION, enrich_response_df)

        restriction_ids = compliance_queries.get_compliance_restriction_id(compliance_restriction_uuid)

        logging.info('Go-Pvt-Form: Inserting analyst request and compliance restriction id mapping')

        insert_request_restriction_id_mapping(selected_request_ids, restriction_ids,
                                              go_private_data_util.TBL_REQUEST_RESTRICTION_ID_MAPPING)

        analyst_queries.update_request_tracker_status(selected_request_ids, status, last_updated)

        return True
    else:
        logging.info('Go-Pvt-Form: Data not inserted')
        return False


def insert_compliance_restriction_log(selected_request_ids, status, last_updated_by, last_updated):
    logging.info(f'Go-Pvt-Form: compliance restriction logs for request ids: {selected_request_ids}')
    df = pd.DataFrame({'request_id': selected_request_ids})
    df['status'] = status
    df['last_updated_by'] = last_updated_by
    df['last_updated'] = last_updated
    insert_df_to_db(go_private_data_util.TBL_COMPLIANCE_RESTRICTION_LOG, df)


def check_null_mandatory_response(df, status):
    questions_df = go_private_data_util.get_analyst_questions_df()

    if status == 'approved':
        section = 'compliance_info'
        filter_condition = (questions_df['report_name'] == 'go_private') & (
                questions_df['section'] == section) & (questions_df['required'] == 'no')
        df = df[~((df.index.str.contains('compliance_comment'))
                  | (df.index.str.contains('alert_date'))
                  | (df.index.str.contains('restriction_end_dt'))
                  )]
        non_mandatory_ids = questions_df[filter_condition]['qid'].to_list()
        df = df.loc[~df.index.isin(non_mandatory_ids)]
    else:
        section = 'compliance_reject'
        filter_condition = (questions_df['report_name'] == 'go_private') & (
                questions_df['section'] == section) & (questions_df['required'] == 'yes')
        non_mandatory_ids = questions_df[filter_condition]['qid'].to_list()
        df = df.loc[df.index.isin(non_mandatory_ids)]

    return any(df['answer'].isnull())


def process_compliance_submission(compliance_response, selected_request_ids, status, last_updated,
                                  analyst_name):
    df = go_private_data_util.transform_response_to_df(compliance_response)
    last_updated_by = df.loc['user_id'][-1]

    if check_null_mandatory_response(df, status):
        display_string = f'Required information missing in Restriction Info'
    else:
        if any(isinstance(element, list) for element in selected_request_ids):
            selected_request_ids = selected_request_ids[-1]

        insert_compliance_restriction(df, selected_request_ids, last_updated, status, analyst_name)
        display_string = f'{status} Request ID - {selected_request_ids}'
        insert_compliance_restriction_log(selected_request_ids, status, last_updated_by, last_updated)

    return display_string
