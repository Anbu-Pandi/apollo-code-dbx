import logging

from common.utils.numutil import string_to_int
from data_access.dbtables.olympus import APP
from data_access.utils import chunk_query

RESTRICTION_SQL = '''
select
    s.bbg_company_id,
	s.is_restricted,
	s.is_child_restricted,
	row_number() over(partition by s.bbg_company_id order by s.everest_restriction_id desc) as rk
from
	agm.restricted_list_lookup c
join agm.restricted_list_lookup s on
	c.ultimate_parent_issuer_olympus_id = s.ultimate_parent_issuer_olympus_id
where
	c.bbg_company_id in {}
'''


def get_restrictions(bbg_company_ids):
    bbg_company_ids = string_to_int(bbg_company_ids)
    df = chunk_query(RESTRICTION_SQL, bbg_company_ids, app=APP)
    df = df[df['rk'] == 1]
    return df


if __name__ == '__main__':
    from common.utils.logutil import setuplogger
    from apo_config.config import config

    setuplogger()

    config.init('prod')
    bbg_ids = [100259, 100386, 100469, 100607, 101264]
    df = get_restrictions(bbg_ids)
    logging.info(df)
