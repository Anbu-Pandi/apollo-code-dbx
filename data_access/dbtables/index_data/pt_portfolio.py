import logging
from typing import List

import pandas as pd

from common.utils import sqlutil
from data_access.dbtables import PT_PORTFOLIO_APP
from data_access.utils import get_where_clause, tosqllist


def get_cusip_isin_map(ids: List[str]) -> pd.DataFrame:
    cusips = [id for id in ids if len(id) == 9 and id.isalnum()]
    if not cusips:
        return pd.DataFrame()
    try:
        query = f"SELECT id_cusip, id_isin FROM dbo.v_bond_security_master WHERE id_cusip IN {tosqllist(cusips)}"
        return sqlutil.readdfsql(query, app=PT_PORTFOLIO_APP)
    except:
        return pd.DataFrame()


def get_existing_constituents(portfolio_names: List[str]) -> pd.DataFrame:
    if not portfolio_names:
        return pd.DataFrame()
    try:
        names_str = "', '".join(portfolio_names)
        query = f"""
        SELECT c.pt_portfolio_name, c.isin, c.qty
        FROM pt_portfolio_constituents c
        INNER JOIN (
            SELECT pt_portfolio_name, MAX(file_modified_date) as max_date
            FROM pt_portfolio_constituents
            WHERE pt_portfolio_name IN ('{names_str}')
            GROUP BY pt_portfolio_name
        ) latest ON c.pt_portfolio_name = latest.pt_portfolio_name 
               AND c.file_modified_date = latest.max_date
        """
        df = sqlutil.readdfsql(query, app=PT_PORTFOLIO_APP)
        df = df.drop_duplicates(subset=['pt_portfolio_name', 'isin'], keep='last')
        return df.reset_index(drop=True)
    except:
        return pd.DataFrame()


def mark_all_portfolios_not_live():
    try:
        sqlutil.execsql('UPDATE pt_portfolio SET is_live = 0', app=PT_PORTFOLIO_APP)
        logging.info("Marked all existing portfolios as is_live=0")
    except Exception as e:
        logging.error(f"Error marking portfolios as not live: {e}")


def get_all_constituents() -> pd.DataFrame:
    try:
        query = """
        SELECT c.pt_portfolio_name, c.isin, c.qty
        FROM pt_portfolio_constituents c
        INNER JOIN (
            SELECT pt_portfolio_name, MAX(file_modified_date) as max_date
            FROM pt_portfolio_constituents
            GROUP BY pt_portfolio_name
        ) latest ON c.pt_portfolio_name = latest.pt_portfolio_name 
               AND c.file_modified_date = latest.max_date
        """
        df = sqlutil.readdfsql(query, app=PT_PORTFOLIO_APP)
        df = df.drop_duplicates(subset=['pt_portfolio_name', 'isin'], keep='last')
        return df.reset_index(drop=True)
    except:
        return pd.DataFrame()


def save_portfolios(df: pd.DataFrame):
    if df.empty:
        return
    keys = (df['name'].astype(str) + '|' + df['file_modified_date'].astype(str)).drop_duplicates().tolist()
    where_clause = get_where_clause("concat(name, '|', file_modified_date)", keys)
    sqlutil.execsql('delete from pt_portfolio where ' + where_clause, app=PT_PORTFOLIO_APP)
    sqlutil.insertdf_batch('pt_portfolio', df, app=PT_PORTFOLIO_APP, maxrow_batch=5000)


def save_constituents(df: pd.DataFrame):
    if df.empty:
        return
    logging.info(f"Saving {len(df)} constituents")
    keys = (df['pt_portfolio_name'].astype(str) + '|' + df['file_modified_date'].astype(
        str)).drop_duplicates().tolist()
    where_clause = get_where_clause("concat(pt_portfolio_name, '|', file_modified_date)", keys)
    sqlutil.execsql('delete from pt_portfolio_constituents where ' + where_clause, app=PT_PORTFOLIO_APP)
    sqlutil.insertdf_batch('pt_portfolio_constituents', df, app=PT_PORTFOLIO_APP, maxrow_batch=5000)