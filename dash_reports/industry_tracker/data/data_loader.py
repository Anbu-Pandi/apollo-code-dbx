import json
import logging
import os
from datetime import datetime
from typing import Optional, Union

import pandas as pd

from common.utils import dtutil
from common.utils.sqlutil import readdfsql
from data_access.storage import atomic_file
from dash_reports.industry_tracker.constants import (
    DATA_APP, CREDIT_LOAN_APP, CREDIT_BOND_APP, get_cache_dir,
    PARQUET_PUBLIC_COMPS, PARQUET_LT_HISTORICAL,
    PARQUET_CREDIT_LOANS, PARQUET_CREDIT_BONDS,
    PARQUET_EARNINGS_CALLS, PARQUET_EARNINGS_EPS,
    JSON_WWR_WWO, JSON_MACRO_ALTD, JSON_EARNINGS_COMMENTARY,
)
from dash_reports.industry_tracker.data import earnings_calls as _earnings_calls
from dash_reports.industry_tracker.data.universe import Industry, get_industry, INDUSTRY_UNIVERSE


_SQL_DIR = os.path.join(os.path.dirname(__file__), 'sql')
_SEEDS_DIR = os.path.join(os.path.dirname(__file__), 'seeds')


def _seed_wwr_wwo_if_missing(industry_key: str):
    folder = get_cache_dir(industry_key)
    target = os.path.join(folder, JSON_WWR_WWO)
    if os.path.exists(target):
        return
    seed_path = os.path.join(_SEEDS_DIR, f'{industry_key}_wwr_wwo.json')
    if not os.path.exists(seed_path):
        return
    with open(seed_path, 'r', encoding='utf-8') as f:
        payload = json.load(f)
    atomic_file.write_json(payload, target, indent=2, ensure_ascii=False)
    logging.info(f'seeded WWR/WWO from {seed_path} -> {target}')


def _load_sql(name: str) -> str:
    with open(os.path.join(_SQL_DIR, name), 'r', encoding='utf-8') as f:
        return f.read()


def _format_universe_values(industry: Industry) -> str:
    rows = []
    for sg, tr, name in industry.all_tickers():
        sg_e = sg.replace("'", "''")
        tr_e = tr.replace("'", "''")
        nm_e = name.replace("'", "''")
        rows.append(f"('{sg_e}', '{tr_e}', '{nm_e}')")
    return ',\n        '.join(rows)


def _build_sql(industry: Industry, sql_name: str) -> str:
    template = _load_sql(sql_name)
    return template.format(universe_values=_format_universe_values(industry))


def fetch_public_comps(industry: Industry) -> pd.DataFrame:
    sql = _build_sql(industry, 'public_comps.sql')
    df = readdfsql(sql, app=DATA_APP, msg=f'industry_tracker:{industry.key}:public_comps')
    df.columns = [c.lower() for c in df.columns]
    return df


def fetch_lt_historical(industry: Industry) -> pd.DataFrame:
    sql = _build_sql(industry, 'lt_historical.sql')
    df = readdfsql(sql, app=DATA_APP, msg=f'industry_tracker:{industry.key}:lt_historical')
    df.columns = [c.lower() for c in df.columns]
    if 'qtr_end' in df.columns:
        df['qtr_end'] = pd.to_datetime(df['qtr_end'], errors='coerce')
    return df


def _ticker_sql_list(industry: Industry) -> str:
    tickers = industry.credit_tickers()
    if not tickers:
        return "''"
    return ', '.join("'" + t.replace("'", "''") + "'" for t in tickers)


def _annotate_sub_group(df: pd.DataFrame, industry: Industry) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    sg_map = industry.credit_sub_group_for_ticker()
    df['sub_group'] = df['issuer_ticker'].map(sg_map)
    return df


def fetch_credit_loans(industry: Industry) -> pd.DataFrame:
    ticker_list = _ticker_sql_list(industry)
    sql = _load_sql('credit_loans.sql').format(ticker_list=ticker_list)
    df = readdfsql(sql, app=CREDIT_LOAN_APP, msg=f'industry_tracker:{industry.key}:credit_loans')
    df.columns = [c.lower() for c in df.columns]
    if 'maturity_date' in df.columns:
        df['maturity_date'] = pd.to_datetime(df['maturity_date'], errors='coerce')
    if 'asof_date' in df.columns:
        df['asof_date'] = pd.to_datetime(df['asof_date'], errors='coerce')
    return _annotate_sub_group(df, industry)


def fetch_credit_bonds(industry: Industry) -> pd.DataFrame:
    ticker_list = _ticker_sql_list(industry)
    sql = _load_sql('credit_bonds.sql').format(ticker_list=ticker_list)
    df = readdfsql(sql, app=CREDIT_BOND_APP, msg=f'industry_tracker:{industry.key}:credit_bonds')
    df.columns = [c.lower() for c in df.columns]
    if 'maturity' in df.columns:
        df['maturity'] = pd.to_datetime(df['maturity'], errors='coerce')
    if 'markit_date' in df.columns:
        df['markit_date'] = pd.to_datetime(df['markit_date'], errors='coerce')
    return _annotate_sub_group(df, industry)


def fetch_earnings_calls(industry: Industry, days_back: int = 30,
                         n_per_bucket: int = 4) -> pd.DataFrame:
    return _earnings_calls.fetch_earnings_calls(industry, days_back=days_back,
                                                n_per_bucket=n_per_bucket)


def fetch_earnings_eps(industry: Industry) -> pd.DataFrame:
    sql = _build_sql(industry, 'earnings_eps.sql')
    df = readdfsql(sql, app=DATA_APP, msg=f'industry_tracker:{industry.key}:earnings_eps')
    df.columns = [c.lower() for c in df.columns]
    if 'fiscal_period_end' in df.columns:
        df['fiscal_period_end'] = pd.to_datetime(df['fiscal_period_end'], errors='coerce')
    return df


def generate_earnings_commentary(df: pd.DataFrame) -> dict:
    return _earnings_calls.generate_earnings_commentary(df)


def write_parquet(df: pd.DataFrame, industry_key: str, filename: str) -> str:
    folder = get_cache_dir(industry_key)
    path = os.path.join(folder, filename)
    atomic_file.write_parquet(df, path, index=False)
    logging.info(f'wrote {path} shape={df.shape}')
    return path


def read_parquet_safe(industry_key: str, filename: str) -> Optional[pd.DataFrame]:
    folder = get_cache_dir(industry_key)
    path = os.path.join(folder, filename)
    if not os.path.exists(path):
        logging.info(f'parquet not found: {path}')
        return None
    df = pd.read_parquet(path)
    logging.info(f'read {path} shape={df.shape}')
    return df


def read_json_safe(industry_key: str, filename: str) -> Optional[dict]:
    folder = get_cache_dir(industry_key)
    path = os.path.join(folder, filename)
    if not os.path.exists(path):
        return None
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def write_json(payload: dict, industry_key: str, filename: str) -> str:
    folder = get_cache_dir(industry_key)
    path = os.path.join(folder, filename)
    atomic_file.write_json(payload, path, indent=2, ensure_ascii=False)
    logging.info(f'wrote {path}')
    return path


def get_data_asof(industry_key: str) -> Optional[datetime]:
    folder = get_cache_dir(industry_key)
    path = os.path.join(folder, PARQUET_PUBLIC_COMPS)
    if not os.path.exists(path):
        return None
    return datetime.fromtimestamp(os.path.getmtime(path))


def _resolve_industry(industry: Union[str, Industry]) -> Industry:
    if isinstance(industry, Industry):
        return industry
    resolved = get_industry(industry)
    if resolved is None:
        raise ValueError(f'unknown industry: {industry}')
    return resolved


def refresh_industry_data(industry: Union[str, Industry]) -> dict:
    ind = _resolve_industry(industry)
    logging.info(f'industry_tracker refresh: {ind.key} ({ind.display_name})')

    pc_df = fetch_public_comps(ind)
    write_parquet(pc_df, ind.key, PARQUET_PUBLIC_COMPS)

    lt_df = fetch_lt_historical(ind)
    write_parquet(lt_df, ind.key, PARQUET_LT_HISTORICAL)

    out = {
        'industry': ind.key,
        'public_comps_rows': int(len(pc_df)),
        'lt_historical_rows': int(len(lt_df)),
        'asof': dtutil.today().strftime('%Y-%m-%d'),
    }

    if ind.credit_tickers():
        try:
            cl_df = fetch_credit_loans(ind)
            write_parquet(cl_df, ind.key, PARQUET_CREDIT_LOANS)
            out['credit_loans_rows'] = int(len(cl_df))
        except Exception:
            logging.exception(f'credit_loans refresh failed for {ind.key}')
        try:
            cb_df = fetch_credit_bonds(ind)
            write_parquet(cb_df, ind.key, PARQUET_CREDIT_BONDS)
            out['credit_bonds_rows'] = int(len(cb_df))
        except Exception:
            logging.exception(f'credit_bonds refresh failed for {ind.key}')

    return out


def refresh_earnings_calls(industry: Union[str, Industry], days_back: int = 30,
                           n_per_bucket: int = 4) -> dict:
    ind = _resolve_industry(industry)
    df = fetch_earnings_calls(ind, days_back=days_back, n_per_bucket=n_per_bucket)
    write_parquet(df, ind.key, PARQUET_EARNINGS_CALLS)
    commentary = generate_earnings_commentary(df)
    write_json(commentary, ind.key, JSON_EARNINGS_COMMENTARY)
    eps_rows = 0
    try:
        eps_df = fetch_earnings_eps(ind)
        write_parquet(eps_df, ind.key, PARQUET_EARNINGS_EPS)
        eps_rows = int(len(eps_df))
    except Exception:
        logging.exception(f'earnings_eps refresh failed for {ind.key}')
    return {
        'industry': ind.key,
        'earnings_calls_rows': int(len(df)),
        'issuers': int(df['ticker_region'].nunique() if len(df) else 0),
        'commentary_themes': int(len(commentary.get('theme_takeaways') or {})),
        'eps_rows': eps_rows,
        'asof': dtutil.today().strftime('%Y-%m-%d'),
    }


def refresh_all_industries() -> list:
    out = []
    for key in INDUSTRY_UNIVERSE:
        try:
            out.append(refresh_industry_data(key))
        except Exception:
            logging.exception(f'refresh failed for industry={key}')
    return out


def load_industry_bundle(industry_key: str) -> dict:
    _seed_wwr_wwo_if_missing(industry_key)
    raw_wwr_wwo = read_json_safe(industry_key, JSON_WWR_WWO) or {}
    return {
        'public_comps': read_parquet_safe(industry_key, PARQUET_PUBLIC_COMPS),
        'lt_historical': read_parquet_safe(industry_key, PARQUET_LT_HISTORICAL),
        'credit_loans': read_parquet_safe(industry_key, PARQUET_CREDIT_LOANS),
        'credit_bonds': read_parquet_safe(industry_key, PARQUET_CREDIT_BONDS),
        'earnings_calls': read_parquet_safe(industry_key, PARQUET_EARNINGS_CALLS),
        'earnings_eps': read_parquet_safe(industry_key, PARQUET_EARNINGS_EPS),
        'earnings_commentary': read_json_safe(industry_key, JSON_EARNINGS_COMMENTARY),
        'wwr': raw_wwr_wwo.get('wwr') or [],
        'wwo': raw_wwr_wwo.get('wwo') or [],
        'macro_altd': read_json_safe(industry_key, JSON_MACRO_ALTD),
        'asof': get_data_asof(industry_key),
    }
