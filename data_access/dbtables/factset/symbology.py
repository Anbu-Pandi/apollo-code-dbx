import logging

from common.utils import dtutil, dfutil, sqlutil
from data_access.dbtables.factset import APP
from data_access.utils import tosqllist, get_where_clause, join_where_clauses

SYMBOL_CROSS_REFERENCE_SQL = '''
SELECT tr.ticker_region,
       tr.fsym_id,
       se.factset_entity_id,
       isin.isin,
       cusip.cusip,
       sedol.sedol,
       bbg.bbg_ticker
FROM FACTSET_DB.sym_v1.sym_ticker_region AS tr
JOIN FACTSET_DB.sym_v1.sym_coverage AS sc
  ON sc.fsym_id = tr.fsym_id
JOIN FACTSET_DB.sym_v1.sym_sec_entity AS se
  ON se.fsym_id = sc.fsym_security_id
LEFT JOIN FACTSET_DB.sym_v1.sym_isin AS isin
  ON isin.fsym_id = sc.fsym_security_id
LEFT JOIN FACTSET_DB.sym_v1.sym_cusip AS cusip
  ON cusip.fsym_id = sc.fsym_security_id
LEFT JOIN FACTSET_DB.sym_v1.sym_sedol AS sedol
  ON sedol.fsym_id = sc.fsym_security_id
LEFT JOIN FACTSET_DB.sym_v1.sym_bbg AS bbg
  ON bbg.fsym_id = sc.fsym_security_id
WHERE {0};
'''

SYMBOL_CROSS_REFERENCE_ASOF_SQL = '''
SELECT tr.ticker_region,
       tr.fsym_id,
       se.factset_entity_id,
       isin.isin,
       cusip.cusip,
       sedol.sedol,
       bbg.bbg_ticker
FROM FACTSET_DB.sym_v1.sym_ticker_region AS tr
JOIN FACTSET_DB.sym_v1.sym_coverage AS sc
  ON sc.fsym_id = tr.fsym_id
JOIN FACTSET_DB.sym_v1.sym_sec_entity_hist AS se
  ON se.fsym_id = sc.fsym_security_id
LEFT JOIN FACTSET_DB.sym_v1.sym_isin_hist AS isin
  ON isin.fsym_id = sc.fsym_security_id
LEFT JOIN FACTSET_DB.sym_v1.sym_cusip_hist AS cusip
  ON cusip.fsym_id = sc.fsym_security_id
LEFT JOIN FACTSET_DB.sym_v1.sym_sedol_hist AS sedol
  ON sedol.fsym_id = sc.fsym_security_id
LEFT JOIN FACTSET_DB.sym_v1.sym_bbg_hist AS bbg
  ON bbg.fsym_id = sc.fsym_security_id
WHERE {0}
  AND '{1}' BETWEEN COALESCE(se.start_date, '1900-01-01') AND COALESCE(se.end_date, '9999-12-31')
  AND '{1}' BETWEEN COALESCE(isin.start_date, '1900-01-01') AND COALESCE(isin.end_date, '9999-12-31')
  AND '{1}' BETWEEN COALESCE(cusip.start_date, '1900-01-01') AND COALESCE(cusip.end_date, '9999-12-31')
  AND '{1}' BETWEEN COALESCE(sedol.start_date, '1900-01-01') AND COALESCE(sedol.end_date, '9999-12-31')
  AND '{1}' BETWEEN COALESCE(bbg.start_date, '1900-01-01') AND COALESCE(bbg.end_date, '9999-12-31');
'''

SYMBOL_CROSS_REFERENCE_ASOF_NO_SEDOL_SQL = '''
SELECT tr.ticker_region,
       tr.fsym_id,
       se.factset_entity_id,
       isin.isin,
       cusip.cusip,
       bbg.bbg_ticker
FROM FACTSET_DB.sym_v1.sym_ticker_region AS tr
JOIN FACTSET_DB.sym_v1.sym_coverage AS sc
  ON sc.fsym_id = tr.fsym_id
JOIN FACTSET_DB.sym_v1.sym_sec_entity_hist AS se
  ON se.fsym_id = sc.fsym_security_id
LEFT JOIN FACTSET_DB.sym_v1.sym_isin_hist AS isin
  ON isin.fsym_id = sc.fsym_security_id
LEFT JOIN FACTSET_DB.sym_v1.sym_cusip_hist AS cusip
  ON cusip.fsym_id = sc.fsym_security_id
LEFT JOIN FACTSET_DB.sym_v1.sym_bbg_hist AS bbg
  ON bbg.fsym_id = sc.fsym_security_id
WHERE {0}
  AND '{1}' BETWEEN COALESCE(se.start_date, '1900-01-01') AND COALESCE(se.end_date, '9999-12-31')
  AND '{1}' BETWEEN COALESCE(isin.start_date, '1900-01-01') AND COALESCE(isin.end_date, '9999-12-31')
  AND '{1}' BETWEEN COALESCE(cusip.start_date, '1900-01-01') AND COALESCE(cusip.end_date, '9999-12-31')
  AND '{1}' BETWEEN COALESCE(bbg.start_date, '1900-01-01') AND COALESCE(bbg.end_date, '9999-12-31');
'''

SYMBOL_CROSS_REFERENCE_ASOF_NO_BBG_SQL = '''
SELECT tr.ticker_region,
       tr.fsym_id,
       se.factset_entity_id,
       isin.isin,
       cusip.cusip,
       sedol.sedol
FROM FACTSET_DB.sym_v1.sym_ticker_region AS tr
JOIN FACTSET_DB.sym_v1.sym_coverage AS sc
  ON sc.fsym_id = tr.fsym_id
JOIN FACTSET_DB.sym_v1.sym_sec_entity_hist AS se
  ON se.fsym_id = sc.fsym_security_id
LEFT JOIN FACTSET_DB.sym_v1.sym_isin_hist AS isin
  ON isin.fsym_id = sc.fsym_security_id
LEFT JOIN FACTSET_DB.sym_v1.sym_cusip_hist AS cusip
  ON cusip.fsym_id = sc.fsym_security_id
LEFT JOIN FACTSET_DB.sym_v1.sym_sedol_hist AS sedol
  ON sedol.fsym_id = sc.fsym_security_id
WHERE {0}
  AND '{1}' BETWEEN COALESCE(se.start_date, '1900-01-01') AND COALESCE(se.end_date, '9999-12-31')
  AND '{1}' BETWEEN COALESCE(isin.start_date, '1900-01-01') AND COALESCE(isin.end_date, '9999-12-31')
  AND '{1}' BETWEEN COALESCE(cusip.start_date, '1900-01-01') AND COALESCE(cusip.end_date, '9999-12-31')
  AND '{1}' BETWEEN COALESCE(sedol.start_date, '1900-01-01') AND COALESCE(sedol.end_date, '9999-12-31');
'''

SYMBOL_CROSS_REFERENCE_ASOF_NO_SEDOL_NO_BBG_SQL = '''
SELECT tr.ticker_region,
       tr.fsym_id,
       se.factset_entity_id,
       isin.isin,
       cusip.cusip
FROM FACTSET_DB.sym_v1.sym_ticker_region AS tr
JOIN FACTSET_DB.sym_v1.sym_coverage AS sc
  ON sc.fsym_id = tr.fsym_id
JOIN FACTSET_DB.sym_v1.sym_sec_entity_hist AS se
  ON se.fsym_id = sc.fsym_security_id
LEFT JOIN FACTSET_DB.sym_v1.sym_isin_hist AS isin
  ON isin.fsym_id = sc.fsym_security_id
LEFT JOIN FACTSET_DB.sym_v1.sym_cusip_hist AS cusip
  ON cusip.fsym_id = sc.fsym_security_id
WHERE {0}
  AND '{1}' BETWEEN COALESCE(se.start_date, '1900-01-01') AND COALESCE(se.end_date, '9999-12-31')
  AND '{1}' BETWEEN COALESCE(isin.start_date, '1900-01-01') AND COALESCE(isin.end_date, '9999-12-31')
  AND '{1}' BETWEEN COALESCE(cusip.start_date, '1900-01-01') AND COALESCE(cusip.end_date, '9999-12-31');
'''


def _build_where_clause(ticker_regions=None, fsym_ids=None, entity_ids=None, isins=None, cusips=None):
    ticker_clause = get_where_clause('tr.ticker_region', dfutil.tolist(ticker_regions))
    fsym_clause = get_where_clause('tr.fsym_id', dfutil.tolist(fsym_ids))
    entity_clause = get_where_clause('se.factset_entity_id', dfutil.tolist(entity_ids))
    isin_clause = get_where_clause('isin.isin', dfutil.tolist(isins))
    cusip_clause = get_where_clause('cusip.cusip', dfutil.tolist(cusips))

    where_clause = join_where_clauses(' OR ', [
        ticker_clause, fsym_clause, entity_clause, isin_clause, cusip_clause
    ])
    assert where_clause, "Provide at least one filter: ticker_regions, fsym_ids, entity_ids, isins, or cusips."
    return where_clause


def get_symbol_cross_reference(ticker_regions=None, fsym_ids=None, entity_ids=None, isins=None, cusips=None):
    where_clause = _build_where_clause(ticker_regions, fsym_ids, entity_ids, isins, cusips)
    sql = SYMBOL_CROSS_REFERENCE_SQL.format(where_clause)
    logging.info("fetching factset symbology cross reference")
    return sqlutil.readdfsql(sql, app=APP)


def get_symbol_cross_reference_asof(asof_date, ticker_regions=None, fsym_ids=None, entity_ids=None, isins=None, cusips=None):
    asof_date = dtutil.todate(asof_date)
    where_clause = _build_where_clause(ticker_regions, fsym_ids, entity_ids, isins, cusips)
    sql = SYMBOL_CROSS_REFERENCE_ASOF_SQL.format(where_clause, asof_date)
    logging.info("fetching factset symbology cross reference as-of")
    try:
        return sqlutil.readdfsql(sql, app=APP)
    except Exception:
        try:
            fallback_sql = SYMBOL_CROSS_REFERENCE_ASOF_NO_SEDOL_SQL.format(where_clause, asof_date)
            logging.info("symbology as-of fallback without SEDOL")
            return sqlutil.readdfsql(fallback_sql, app=APP)
        except Exception:
            try:
                fallback_sql = SYMBOL_CROSS_REFERENCE_ASOF_NO_BBG_SQL.format(where_clause, asof_date)
                logging.info("symbology as-of fallback without BBG")
                return sqlutil.readdfsql(fallback_sql, app=APP)
            except Exception:
                fallback_sql = SYMBOL_CROSS_REFERENCE_ASOF_NO_SEDOL_NO_BBG_SQL.format(where_clause, asof_date)
                logging.info("symbology as-of fallback without SEDOL/BBG")
                return sqlutil.readdfsql(fallback_sql, app=APP)


if __name__ == "__main__":
    from common.utils.logutil import setuplogger
    from apo_config.config import config

    setuplogger()
    config.init("prod")

    sample = ["AAPL-US"]
    df_current = get_symbol_cross_reference(ticker_regions=sample)
    df_asof = get_symbol_cross_reference_asof(
        "2020-12-31",
        ticker_regions=sample,
    )
    logging.info(df_current.head().to_string())
    logging.info(df_asof.head().to_string())
