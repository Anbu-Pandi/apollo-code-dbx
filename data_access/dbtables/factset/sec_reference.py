import logging

import pandas as pd

from common.utils import dfutil, sqlutil, dtutil
from data_access.dbtables.factset import APP
from data_access.dbtables.factset.utils import normalize_df
from data_access.utils import tosqllist, get_where_clause, join_where_clauses

ISIN_SQL = '''
SELECT DISTINCT i.isin, i.fsym_id, sc.FSYM_PRIMARY_EQUITY_ID, primary_sc.FSYM_REGIONAL_ID, tr.ticker_region, tr2.bbg_ticker,
parent_se.FACTSET_ENTITY_ID AS parent_factset_entity_id, equity_parent_se.factset_entity_id AS equity_parent_factset_entity_id,
CASE WHEN parent_se.FACTSET_ENTITY_ID = equity_parent_se.factset_entity_id THEN TRUE ELSE FALSE END AS is_equity_ticker_parent
FROM FACTSET_DB.sym_v1.sym_isin i
LEFT JOIN FACTSET_DB.sym_v1.sym_credit_parent c ON i.FSYM_ID = c.FSYM_ID
	AND coalesce('{1}', getdate()) BETWEEN coalesce(c.start_date, '1900-01-01') AND coalesce(c.end_date, '9999-12-31') 
LEFT JOIN FACTSET_DB.sym_v1.sym_sec_entity_hist parent_se  ON i.FSYM_ID = parent_se.FSYM_ID
	AND coalesce('{1}', getdate()) BETWEEN coalesce(parent_se.start_date, '1900-01-01') AND coalesce(parent_se.end_date, '9999-12-31') 
LEFT JOIN FACTSET_DB.sym_v1.SYM_SEC_ENTITY_HIST se 
	ON COALESCE(c.FACTSET_CRED_PARENT_ENTITY_ID, parent_se.FACTSET_ENTITY_ID) = se.FACTSET_ENTITY_ID
	AND coalesce('{1}', getdate()) BETWEEN coalesce(se.start_date, '1900-01-01') AND coalesce(se.end_date, '9999-12-31') 
LEFT JOIN FACTSET_DB.sym_v1.sym_coverage sc ON sc.FSYM_ID = se.FSYM_ID
LEFT JOIN FACTSET_DB.sym_v1.sym_coverage primary_sc ON sc.FSYM_PRIMARY_EQUITY_ID = primary_sc.FSYM_ID
LEFT JOIN FACTSET_DB.sym_v1.sym_ticker_region tr ON tr.FSYM_ID = primary_sc.FSYM_REGIONAL_ID
LEFT JOIN FACTSET_DB.sym_v1.sym_bbg tr2 ON tr2.FSYM_ID = primary_sc.FSYM_REGIONAL_ID
LEFT JOIN FACTSET_DB.sym_v1.SYM_SEC_ENTITY_HIST equity_parent_se ON equity_parent_se.fsym_id=primary_sc.FSYM_SECURITY_ID
	AND coalesce('{1}', getdate()) BETWEEN coalesce(equity_parent_se.start_date, '1900-01-01') AND coalesce(equity_parent_se.end_date, '9999-12-31')
WHERE i.ISIN IN 
{0}
'''

ENTITY_FROM_TICKER_SQL = '''
SELECT tr.ticker_region,
       cov.proper_name,
       cov.fsym_id,
       cov.fsym_security_id,
       se.factset_entity_id,
       ent.entity_proper_name
FROM FACTSET_DB.sym_v1.sym_ticker_region AS tr
JOIN FACTSET_DB.sym_v1.sym_coverage AS cov
  ON tr.fsym_id = cov.fsym_id
JOIN FACTSET_DB.sym_v1.sym_sec_entity AS se
  ON se.fsym_id = cov.fsym_security_id
JOIN FACTSET_DB.sym_v1.sym_entity AS ent
  ON ent.factset_entity_id = se.factset_entity_id
WHERE {0};
'''

ENTITY_IDENTITY_FROM_ROOT_SQL = '''
SELECT DISTINCT tr.ticker_region,
       se.factset_entity_id,
       ent.entity_proper_name,
       names.entity_name_type,
       names.entity_name_value,
       primary_tr.ticker_region AS primary_ticker_region
FROM FACTSET_DB.sym_v1.sym_ticker_region AS tr
JOIN FACTSET_DB.sym_v1.sym_coverage AS cov
  ON tr.fsym_id = cov.fsym_id
JOIN FACTSET_DB.sym_v1.sym_sec_entity AS se
  ON se.fsym_id = cov.fsym_security_id
JOIN FACTSET_DB.sym_v1.sym_entity AS ent
  ON ent.factset_entity_id = se.factset_entity_id
LEFT JOIN FACTSET_DB.ent_v1.ent_entity_names AS names
  ON names.factset_entity_id = se.factset_entity_id
 AND names.entity_name_type IN ('LEGAL_NAME', 'SHORT_NAME', 'TRADE_DBA_NAME')
LEFT JOIN FACTSET_DB.sym_v1.sym_coverage AS security
  ON security.fsym_id = se.fsym_id
LEFT JOIN FACTSET_DB.sym_v1.sym_coverage AS primary_equity
  ON primary_equity.fsym_id = security.fsym_primary_equity_id
LEFT JOIN FACTSET_DB.sym_v1.sym_ticker_region AS primary_tr
  ON primary_tr.fsym_id = primary_equity.fsym_regional_id
WHERE {0}
  AND cov.active_flag = TRUE
  AND primary_equity.fref_security_type IN ('SHARE', 'ADR', 'DR', 'GDR', 'UNIT');
'''

ENTITY_BY_NAME_SQL = '''
SELECT ec.factset_entity_id,
       ec.entity_proper_name
FROM FACTSET_DB.ent_v1.ent_entity_coverage AS ec
WHERE {0};
'''

ENTITY_NAMES_SQL = '''
SELECT en.factset_entity_id,
       en.entity_name_type,
       en.entity_name_value
FROM FACTSET_DB.ent_v1.ent_entity_names AS en
WHERE {0}
  AND en.entity_name_type IN ('LEGAL_NAME', 'SHORT_NAME', 'TRADE_DBA_NAME');
'''

PRIMARY_EQUITY_TICKER_SQL = '''
SELECT DISTINCT se.factset_entity_id,
       tr.ticker_region,
       bbg.bbg_ticker
FROM FACTSET_DB.sym_v1.sym_sec_entity AS se
JOIN FACTSET_DB.sym_v1.sym_coverage AS sc
  ON sc.fsym_id = se.fsym_id
JOIN FACTSET_DB.sym_v1.sym_coverage AS eq
  ON eq.fsym_id = sc.fsym_primary_equity_id
JOIN FACTSET_DB.sym_v1.sym_ticker_region AS tr
  ON tr.fsym_id = eq.fsym_regional_id
LEFT JOIN FACTSET_DB.sym_v1.sym_bbg AS bbg
  ON bbg.fsym_id = eq.fsym_regional_id
WHERE {0}
  AND eq.active_flag = TRUE
  AND eq.fref_security_type IN ('SHARE', 'ADR', 'DR', 'GDR', 'UNIT');
'''

ACTIVE_EQUITY_IDENTITIES_SQL = '''
SELECT DISTINCT ec.factset_entity_id,
       ec.entity_proper_name,
       names.entity_name_type,
       names.entity_name_value,
       tr.ticker_region,
       tr.ticker_region AS primary_ticker_region
FROM FACTSET_DB.ent_v1.ent_entity_coverage AS ec
LEFT JOIN FACTSET_DB.ent_v1.ent_entity_names AS names
  ON names.factset_entity_id = ec.factset_entity_id
 AND names.entity_name_type IN ('LEGAL_NAME', 'SHORT_NAME', 'TRADE_DBA_NAME')
JOIN FACTSET_DB.sym_v1.sym_sec_entity AS se
  ON se.factset_entity_id = ec.factset_entity_id
JOIN FACTSET_DB.sym_v1.sym_coverage AS security
  ON security.fsym_id = se.fsym_id
JOIN FACTSET_DB.sym_v1.sym_coverage AS equity
  ON equity.fsym_id = security.fsym_primary_equity_id
JOIN FACTSET_DB.sym_v1.sym_ticker_region AS tr
  ON tr.fsym_id = equity.fsym_regional_id
WHERE equity.active_flag = TRUE
  AND equity.fref_security_type IN ('SHARE', 'ADR', 'DR', 'GDR', 'UNIT');
'''

CUSIP_ENTITY_ASOF_SQL = '''
SELECT '{1}' AS as_of_date,
       cu.cusip,
       cu.fsym_id,
       se.factset_entity_id,
       ent.entity_proper_name
FROM FACTSET_DB.sym_v1.sym_cusip_hist AS cu
JOIN FACTSET_DB.sym_v1.sym_sec_entity_hist AS se
  ON se.fsym_id = cu.fsym_id
JOIN FACTSET_DB.sym_v1.sym_entity AS ent
  ON ent.factset_entity_id = se.factset_entity_id
WHERE cu.cusip IN {0}
  AND cu.start_date <= '{1}'
  AND (cu.end_date > '{1}' OR cu.end_date IS NULL)
  AND se.start_date <= '{1}'
  AND (se.end_date > '{1}' OR se.end_date IS NULL);
'''

CREDIT_PARENT_SQL = '''
SELECT tr.ticker_region,
       cp.fsym_id,
       cp.factset_cred_parent_entity_id,
       ec.entity_proper_name,
       cp.start_date,
       cp.end_date
FROM FACTSET_DB.sym_v1.sym_credit_parent AS cp
LEFT JOIN FACTSET_DB.sym_v1.sym_ticker_region AS tr
  ON tr.fsym_id = cp.fsym_id
JOIN FACTSET_DB.ent_v1.ent_entity_coverage AS ec
  ON cp.factset_cred_parent_entity_id = ec.factset_entity_id
WHERE {0}
  AND '{1}' BETWEEN COALESCE(cp.start_date, '1900-01-01')
                AND COALESCE(cp.end_date, '9999-12-31');
'''

def get_factset_ids_from_isin(isins, dt):
    dfs = []
    dt = dt or dtutil.today()

    for _isins in dfutil.chunks_list(dfutil.tolist(isins), 10_000):
        _df = sqlutil.readdfsql(ISIN_SQL.format(tosqllist(_isins), dt), app=APP)
        dfs.append(_df)
    df = pd.concat(dfs)
    df = normalize_df(df)
    return df


def get_entity_from_ticker(ticker_regions):
    ticker_clause = get_where_clause('tr.ticker_region', dfutil.tolist(ticker_regions))
    assert ticker_clause, "ticker_regions required"
    sql = ENTITY_FROM_TICKER_SQL.format(ticker_clause)
    logging.info("fetching factset entity from ticker")
    return sqlutil.readdfsql(sql, app=APP)


def get_entities_from_root_tickers(root_tickers):
    dfs = []
    roots = sorted({str(value).strip().upper() for value in dfutil.tolist(root_tickers) if str(value).strip()})
    for chunk in dfutil.chunks_list(roots, 500):
        clause = get_where_clause("SPLIT_PART(UPPER(tr.ticker_region), '-', 1)", chunk)
        if clause:
            dfs.append(sqlutil.readdfsql(ENTITY_FROM_TICKER_SQL.format(clause), app=APP))
    logging.info("fetched factset entities for %d root tickers", len(roots))
    return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame(
        columns=["ticker_region", "proper_name", "fsym_id", "fsym_security_id", "factset_entity_id", "entity_proper_name"]
    )


def get_identity_from_root_tickers(root_tickers):
    dfs = []
    roots = sorted({str(value).strip().upper() for value in dfutil.tolist(root_tickers) if str(value).strip()})
    for chunk in dfutil.chunks_list(roots, 500):
        clause = get_where_clause("SPLIT_PART(UPPER(tr.ticker_region), '-', 1)", chunk)
        if clause:
            dfs.append(sqlutil.readdfsql(ENTITY_IDENTITY_FROM_ROOT_SQL.format(clause), app=APP))
    logging.info("fetched factset identity for %d root tickers", len(roots))
    return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame(
        columns=[
            "ticker_region",
            "factset_entity_id",
            "entity_proper_name",
            "entity_name_type",
            "entity_name_value",
            "primary_ticker_region",
        ]
    )


def get_entities_by_name(name_patterns):
    dfs = []
    for chunk in dfutil.chunks_list(dfutil.tolist(name_patterns), 200):
        clause = get_where_clause('UPPER(ec.entity_proper_name)', chunk)
        if not clause:
            continue
        dfs.append(sqlutil.readdfsql(ENTITY_BY_NAME_SQL.format(clause), app=APP))
    logging.info("fetched factset entities for %d name patterns", len(dfutil.tolist(name_patterns)))
    return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame(columns=["factset_entity_id", "entity_proper_name"])


def get_entity_names(entity_ids):
    dfs = []
    ids = sorted({str(value).strip() for value in dfutil.tolist(entity_ids) if str(value).strip()})
    for chunk in dfutil.chunks_list(ids, 500):
        clause = get_where_clause("en.factset_entity_id", chunk)
        if clause:
            dfs.append(sqlutil.readdfsql(ENTITY_NAMES_SQL.format(clause), app=APP))
    logging.info("fetched factset names for %d entities", len(ids))
    return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame(
        columns=["factset_entity_id", "entity_name_type", "entity_name_value"]
    )


def get_primary_equity_ticker(entity_ids):
    dfs = []
    for chunk in dfutil.chunks_list(dfutil.tolist(entity_ids), 500):
        clause = get_where_clause('se.factset_entity_id', chunk)
        if not clause:
            continue
        dfs.append(sqlutil.readdfsql(PRIMARY_EQUITY_TICKER_SQL.format(clause), app=APP))
    logging.info("fetched factset primary equity tickers for %d entities", len(dfutil.tolist(entity_ids)))
    return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame(columns=["factset_entity_id", "ticker_region", "bbg_ticker"])


def get_active_equity_identities():
    logging.info("fetching active factset equity identities")
    return sqlutil.readdfsql(ACTIVE_EQUITY_IDENTITIES_SQL, app=APP)


def get_cusip_entity_asof(cusips, asof_date):
    asof_date = dtutil.todate(asof_date)
    cusips = tosqllist(dfutil.tolist(cusips))
    sql = CUSIP_ENTITY_ASOF_SQL.format(cusips, asof_date)
    logging.info("fetching factset entity from cusip as-of")
    return sqlutil.readdfsql(sql, app=APP)


def get_credit_parent_history(fsym_ids=None, ticker_regions=None, asof_date=None):
    asof_date = dtutil.todate(asof_date or dtutil.today())
    fsym_clause = get_where_clause('cp.fsym_id', dfutil.tolist(fsym_ids))
    ticker_clause = get_where_clause('tr.ticker_region', dfutil.tolist(ticker_regions))
    where_clause = join_where_clauses(' OR ', [fsym_clause, ticker_clause])
    assert where_clause, "fsym_ids or ticker_regions required"
    sql = CREDIT_PARENT_SQL.format(where_clause, asof_date)
    logging.info("fetching factset credit parent history")
    return sqlutil.readdfsql(sql, app=APP)


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()
    from apo_config.config import config

    config.init('prod')
    isins = ['US25470XBD66','US25470XAX30','US25470XBE40','USU25486AW88','US25470XBC83','USU25486AP38','USU25486AM07','USU25486AU23','USU25486AN89','USU25486AR93','USU25486AQ11','US25470XAY13','US25470XBF15','USU25486AV06','US25470XBJ37','US25470XBB01','US25470XAZ87' ]
    isins=['US25470XBD66']
    isins = ['US29078EAA38']
    df = get_factset_ids_from_isin(isins, None)
    logging.info('\n'+df.to_string())

    df_ent = get_entity_from_ticker(['AAPL-US'])
    logging.info(df_ent.to_string())

    df_cusip = get_cusip_entity_asof(['037833100'], '2020-12-31')
    logging.info(df_cusip.to_string())

    df_cp = get_credit_parent_history(ticker_regions=['AAPL-US'], asof_date='2020-12-31')
    logging.info(df_cp.to_string())
