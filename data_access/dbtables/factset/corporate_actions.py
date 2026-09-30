import logging

from common.utils import dtutil, dfutil, sqlutil
from data_access.dbtables.factset import APP
from data_access.utils import tosqllist

CA_MAPPING_SQL = '''
WITH mapping AS (
  SELECT DISTINCT tr.ticker_region, sm.fsym_id AS fsym_id
  FROM FACTSET_DB.sym_v1.sym_ticker_region AS tr
  JOIN FACTSET_DB.FGP_V1.FGP_SEC_COVERAGE AS sm_regional
    ON tr.fsym_id = sm_regional.fsym_id
  JOIN FACTSET_DB.FGP_V1.FGP_SEC_COVERAGE AS sm
    ON sm.fsym_security_id = sm_regional.fsym_security_id
   AND sm.fref_exchange_code = sm_regional.fref_exchange_code
  WHERE tr.ticker_region IN {0}

  UNION

  SELECT DISTINCT tr.ticker_region, sm_regional.fsym_id AS fsym_id
  FROM FACTSET_DB.sym_v1.sym_ticker_region AS tr
  JOIN FACTSET_DB.FGP_V1.FGP_SEC_COVERAGE AS sm_regional
    ON tr.fsym_id = sm_regional.fsym_id
  WHERE tr.ticker_region IN {0}

  UNION

  SELECT DISTINCT tr.ticker_region, sc.fsym_security_id AS fsym_id
  FROM FACTSET_DB.sym_v1.sym_ticker_region AS tr
  JOIN FACTSET_DB.sym_v1.sym_coverage AS sc
    ON sc.fsym_id = tr.fsym_id
  WHERE tr.ticker_region IN {0}
)
'''

CA_EVENTS_SQL = (
    CA_MAPPING_SQL
    + '''
SELECT m.ticker_region, e.*
FROM FACTSET_DB.FGP_V1.FGP_CA_EVENTS AS e
JOIN mapping AS m
  ON e.fsym_id = m.fsym_id
WHERE e.{3} BETWEEN '{1}' AND '{2}'
ORDER BY m.ticker_region, e.{3};
'''
)

CA_ADJ_FACTORS_SQL = (
    CA_MAPPING_SQL
    + '''
SELECT m.ticker_region, f.*
FROM FACTSET_DB.FGP_V1.FGP_CA_ADJ_FACTORS AS f
JOIN mapping AS m
  ON f.fsym_id = m.fsym_id
WHERE f.effective_date BETWEEN '{1}' AND '{2}'
ORDER BY m.ticker_region, f.effective_date;
'''
)


def get_corporate_actions(ticker_regions, from_dt, to_dt):
    from_dt = dtutil.todate(from_dt)
    to_dt = dtutil.todate(to_dt)
    tickers = tosqllist(dfutil.tolist(ticker_regions))
    date_col = _resolve_ca_event_date_column()
    sql = CA_EVENTS_SQL.format(tickers, from_dt, to_dt, date_col)
    logging.info("fetching factset corporate actions")
    return sqlutil.readdfsql(sql, app=APP)


def get_adjustment_factors(ticker_regions, from_dt, to_dt):
    from_dt = dtutil.todate(from_dt)
    to_dt = dtutil.todate(to_dt)
    tickers = tosqllist(dfutil.tolist(ticker_regions))
    sql = CA_ADJ_FACTORS_SQL.format(tickers, from_dt, to_dt)
    logging.info("fetching factset corporate action adjustment factors")
    return sqlutil.readdfsql(sql, app=APP)


def _resolve_ca_event_date_column():
    candidates = ["event_date", "effective_date", "ex_date", "record_date", "announcement_date"]
    try:
        df = sqlutil.readdfsql(
            "SELECT column_name FROM FACTSET_DB.information_schema.columns "
            "WHERE table_schema='FGP_V1' AND table_name='FGP_CA_EVENTS'",
            app=APP,
        )
        cols = {str(c).lower() for c in df["COLUMN_NAME"].tolist()}
        for cand in candidates:
            if cand in cols:
                return cand
    except Exception:
        pass
    return "effective_date"


if __name__ == "__main__":
    from common.utils.logutil import setuplogger
    from apo_config.config import config

    setuplogger()
    config.init("prod")

    sample = ["AAPL-US"]
    df_events = get_corporate_actions(
        sample,
        dtutil.dateadd(dtutil.today(), "-5y"),
        dtutil.today(),
    )
    df_adj = get_adjustment_factors(
        sample,
        dtutil.dateadd(dtutil.today(), "-5y"),
        dtutil.today(),
    )
    logging.info(df_events.head().to_string())
    logging.info(df_adj.head().to_string())
