from common.utils import sqlutil
from data_access.dbtables import FINDOX_APP

CORE_SQL = """
WITH issuerslist AS (
    SELECT
        issuer_id,
        concat_ws(';', collect_list(concat(identifier_type_name, ':', identifier))) AS identifier,
        max(created_on_date_time) AS created_on_date_time
    FROM findox_models_common_ids
    GROUP BY issuer_id
)

SELECT
    max(fch.created)                                   AS created,
    max(fch.modified)                                  AS modified,
    avg(fch.deal_doc_count)                            AS deal_doc_count,
    avg(fch.unassigned_doc_count)                      AS unassigned_doc_count,
    avg(fch.doc_count)                                 AS doc_count,
    avg(fch.doc_count_public)                          AS doc_count_public,
    fch.deal_issuer_id,
    fch.deal_issuer_name,
    fch.deal_name,
    fch.deal_type_name,
    fch.deal_agent_name,
    fch.status_name,
    fch.owner_analyst_user_names,
    fch.owner_analysts_user_names,
    fch.agent_name,
    fch.system_issuer_name,
    fch.client_issuer_name,
    fch.source_name,
    fch.deal_source_name,
    fch.in_dataroom,
    fcd.holding_deal_issuer_name,
    fcd.holding_client_issuer_name,
    fcd.holding_deal_name,
    fcd.holding_deal_issuer_id,
    avg(fcd.holding_doc_count)                         AS holding_doc_count,
    avg(fcd.holding_doc_count_public)                  AS holding_doc_count_public,
    fcd.holding_deal_id,
    fcd.folder_path,
    fcd.folder_path_strings,
    fcd.system_folder_path_strings,

    -- Replaces CROSS APPLY + STRING_SPLIT + ROW_NUMBER()
    element_at(split(fcd.system_folder_path_strings, ','), 1) AS path_part_1,
    element_at(split(fcd.system_folder_path_strings, ','), 2) AS path_part_2,
    element_at(split(fcd.system_folder_path_strings, ','), 3) AS path_part_3,

    fcd.system_folder_name,
    fcd.full_filename,
    fcd.deal_doc_is_private,
    fcd.holding_default_private,
    fcd.holding_has_private_access,
    max(fcd.holding_last_posted_private)               AS holding_last_posted_private,
    fcd.is_private,
    fch.default_private,
    fch.has_private_access,
    max(fch.last_posted_private)                       AS last_posted_private,
    fcd.deal_doc_id,
    CASE WHEN max(il.created_on_date_time) IS NULL THEN 0 ELSE 1 END AS has_FDA,
    il.identifier

FROM findox_core_holdings fch
LEFT JOIN findox_core_documents fcd
    ON fch.deal_issuer_id = fcd.holding_deal_issuer_id
LEFT JOIN issuerslist il
    ON il.issuer_id = fch.deal_issuer_id

GROUP BY
    fch.deal_issuer_id,
    fch.deal_issuer_name,
    fch.deal_name,
    fch.deal_type_name,
    fch.deal_agent_name,
    fch.status_name,
    fch.owner_analyst_user_names,
    fch.owner_analysts_user_names,
    fch.agent_name,
    fch.system_issuer_name,
    fch.client_issuer_name,
    fcd.holding_deal_issuer_name,
    fcd.holding_client_issuer_name,
    fcd.holding_deal_name,
    fcd.holding_deal_issuer_id,
    fcd.holding_deal_id,
    fcd.folder_path,
    fcd.folder_path_strings,
    fcd.system_folder_path_strings,
    fcd.system_folder_name,
    fcd.full_filename,
    fcd.deal_doc_id,
    fcd.deal_doc_is_private,
    fcd.holding_default_private,
    fcd.holding_has_private_access,
    fcd.is_private,
    fch.default_private,
    fch.source_name,
    fch.deal_source_name,
    fch.in_dataroom,
    fch.has_private_access,
    il.identifier
limit 5
"""

def get_core_summary():
    df = sqlutil.readdfsql(CORE_SQL, app=FINDOX_APP)
    return df

if __name__ == '__main__':
    import logging
    import common.utils.envsetup as envsetup
    envsetup.init_app('dev', 'findox_core_summary.log', debug=True)

    df = get_core_summary()
    logging.info(df.to_string())