import pandas as pd
import logging
from datetime import datetime

import dash_reports.report_common.data.utils
import data_access.dbtables.analyst_questionnaire.compliance as compliance_queries
import data_access.dbtables.analyst_questionnaire.analyst_response as analyst_queries
from data_access.utils import insert_df_to_db
import dash_reports.go_private_form.data.utils as data_utils

EVENT_MAP_TABLE = 'compliance_request_event_id_map'
EVENT_ID_TRACKER_TABLE = 'compliance_event_id_tracker'


def get_request_id(company, analyst, status):
    df = analyst_queries.get_request_tracker_data('go_private')

    if not (company is None or len(company) == 0):
        company_list = [company] if isinstance(company, str) else company
        df = df[df['ult_company_id'].isin([float(x) for x in company_list])]

    if not (analyst is None or len(analyst) == 0):
        analyst_list = [analyst] if isinstance(analyst, str) else analyst
        df = df[df['requested_by'].isin(analyst_list)]

    if not (status is None or len(status) == 0):
        status_list = [status] if isinstance(analyst, str) else status
        df = df[df['status'].isin(status_list)]

    request_ids = df['request_id'].to_list()

    return request_ids


def _get_request_restriction_map_df(request_ids_list):
    df = compliance_queries.get_request_restriction_id_map()
    df = df.groupby('request_id').max()
    df = df[df.index.isin(request_ids_list)]
    df = df.reset_index()

    return df


def _get_compliance_restriction(restriction_ids):
    df = compliance_queries.compliance_restrictions()
    df = df.set_index('restriction_id')
    df = df[df.index.isin(restriction_ids)]
    return df


def get_compliance_restrictions_by_request(request_ids):
    request_restriction_map_df = _get_request_restriction_map_df(request_ids)

    restriction_ids = request_restriction_map_df['restriction_id'].unique().tolist()

    restriction_df = _get_compliance_restriction(restriction_ids)
    restriction_df = restriction_df.T

    restriction_request_dict = request_restriction_map_df.groupby('restriction_id').apply(
        lambda x: [','.join(x['request_id'].astype(str).to_list())]).to_dict()

    restriction_df = pd.concat([restriction_df, pd.DataFrame(restriction_request_dict, index=['request_id'])],
                               ignore_index=True)
    restriction_df.index.name = 'restriction_id'
    restriction_df = restriction_df.rename(index={'id_bb_company': 'Company ID', 'id_bb_global_company_name': 'Company',
                                                  'ult_parent_id_bb_company': 'Parent Company ID',
                                                  'ult_parent_id_bb_global_company_name': 'Parent Company'})
    restriction_df = restriction_df.reset_index()

    return restriction_df


def insert_request_event_map(request_id, user):
    if isinstance(request_id[0], list):
        request_id = request_id[0]
    sys_key = dash_reports.report_common.data.utils.get_uuid()

    logging.info(f'creating event id in compliance_event_id_tracker table')

    insert_df_to_db(EVENT_ID_TRACKER_TABLE, pd.DataFrame({'sys_key': [sys_key]})
                    )

    event_id = compliance_queries.get_compliance_event_id(sys_key)

    df = pd.DataFrame({'request_id': request_id})
    df['updated_by'] = user
    df['last_updated'] = datetime.now()
    df['event_id'] = event_id

    logging.info(f'Assigning event id to request id: {request_id}')

    insert_df_to_db(EVENT_MAP_TABLE, df)
    return f'Event ID Assigned to request id - {request_id}'


def get_request_event_matrix(request_ids):
    df = compliance_queries.get_request_event_id_map()
    df = df[df['request_id'].isin(request_ids)]
    df['event_id'] = df['event_id'].apply(lambda x: f'Event ID {x}')
    df['occurence'] = 1
    df = df.set_index(['request_id', 'event_id', 'updated_by', 'last_updated'])
    df = df.unstack(['event_id', 'updated_by', 'last_updated'])
    df.columns = df.columns.droplevel(None)
    df = df.reindex(request_ids)
    df = df.sort_values('event_id', axis=1, ascending=False)
    df = df.sort_index(ascending=False)
    df = df.reset_index()

    df = df.rename(columns={'request_id': 'Event ID'}, level='event_id')
    df = df.rename(columns={'': 'Updated By'}, level='updated_by')
    df = df.rename(columns={pd.NaT: 'Last Updated'}, level='last_updated')

    return df


def get_event_id_map_tbl(request_ids):
    df = compliance_queries.get_request_event_id_map()
    df = df[df['request_id'].isin(request_ids)]
    df['request_id'] = df['request_id'].apply(lambda x: f'Request ID {x}')
    df = df.set_index(['request_id', 'last_updated', 'updated_by'])
    df = df.unstack('request_id')
    df.columns = df.columns.droplevel(0)
    df = df.reset_index()
    df = df.sort_values('last_updated', ascending=False)
    df = df.rename(columns={'last_updated': 'Last Updated', 'updated_by': 'Updated By'})
    return df


if __name__ == '__main__':
    from apo_config.config import config

    config.init('dev')
    # config.init('prod')
    request_ids = [49, 38, 44]
    get_request_event_matrix(request_ids)
