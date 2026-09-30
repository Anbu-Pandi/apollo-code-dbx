"""Data layer for the GenAI Issues admin dashboard.

Reads the per-host Delta tables that GenAI hosts append to in the AzureDB shared
"local" dir (a network share, no cloud), unions them, and folds them by signature
via genai.observability.reader. Shaping is defensive so the page renders even
when there are no issue tables yet.

Access control uses the existing dash_app_access permission table (the same one
every restricted dash page uses); that is a permission row, not issue data.
"""

import pandas as pd

from common.utils.sqlutil import execsql, readdfsql
from data_access.dbtables import DASH_ACCESS_APP

APP_ACCESS_TABLE = 'dash_app_access'
# dash_app_access is a Databricks-managed Delta table (DeletionVectors); write it
# via the Databricks SQL warehouse. The dash still READS the synced blob copy
# (app='remote'); this is only the write/source-of-truth path.
ACCESS_WRITE_APP = 'databricks'

# dash_access_check does an exact link==pathname match and defaults OPEN for
# unlisted links, so we register both slash forms (the access-logs page is
# stored as '/dash-usage-report/' with a trailing slash). ADMIN_GROUP mirrors the
# real dash_app_access convention (app_Dash_Admin), which gates /dash-usage-report/.
DASHBOARD_LINK = '/genai-issues-report'
# both slash forms of every admin GenAI page (dash_access_check is exact-match)
DASHBOARD_LINKS = (
    '/dash-usage-report', '/dash-usage-report/',
    '/genai-issues-report', '/genai-issues-report/',
    '/genai-conversations', '/genai-conversations/',
    '/jobs',
    '/macro-data', '/macro-data/',
)
ADMIN_GROUP = 'app_Dash_Admin'

_OPEN_STATUSES = ('new', 'acknowledged', 'investigating')

# columns shown in the drill-down table (order matters), snake_case
_TABLE_COLUMNS = [
    'last_seen_at', 'issue_class', 'severity', 'category', 'domain', 'tool_name',
    'occurrence_count', 'error_summary', 'root_cause', 'suggestion',
    'run_id', 'session_id', 'status',
]
# only actionable signal is surfaced by default (user-error noise is excluded)
_SIGNAL_CLASSES = ('system', 'agent')


def get_genai_issues_raw() -> pd.DataFrame:
    """Distinct issues (one row per signature) across all hosts. Empty on miss."""
    try:
        from genai.observability import reader
        df = reader.aggregate_by_signature(reader.read_all_issues())
    except Exception:
        df = pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame(columns=_TABLE_COLUMNS)
    if 'last_seen_at' in df.columns:
        df['last_seen_at'] = pd.to_datetime(df['last_seen_at'], errors='coerce')
    return df


def get_genai_issue_summary(since_days: int = 30) -> dict:
    """Return the shaped pieces the page renders: kpis, trend, category, table."""
    df = get_genai_issues_raw()
    if df.empty:
        return {'kpis': _empty_kpis(), 'trend': pd.DataFrame(), 'by_category': pd.DataFrame(),
                'table': pd.DataFrame(columns=_TABLE_COLUMNS)}

    if since_days and 'last_seen_at' in df.columns:
        cutoff = pd.Timestamp.utcnow().tz_localize(None) - pd.Timedelta(days=since_days)
        df = df[df['last_seen_at'].fillna(cutoff) >= cutoff]

    # signal only — drop user-error noise from the whole page
    if 'issue_class' in df.columns:
        df = df[df['issue_class'].fillna('unknown').isin(_SIGNAL_CLASSES)]

    return {
        'kpis': _kpis(df),
        'trend': _trend(df),
        'by_category': _by_category(df),
        'table': _table(df),
    }


def _kpis(df: pd.DataFrame) -> dict:
    is_open = df['status'].isin(_OPEN_STATUSES) if 'status' in df.columns else pd.Series(True, index=df.index)
    cls = df['issue_class'].fillna('unknown') if 'issue_class' in df.columns else pd.Series('unknown', index=df.index)
    occ = df['occurrence_count'].fillna(1) if 'occurrence_count' in df.columns else pd.Series(1, index=df.index)
    crit = df['severity'].fillna('').isin(['critical', 'high']) if 'severity' in df.columns else pd.Series(False, index=df.index)
    resolved = (~is_open).sum()
    return {
        'distinct_issues': int(len(df)),
        'total_occurrences': int(occ.sum()),
        'open': int(is_open.sum()),
        'system': int((cls == 'system').sum()),
        'agent': int((cls == 'agent').sum()),
        'critical_high': int((crit & is_open).sum()),
        'resolved': int(resolved),
    }


def _empty_kpis() -> dict:
    return {'distinct_issues': 0, 'total_occurrences': 0, 'open': 0,
            'system': 0, 'agent': 0, 'critical_high': 0, 'resolved': 0}


def _trend(df: pd.DataFrame) -> pd.DataFrame:
    """Occurrences per day by severity (wide), most recent last — for a stacked chart."""
    if 'last_seen_at' not in df.columns or df['last_seen_at'].isna().all():
        return pd.DataFrame()
    d = df.copy()
    d['day'] = d['last_seen_at'].dt.date
    d['severity'] = d.get('severity', 'unknown').fillna('unknown')
    pivot = d.pivot_table(index='day', columns='severity', values='occurrence_count',
                          aggfunc='sum', fill_value=0)
    pivot = pivot.sort_index()
    pivot.reset_index(inplace=True)
    return pivot


def _by_category(df: pd.DataFrame) -> pd.DataFrame:
    if 'category' not in df.columns:
        return pd.DataFrame()
    d = df.copy()
    d['category'] = d['category'].fillna('unknown')
    occ = d['occurrence_count'].fillna(1) if 'occurrence_count' in d.columns else 1
    out = d.assign(occ=occ).groupby('category', as_index=False)['occ'].sum()
    out = out.sort_values('occ', ascending=False).rename(columns={'occ': 'occurrences'})
    return out


def _table(df: pd.DataFrame) -> pd.DataFrame:
    cols = [c for c in _TABLE_COLUMNS if c in df.columns]
    d = df[cols].copy()
    # open first, then most severe, then most recent
    sev_rank = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3, 'unknown': 4}
    if 'severity' in d.columns:
        d['_sev'] = d['severity'].map(sev_rank).fillna(5)
    if 'status' in d.columns:
        d['_open'] = (~d['status'].isin(_OPEN_STATUSES)).astype(int)
    sort_cols = [c for c in ['_open', '_sev', 'last_seen_at'] if c in d.columns]
    ascending = [True, True, False][:len(sort_cols)]
    if sort_cols:
        d = d.sort_values(sort_cols, ascending=ascending)
    return d.drop(columns=[c for c in ['_sev', '_open'] if c in d.columns]).head(200)


# ── access control ──────────────────────────────────────────────────────

def get_dash_app_access() -> pd.DataFrame:
    """Blob Delta copy the dash reads for gating (may lag the Synapse source)."""
    return readdfsql(f'select * from {APP_ACCESS_TABLE}', app=DASH_ACCESS_APP)


def _read_access_source() -> pd.DataFrame:
    """Read dash_app_access from the Databricks source of truth for an accurate
    idempotency check. readdfsql(app='remote') is special-cased to the synced blob
    copy, which lags — so we read the same store execsql writes to (databricks)."""
    return readdfsql(
        f'select ad_group, link, access_level from {APP_ACCESS_TABLE}',
        app=ACCESS_WRITE_APP)


def ensure_access_control() -> int:
    """Register read-access rows restricting the dashboard to ADMIN_GROUP.

    dash_access_check does an exact link==pathname match and defaults OPEN for
    links with no read row, so these rows are what actually restrict the page.
    Both slash forms are registered so either request path is gated. Idempotent —
    only the missing variants are inserted. Returns the number of rows added.

    Writes via execsql to the Databricks SQL warehouse (ACCESS_WRITE_APP): neither
    insert_df_to_db's batch path (SQL-Server `select top 1` on DuckDB) nor a
    deltalake merge (DeletionVectors) can write this Databricks-managed table.
    """
    try:
        existing = _read_access_source()
    except Exception:
        existing = pd.DataFrame(columns=['ad_group', 'link', 'access_level'])

    def _present(link: str) -> bool:
        if existing is None or existing.empty:
            return False
        return bool((
            (existing['link'] == link)
            & (existing['ad_group'] == ADMIN_GROUP)
            & (existing['access_level'] == 'read')
        ).any())

    to_add = [link for link in DASHBOARD_LINKS if not _present(link)]
    for link in to_add:
        sql = (
            f"INSERT INTO {APP_ACCESS_TABLE} (ad_group, link, access_level) "
            f"VALUES ('{ADMIN_GROUP}', '{link}', 'read')"
        )
        execsql(sql, app=ACCESS_WRITE_APP)
    return len(to_add)


if __name__ == '__main__':
    from apo_config.config import config
    config.init('prod')
    print(get_genai_issue_summary()['kpis'])
