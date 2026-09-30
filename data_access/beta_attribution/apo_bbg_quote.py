from data_access.beta_attribution.utils import get_count_for_table_by_date

DATE_COLUMN = 'asofdate'

TABLE_NAME = 'dbo.apo_bbg_quote'


def get_count(asofdate):
    df = get_count_for_table_by_date(TABLE_NAME, DATE_COLUMN, asofdate)
    return df
