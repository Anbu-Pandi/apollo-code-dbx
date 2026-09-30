"""
CDS cash-CDS basis data layer (ground-up, tier-correct, 5Y maturity-matched).

Per constituent on each date:
    bond_z_5y_bps   = np.interp(5y, sorted bond maturities, bond z-spreads)  # tier-matched fixed-rate bonds
    cds_5y_bps      = markit_cds.conv_spread × 10000                          # at constituent_tier
    basis_bps       = cds_5y_bps − bond_z_5y_bps  (negative = bonds cheap vs CDS)

Index-level basis:
    ew_basis_bps    = mean(basis_bps over today's constituents with non-null bond+cds)

Membership is per-date (rolls handled correctly). All curves are tier-matched —
FinSub uses SUBLT2 bonds vs SUBLT2 CDS, FinSen uses SNRFOR/SNRLAC vs same.
"""
import logging

import numpy as np
import pandas as pd

from common.utils import dtutil, sqlutil
from dash_reports.cds_cash_basis.constants import INDEX_META
from data_access.dbtables import MARKIT_BOND_PRICING_APP, MARKIT_CDS_PRICING_APP
from data_access.dbtables.market_data.credit import cds_index
from data_access.utils import tosqllist

# Higher = preferred. Matches the ordering used in data_access cds_curve.py.
_DOC_CLAUSE_RANK = {'CR14': 0, 'XR14': 1, 'MR14': 2, 'CR': 0, 'XR': 1, 'MR': 2}

# Bonds with maturity outside this window are discarded before interpolation
# (avoids degenerate <1y money-market bonds and ultra-long perp-ish quotes).
_BOND_MIN_MATURITY_YRS = 1.0
_BOND_MAX_MATURITY_YRS = 30.0


# ── Constituent fetch ──────────────────────────────────────────────────────
def _index_constituents(fromdt, todt, index_ticker):
    """Daily constituent panel for an index over [fromdt, todt].

    Returns rows of (asof_date, constituent_red_code, constituent_tier,
    constituent_ticker, constituent_doc_clause, constituent_weight, …).
    """
    fromdt, todt = dtutil.todate(fromdt), dtutil.todate(todt)
    cons = cds_index.get_index_original_constituents(
        fromdt, todt, [index_ticker], max_series_version_only=True,
    )
    if cons.empty:
        return cons
    cons = cons[cons['constituent_weight'].fillna(0) > 0].copy()
    cons['asof_date'] = pd.to_datetime(cons['asof_date']).dt.normalize()
    return cons


# ── CDS panel ─────────────────────────────────────────────────────────────
def _fetch_cds_panel(red_codes, tiers, fromdt, todt):
    """Daily 5Y CDS spread per (red_code, tier). Deduped to best doc_clause."""
    if not red_codes or not tiers:
        return pd.DataFrame(columns=['asof_date', 'red_code', 'tier', 'cds_spread_bps'])
    sql = f"""
    SELECT cast(asof_date AS date) AS asof_date, red_code, tier, doc_clause,
           conv_spread * 10000 AS cds_spread_bps
    FROM markit_cds
    WHERE cast(asof_date AS date) BETWEEN '{dtutil.todate(fromdt)}' AND '{dtutil.todate(todt)}'
      AND tenor = '5Y' AND primary_coupon = 'Y'
      AND red_code IN {tosqllist(red_codes)}
      AND tier IN {tosqllist(tiers)}
    """
    df = sqlutil.readdfsql(sql, app=MARKIT_CDS_PRICING_APP)
    if df.empty:
        return df
    df['_rank'] = df['doc_clause'].map(lambda x: _DOC_CLAUSE_RANK.get(x, -1))
    df = (df.sort_values(['asof_date', 'red_code', 'tier', '_rank'])
          .drop_duplicates(subset=['asof_date', 'red_code', 'tier'], keep='last')
          .drop(columns=['_rank']))
    df['asof_date'] = pd.to_datetime(df['asof_date']).dt.normalize()
    df['cds_spread_bps'] = pd.to_numeric(df['cds_spread_bps'], errors='coerce')
    return df[['asof_date', 'red_code', 'tier', 'cds_spread_bps']]


# ── Bond panel: 5Y-interpolated z-spread (Python-side, robust) ─────────────
def _fetch_bond_z_5y(tickers, tiers, fromdt, todt):
    """One row per (asof_date, ticker, tier) with 5Y-interpolated bond z-spread.

    Pulls all qualifying fixed-rate bonds from SQL, then Python-side:
      - removes z-spread outliers per (date, ticker, tier) using IQR
      - interpolates the 5Y point via np.interp across the full maturity curve
    """
    if not tickers or not tiers:
        return pd.DataFrame(columns=['asof_date', 'ticker', 'tier',
                                     'bond_z_5y_bps', 'n_bonds'])
    min_days = int(_BOND_MIN_MATURITY_YRS * 365.25)
    max_days = int(_BOND_MAX_MATURITY_YRS * 365.25)
    z_min, z_max = -200, 5000
    sql = f"""
    SELECT cast(asof_date AS date) AS asof_date, ticker, tier,
           datediff(maturity, cast(asof_date AS date)) / 365.25 AS maturity_years,
           mid_z_spread AS z_spread_bps
    FROM markit_pricing_data
    WHERE cast(asof_date AS date) BETWEEN '{dtutil.todate(fromdt)}' AND '{dtutil.todate(todt)}'
      AND ticker IN {tosqllist(tickers)}
      AND tier IN {tosqllist(tiers)}
      AND mid_z_spread IS NOT NULL
      AND maturity IS NOT NULL
      AND mid_z_spread BETWEEN {z_min} AND {z_max}
      AND defaulted = 'N' AND perpetual = 'N'
      AND coupon_type = 'Fixed'
      AND datediff(maturity, cast(asof_date AS date)) BETWEEN {min_days} AND {max_days}
    """
    raw = sqlutil.readdfsql(sql, app=MARKIT_BOND_PRICING_APP)
    if raw.empty:
        return pd.DataFrame(columns=['asof_date', 'ticker', 'tier',
                                     'bond_z_5y_bps', 'n_bonds'])
    raw['asof_date'] = pd.to_datetime(raw['asof_date']).dt.normalize()
    raw['maturity_years'] = pd.to_numeric(raw['maturity_years'], errors='coerce')
    raw['z_spread_bps'] = pd.to_numeric(raw['z_spread_bps'], errors='coerce')
    raw = raw.dropna(subset=['maturity_years', 'z_spread_bps'])

    results = []
    for key, grp in raw.groupby(['asof_date', 'ticker', 'tier']):
        z5 = _interp_5y_robust(grp)
        if z5 is not None:
            results.append({
                'asof_date': key[0], 'ticker': key[1], 'tier': key[2],
                'bond_z_5y_bps': z5, 'n_bonds': len(grp),
            })
    if not results:
        return pd.DataFrame(columns=['asof_date', 'ticker', 'tier',
                                     'bond_z_5y_bps', 'n_bonds'])
    return pd.DataFrame(results)


def _interp_5y_robust(grp, target=5.0):
    """Interpolate the 5Y z-spread from a group of bonds, filtering outliers.

    Steps:
      1. Deduplicate at the same maturity by taking the median z-spread.
      2. Remove local outliers: any bond whose z-spread is >2.5× or <0.4× the
         median of its 4 nearest maturity neighbors is dropped.
      3. np.interp at target=5.0 using the cleaned curve.
    """
    g = grp[['maturity_years', 'z_spread_bps']].copy()
    if g.empty:
        return None

    # Deduplicate same-maturity bonds by median
    g = g.groupby('maturity_years', as_index=False).agg(z_spread_bps=('z_spread_bps', 'median'))
    g = g.sort_values('maturity_years').reset_index(drop=True)

    # Local outlier filter: compare each point to its nearest-maturity neighbors
    if len(g) >= 5:
        z = g['z_spread_bps'].values
        n = len(z)
        keep = np.ones(n, dtype=bool)
        k = min(4, n - 1)
        for i in range(n):
            dists = np.abs(np.arange(n) - i).astype(float)
            dists[i] = np.inf  # exclude self
            neighbor_idx = np.argsort(dists)[:k]
            neighbor_med = np.median(z[neighbor_idx])
            if neighbor_med > 0:
                ratio = z[i] / neighbor_med
                if ratio > 2.5 or ratio < 0.4:
                    keep[i] = False
        g = g[keep].reset_index(drop=True)

    if g.empty:
        return None

    mats = g['maturity_years'].values
    zspr = g['z_spread_bps'].values
    return float(np.interp(target, mats, zspr))


# ── Build per-name basis panel (historical) ───────────────────────────────
def _build_basis_panel(cons_history, fromdt, todt):
    """Merge constituent membership with CDS + bond-5y panels, compute basis.

    cons_history: daily (asof_date, constituent_red_code, constituent_tier,
                         constituent_ticker, constituent_weight, ...)
    Returns: rows of (asof_date, ticker, red_code, tier, cds_spread_bps,
                      bond_z_5y_bps, basis_bps, n_bonds, weight)
    """
    if cons_history is None or cons_history.empty:
        return pd.DataFrame()

    red_codes = cons_history['constituent_red_code'].dropna().unique().tolist()
    tiers = cons_history['constituent_tier'].dropna().unique().tolist()
    tickers = cons_history['constituent_ticker'].dropna().unique().tolist()

    cds = _fetch_cds_panel(red_codes, tiers, fromdt, todt)
    bonds_5y = _fetch_bond_z_5y(tickers, tiers, fromdt, todt)

    if cds.empty and bonds_5y.empty:
        return pd.DataFrame()

    keep = ['asof_date', 'constituent_red_code', 'constituent_tier',
            'constituent_ticker', 'constituent_weight']
    m = cons_history[keep].drop_duplicates().rename(columns={
        'constituent_red_code': 'red_code',
        'constituent_tier': 'tier',
        'constituent_ticker': 'ticker',
        'constituent_weight': 'weight',
    })
    m = m.merge(cds, on=['asof_date', 'red_code', 'tier'], how='left')
    m = m.merge(bonds_5y, on=['asof_date', 'ticker', 'tier'], how='left')

    m['basis_bps'] = pd.to_numeric(m['cds_spread_bps'], errors='coerce') \
                     - pd.to_numeric(m['bond_z_5y_bps'], errors='coerce')
    return m[['asof_date', 'ticker', 'red_code', 'tier', 'weight',
              'cds_spread_bps', 'bond_z_5y_bps', 'n_bonds', 'basis_bps']]


# ── Public API ────────────────────────────────────────────────────────────
def get_constituent_basis_history(fromdt, todt, index_ticker):
    """Per-date constituent-level basis panel (membership rolls handled)."""
    cons = _index_constituents(fromdt, todt, index_ticker)
    if cons.empty:
        return pd.DataFrame()
    panel = _build_basis_panel(cons, fromdt, todt)
    if not panel.empty:
        panel['index_ticker'] = index_ticker
    return panel


def get_constituent_basis(asof_date, index_ticker, panel=None):
    """Snapshot per-constituent basis at asof_date (with name + sector for display).

    If `panel` is provided (e.g. already fetched for a wider lookback), the snapshot
    is sliced from it and we avoid re-running the heavy bond/CDS SQL — only an
    extra cheap label-lookup query is issued.
    """
    dt = dtutil.todate(asof_date)
    if panel is None:
        panel = get_constituent_basis_history(dt, dt, index_ticker)
    if panel.empty:
        return pd.DataFrame()
    snap = panel[panel['asof_date'] == pd.Timestamp(dt).normalize()].copy()
    if snap.empty:
        return snap
    red_codes = snap['red_code'].dropna().unique().tolist()
    labels = _fetch_name_labels(red_codes, dt) if red_codes else pd.DataFrame()
    if not labels.empty:
        snap = snap.merge(labels, on='red_code', how='left')
    snap['name'] = snap.get('short_name', pd.Series(index=snap.index)).fillna(snap['ticker'])
    snap['index_ticker'] = index_ticker
    return snap


def _fetch_name_labels(red_codes, dt):
    """short_name + sector lookup per red_code at the snapshot date."""
    sql = f"""
    SELECT red_code, FIRST(short_name) AS short_name, FIRST(sector) AS sector
    FROM markit_cds
    WHERE cast(asof_date AS date) = '{dtutil.todate(dt)}'
      AND red_code IN {tosqllist(red_codes)}
    GROUP BY red_code
    """
    return sqlutil.readdfsql(sql, app=MARKIT_CDS_PRICING_APP)


# ── Aggregations ──────────────────────────────────────────────────────────
def ew_basis_timeseries(panel_df):
    """Equal-weight per-date basis from a constituent-level panel."""
    if panel_df is None or panel_df.empty:
        return pd.DataFrame(columns=['asof_date', 'ew_basis_bps', 'n'])
    d = panel_df.dropna(subset=['basis_bps'])
    if d.empty:
        return pd.DataFrame(columns=['asof_date', 'ew_basis_bps', 'n'])
    return (d.groupby('asof_date', as_index=False)
            .agg(ew_basis_bps=('basis_bps', 'mean'), n=('basis_bps', 'count'))
            .sort_values('asof_date'))


def equal_weight_basis(df):
    if df is None or df.empty or 'basis_bps' not in df.columns:
        return None
    s = pd.to_numeric(df['basis_bps'], errors='coerce').dropna()
    return float(s.mean()) if len(s) else None


def sector_basis(df):
    if df is None or df.empty or 'sector' not in df.columns:
        return pd.DataFrame()
    d = df.dropna(subset=['basis_bps', 'sector']).copy()
    if d.empty:
        return pd.DataFrame()
    return (d.groupby('sector', as_index=False)
            .agg(basis_bps=('basis_bps', 'mean'),
                 cds_spread_bps=('cds_spread_bps', 'mean'),
                 n=('basis_bps', 'count'))
            .sort_values('basis_bps'))


def _coverage_pct(names_df):
    if names_df is None or names_df.empty:
        return None
    total = len(names_df)
    has_basis = pd.to_numeric(names_df['basis_bps'], errors='coerce').notna().sum() if 'basis_bps' in names_df.columns else 0
    return float(has_basis) / total if total else None


def build_index_summary(asof_date, lookback):
    dt = dtutil.todate(asof_date)
    fromdt = dtutil.dateadd(dt, f'-{lookback}')
    history_per_index = {}
    rows = []
    for key, meta in INDEX_META.items():
        tkr = meta['tickers'][0]
        logging.info(f'[ccb] building index summary for {tkr} ({fromdt}..{dt})')
        panel = get_constituent_basis_history(fromdt, dt, tkr)
        ts = ew_basis_timeseries(panel)
        ts['ticker'] = tkr
        ts['label'] = meta['label']
        history_per_index[key] = ts
        names = get_constituent_basis(dt, tkr, panel=panel)  # slice; no new SQL
        rows.append({
            'key': key, 'label': meta['label'], 'region': meta['region'], 'ticker': tkr,
            'ew_basis_bps': equal_weight_basis(names),
            'coverage_pct': _coverage_pct(names),
            'names': len(names),
        })
    history = pd.concat(history_per_index.values(), ignore_index=True) if history_per_index else pd.DataFrame()
    return {
        'asof_date': dt, 'fromdt': fromdt,
        'history': history, 'summary': pd.DataFrame(rows),
    }


def load_index_pack(asof_date, lookback, index_key):
    meta = INDEX_META[index_key]
    dt = dtutil.todate(asof_date)
    fromdt = dtutil.dateadd(dt, f'-{lookback}')
    tkr = meta['tickers'][0]
    panel = get_constituent_basis_history(fromdt, dt, tkr)
    history = ew_basis_timeseries(panel)
    names = get_constituent_basis(dt, tkr, panel=panel)  # slice; no new SQL
    return {
        'meta': meta, 'key': index_key, 'history': history, 'panel': panel,
        'names': names, 'sectors': sector_basis(names),
        'ew_basis_bps': equal_weight_basis(names),
        'coverage_pct': _coverage_pct(names),
    }
