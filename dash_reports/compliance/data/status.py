import pandas as pd

from data_access.dbtables.analyst_questionnaire.analyst_response import get_request_tracker_data
from data_access.dbtables.analyst_questionnaire.compliance import compliance_restrictions
from common.utils.dtutil import today
from common.utils.dtutil import dateadd


def get_status_fields(grp_df):
    company_name = grp_df['ult_company_name'].iloc[-1]
    request_count = grp_df['request_id'].count()
    last_requested = grp_df['last_requested'].max()
    requested_by = ', '.join(grp_df['requested_by'].values)
    last_requested_by = grp_df.sort_values('requested_by', ascending=False).head(1)['requested_by'].iloc[-1]
    return company_name, request_count, last_requested, requested_by, last_requested_by


def get_status_df(status):
    status_df = pd.DataFrame()
    df = get_request_tracker_data('go_private')
    df = df[df['status'] == status]

    for grp_key, grp_df in df.groupby('ult_company_name'):
        temp_df = pd.DataFrame(
            {'Company': [grp_key],
             'Request Count': [grp_df['request_id'].count()],
             'Last Requested': [grp_df['last_requested'].max()],
             'Requested By': ', '.join(grp_df['requested_by'].dropna().values),
             'Last Requested By': [grp_df.sort_values('requested_by', ascending=False).head(1)['requested_by'].iloc[-1]]
             }
        )

        status_df = pd.concat([status_df, temp_df], ignore_index=True)
        del temp_df

    return status_df


def get_upcoming_alerts_df(lookup=15):
    df = compliance_restrictions()
    curr_dt = today()
    lookup_dt = dateadd(curr_dt, f'{lookup}b')

    df = df[(df['alert_date'] >= pd.to_datetime(curr_dt)) & (df['alert_date'] <= pd.to_datetime(lookup_dt))]
    df = df[['restriction_id', 'id_bb_company', 'id_bb_global_company_name', 'ult_parent_id_bb_company',
             'ult_parent_id_bb_global_company_name', 'start_date', 'end_date', 'alert_date']]

    col_rename = {'restriction_id': 'Restriction ID', 'id_bb_company': 'Company ID',
                  'id_bb_global_company_name': 'Company Name', 'ult_parent_id_bb_company': 'Parent Co ID',
                  'ult_parent_id_bb_global_company_name': 'Parent Company', 'start_date': 'Restriction Start Date',
                  'end_date': 'Restriction End Date', 'alert_date': 'Alert Date'}

    df = df.sort_values('alert_date', axis=0)
    df = df.rename(columns=col_rename)

    return df


if __name__ == '__main__':
    from apo_config.config import config

    # config.init('dev')
    config.init('prod')
    df = get_upcoming_alerts_df()
    print(df.to_string())
