import logging

import pandas as pd

from common.utils.sqlutil import readdfsql
from data_access.dbtables import SENSORTOWER_APP
from data_access.utils import get_where_clause, join_where_clauses

CAT = 'snowflake_sensortower_prod'
_DIM = f'{CAT}.dim'
_CORE = f'{CAT}.core'


def _date_clause(col, start_date=None, end_date=None):
    parts = []
    if start_date:
        parts.append(f"{col} >= DATE '{start_date}'")
    if end_date:
        parts.append(f"{col} <= DATE '{end_date}'")
    return ' AND '.join(parts) if parts else ''


def _build_where(clauses):
    valid = [c for c in clauses if c]
    return ('WHERE ' + ' AND '.join(valid)) if valid else ''


def _keys_in(keys):
    return ', '.join(f"'{k}'" for k in keys)


def _resolve_apps_cte(unified_product_keys):
    keys_sql = _keys_in(unified_product_keys)
    return f"""
    dca_raw AS (
        SELECT DISTINCT UNIFIED_PRODUCT_KEY, UNIFIED_PRODUCT_NAME, APP_ID, PRODUCT_KEY
        FROM {_DIM}.dim_company_app
        WHERE UNIFIED_PRODUCT_KEY IN ({keys_sql})
    ),
    dca AS (
        SELECT UNIFIED_PRODUCT_KEY, UNIFIED_PRODUCT_NAME, APP_ID, PRODUCT_KEY
        FROM (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY APP_ID ORDER BY UNIFIED_PRODUCT_KEY) AS rn
            FROM dca_raw
        ) WHERE rn = 1
    )"""


# ─────────────────────────────────────────────────
#  DIM lookups
# ─────────────────────────────────────────────────

def get_apps(unified_product_keys=None, search=None):
    sql = f"""
    SELECT DISTINCT
        UNIFIED_PRODUCT_KEY, UNIFIED_PRODUCT_NAME, APP_ID,
        PRODUCT_NAME, PRODUCT_MARKET_CODE, PRODUCT_DEVICE_NAME,
        PUBLISHER_NAME, COMPANY_NAME, COMPANY_STOCK_SYMBOL,
        PRODUCT_UNIFIED_CATEGORY_NAME, PRODUCT_FIRST_RELEASE_DATE,
        WORLDWIDE_RELEASE_DATE
    FROM {_DIM}.dim_company_app
    """
    clauses = []
    if unified_product_keys:
        clauses.append(f"UNIFIED_PRODUCT_KEY IN ({_keys_in(unified_product_keys)})")
    if search:
        clauses.append(f"(UPPER(UNIFIED_PRODUCT_NAME) LIKE UPPER('%{search}%') "
                       f"OR UPPER(PRODUCT_NAME) LIKE UPPER('%{search}%') "
                       f"OR UPPER(PUBLISHER_NAME) LIKE UPPER('%{search}%'))")
    sql += _build_where(clauses)
    sql += "\nORDER BY UNIFIED_PRODUCT_NAME, PRODUCT_MARKET_CODE"

    logging.info('sensortower: get_apps')
    df = readdfsql(sql, app=SENSORTOWER_APP)
    df.columns = [c.lower() for c in df.columns]
    return df


def get_countries():
    sql = f"SELECT * FROM {_DIM}.dim_country ORDER BY COUNTRY_NAME"
    logging.info('sensortower: get_countries')
    df = readdfsql(sql, app=SENSORTOWER_APP)
    df.columns = [c.lower() for c in df.columns]
    return df


def get_categories():
    sql = f"SELECT * FROM {_DIM}.dim_category ORDER BY CATEGORY_NAME"
    logging.info('sensortower: get_categories')
    df = readdfsql(sql, app=SENSORTOWER_APP)
    df.columns = [c.lower() for c in df.columns]
    return df


# ─────────────────────────────────────────────────
#  Downloads & Revenue  (daily / monthly)
# ─────────────────────────────────────────────────

def get_downloads_revenue(unified_product_keys, start_date=None, end_date=None,
                          country='WW', frequency='daily'):
    cte = _resolve_apps_cte(unified_product_keys)
    freq = frequency.lower()
    ios_table = f'{_CORE}.app_raw_download_revenue_ios_{freq}'
    android_table = f'{_CORE}.app_raw_download_revenue_android_{freq}'

    date_filter = _date_clause('DATE', start_date, end_date)
    country_filter = f"COUNTRY_CODE = '{country}'" if country else ''
    where_parts = [p for p in [date_filter, country_filter] if p]
    where = ('WHERE ' + ' AND '.join(where_parts)) if where_parts else ''

    sql = f"""
    WITH {cte},
    platform AS (
        SELECT APP_ID, DATE, COUNTRY_CODE,
               SUM(EST_DOWNLOAD) AS EST_DOWNLOAD,
               SUM(EST_REVENUE)  AS EST_REVENUE
        FROM {ios_table}
        {where}
        GROUP BY 1,2,3

        UNION ALL

        SELECT APP_ID, DATE, COUNTRY_CODE,
               SUM(EST_DOWNLOAD) AS EST_DOWNLOAD,
               SUM(EST_REVENUE)  AS EST_REVENUE
        FROM {android_table}
        {where}
        GROUP BY 1,2,3
    )
    SELECT dca.UNIFIED_PRODUCT_KEY, dca.UNIFIED_PRODUCT_NAME, p.DATE, p.COUNTRY_CODE,
           SUM(p.EST_DOWNLOAD) AS DOWNLOADS,
           SUM(p.EST_REVENUE)  AS REVENUE
    FROM dca
    JOIN platform p ON dca.APP_ID = p.APP_ID
    GROUP BY 1,2,3,4
    ORDER BY DATE, UNIFIED_PRODUCT_NAME
    """

    logging.info(f'sensortower: get_downloads_revenue ({freq}, country={country})')
    df = readdfsql(sql, app=SENSORTOWER_APP)
    df.columns = [c.lower() for c in df.columns]
    df['date'] = pd.to_datetime(df['date'])
    return df


def get_downloads_revenue_by_platform(unified_product_keys, start_date=None, end_date=None,
                                      country='WW'):
    cte = _resolve_apps_cte(unified_product_keys)
    date_filter = _date_clause('DATE', start_date, end_date)
    country_filter = f"COUNTRY_CODE = '{country}'" if country else ''
    where_parts = [p for p in [date_filter, country_filter] if p]
    where = ('WHERE ' + ' AND '.join(where_parts)) if where_parts else ''

    sql = f"""
    WITH {cte},
    platform AS (
        SELECT APP_ID, DATE, COUNTRY_CODE, 'iOS' AS PLATFORM,
               SUM(EST_DOWNLOAD) AS EST_DOWNLOAD,
               SUM(EST_REVENUE)  AS EST_REVENUE
        FROM {_CORE}.app_raw_download_revenue_ios_daily
        {where}
        GROUP BY 1,2,3,4

        UNION ALL

        SELECT APP_ID, DATE, COUNTRY_CODE, 'Android' AS PLATFORM,
               SUM(EST_DOWNLOAD) AS EST_DOWNLOAD,
               SUM(EST_REVENUE)  AS EST_REVENUE
        FROM {_CORE}.app_raw_download_revenue_android_daily
        {where}
        GROUP BY 1,2,3,4
    )
    SELECT dca.UNIFIED_PRODUCT_KEY, dca.UNIFIED_PRODUCT_NAME, p.DATE, p.COUNTRY_CODE, p.PLATFORM,
           SUM(p.EST_DOWNLOAD) AS DOWNLOADS,
           SUM(p.EST_REVENUE)  AS REVENUE
    FROM dca
    JOIN platform p ON dca.APP_ID = p.APP_ID
    GROUP BY 1,2,3,4,5
    ORDER BY DATE, UNIFIED_PRODUCT_NAME, PLATFORM
    """

    logging.info(f'sensortower: get_downloads_revenue_by_platform (country={country})')
    df = readdfsql(sql, app=SENSORTOWER_APP)
    df.columns = [c.lower() for c in df.columns]
    df['date'] = pd.to_datetime(df['date'])
    return df


# ─────────────────────────────────────────────────
#  Active Users  (daily / weekly / monthly)
# ─────────────────────────────────────────────────

def get_active_users(unified_product_keys, start_date=None, end_date=None,
                     country='WW', frequency='daily'):
    cte = _resolve_apps_cte(unified_product_keys)
    freq = frequency.lower()
    ios_table = f'{_CORE}.app_raw_active_users_ios_{freq}'
    android_table = f'{_CORE}.app_raw_active_users_android_{freq}'

    date_filter = _date_clause('DATE', start_date, end_date)
    country_filter = f"COUNTRY_CODE = '{country}'" if country else ''
    where_parts = [p for p in [date_filter, country_filter] if p]
    where = ('WHERE ' + ' AND '.join(where_parts)) if where_parts else ''

    sql = f"""
    WITH {cte},
    platform AS (
        SELECT APP_ID, DATE, COUNTRY_CODE,
               SUM(EST_ACTIVE_USERS) AS EST_ACTIVE_USERS
        FROM {ios_table}
        {where}
        GROUP BY 1,2,3

        UNION ALL

        SELECT APP_ID, DATE, COUNTRY_CODE,
               SUM(EST_ACTIVE_USERS) AS EST_ACTIVE_USERS
        FROM {android_table}
        {where}
        GROUP BY 1,2,3
    )
    SELECT dca.UNIFIED_PRODUCT_KEY, dca.UNIFIED_PRODUCT_NAME, p.DATE, p.COUNTRY_CODE,
           SUM(p.EST_ACTIVE_USERS) AS ACTIVE_USERS
    FROM dca
    JOIN platform p ON dca.APP_ID = p.APP_ID
    GROUP BY 1,2,3,4
    ORDER BY DATE, UNIFIED_PRODUCT_NAME
    """

    logging.info(f'sensortower: get_active_users ({freq}, country={country})')
    df = readdfsql(sql, app=SENSORTOWER_APP)
    df.columns = [c.lower() for c in df.columns]
    df['date'] = pd.to_datetime(df['date'])
    return df


# ─────────────────────────────────────────────────
#  Session Metrics  (daily / weekly / monthly)
# ─────────────────────────────────────────────────

def get_session_metrics(unified_product_keys, start_date=None, end_date=None,
                        country='WW', frequency='daily'):
    cte = _resolve_apps_cte(unified_product_keys)
    freq = frequency.lower()
    ios_table = f'{_CORE}.app_raw_session_metrics_estimates_ios_{freq}'
    android_table = f'{_CORE}.app_raw_session_metrics_estimates_android_{freq}'

    date_filter = _date_clause('DATE', start_date, end_date)
    country_filter = f"COUNTRY_CODE = '{country}'" if country else ''
    where_parts = [p for p in [date_filter, country_filter] if p]
    where = ('WHERE ' + ' AND '.join(where_parts)) if where_parts else ''

    sql = f"""
    WITH {cte},
    platform AS (
        SELECT APP_ID, DATE, COUNTRY_CODE,
               AVG(AVERAGE_DAILY_SESSION_COUNT) AS AVG_DAILY_SESSION_COUNT,
               AVG(AVERAGE_DAILY_TIME_SPENT) AS AVG_DAILY_TIME_SPENT,
               SUM(EST_TOTAL_SESSION_COUNT) AS TOTAL_SESSION_COUNT,
               SUM(EST_TOTAL_TIME_SPENT) AS TOTAL_TIME_SPENT,
               AVG(AVERAGE_SESSION_DURATION) AS AVG_SESSION_DURATION
        FROM {ios_table}
        {where}
        GROUP BY 1,2,3

        UNION ALL

        SELECT APP_ID, DATE, COUNTRY_CODE,
               AVG(AVERAGE_DAILY_SESSION_COUNT),
               AVG(AVERAGE_DAILY_TIME_SPENT),
               SUM(EST_TOTAL_SESSION_COUNT),
               SUM(EST_TOTAL_TIME_SPENT),
               AVG(AVERAGE_SESSION_DURATION)
        FROM {android_table}
        {where}
        GROUP BY 1,2,3
    )
    SELECT dca.UNIFIED_PRODUCT_KEY, dca.UNIFIED_PRODUCT_NAME, p.DATE, p.COUNTRY_CODE,
           AVG(p.AVG_DAILY_SESSION_COUNT) AS AVG_DAILY_SESSIONS,
           AVG(p.AVG_DAILY_TIME_SPENT) AS AVG_DAILY_TIME_SPENT,
           SUM(p.TOTAL_SESSION_COUNT) AS TOTAL_SESSIONS,
           SUM(p.TOTAL_TIME_SPENT) AS TOTAL_TIME_SPENT,
           AVG(p.AVG_SESSION_DURATION) AS AVG_SESSION_DURATION
    FROM dca
    JOIN platform p ON dca.APP_ID = p.APP_ID
    GROUP BY 1,2,3,4
    ORDER BY DATE, UNIFIED_PRODUCT_NAME
    """

    logging.info(f'sensortower: get_session_metrics ({freq}, country={country})')
    df = readdfsql(sql, app=SENSORTOWER_APP)
    df.columns = [c.lower() for c in df.columns]
    df['date'] = pd.to_datetime(df['date'])
    return df


# ─────────────────────────────────────────────────
#  App Store Rankings
# ─────────────────────────────────────────────────

def get_rankings(unified_product_keys, start_date=None, end_date=None,
                 country='US', category=None):
    cte = _resolve_apps_cte(unified_product_keys)
    date_filter = _date_clause('r.DATE', start_date, end_date)
    country_filter = f"r.COUNTRY_CODE = '{country}'" if country else ''
    cat_filter = f"r.CATEGORY_CODE = '{category}'" if category else ''
    where_parts = [p for p in [date_filter, country_filter, cat_filter] if p]
    where = ('WHERE ' + ' AND '.join(where_parts)) if where_parts else ''

    sql = f"""
    WITH {cte}
    SELECT dca.UNIFIED_PRODUCT_KEY, dca.UNIFIED_PRODUCT_NAME,
           r.DATE, r.COUNTRY_CODE, r.CATEGORY_NAME, r.DEVICE_CODE,
           r.RANK_FREE, r.RANK_PAID, r.RANK_GROSSING
    FROM dca
    JOIN {_CORE}.app_store_rankings r ON dca.APP_ID = r.APP_ID
    {where}
    ORDER BY r.DATE, dca.UNIFIED_PRODUCT_NAME
    """

    logging.info(f'sensortower: get_rankings (country={country})')
    df = readdfsql(sql, app=SENSORTOWER_APP)
    df.columns = [c.lower() for c in df.columns]
    df['date'] = pd.to_datetime(df['date'])
    return df


# ─────────────────────────────────────────────────
#  App Store Ratings
# ─────────────────────────────────────────────────

def get_ratings(unified_product_keys, start_date=None, end_date=None, country='US'):
    cte = _resolve_apps_cte(unified_product_keys)
    date_filter = _date_clause('rt.DATE', start_date, end_date)
    country_filter = f"rt.COUNTRY_CODE = '{country}'" if country else ''
    where_parts = [p for p in [date_filter, country_filter] if p]
    where = ('WHERE ' + ' AND '.join(where_parts)) if where_parts else ''

    sql = f"""
    WITH {cte}
    SELECT dca.UNIFIED_PRODUCT_KEY, dca.UNIFIED_PRODUCT_NAME,
           rt.DATE, rt.COUNTRY_CODE, rt.DEVICE_CODE,
           rt.AVERAGE_STAR_CUMULATIVE, rt.TOTAL_COUNT_CUMULATIVE,
           rt.FIVE_STAR_CUMULATIVE, rt.FOUR_STAR_CUMULATIVE,
           rt.THREE_STAR_CUMULATIVE, rt.TWO_STAR_CUMULATIVE, rt.ONE_STAR_CUMULATIVE,
           rt.AVERAGE_STAR_INCREMENTAL, rt.TOTAL_COUNT_INCREMENTAL
    FROM dca
    JOIN {_CORE}.app_store_ratings rt ON dca.APP_ID = rt.APP_ID
    {where}
    ORDER BY rt.DATE, dca.UNIFIED_PRODUCT_NAME
    """

    logging.info(f'sensortower: get_ratings (country={country})')
    df = readdfsql(sql, app=SENSORTOWER_APP)
    df.columns = [c.lower() for c in df.columns]
    df['date'] = pd.to_datetime(df['date'])
    return df


# ─────────────────────────────────────────────────
#  App Store Reviews
# ─────────────────────────────────────────────────

def get_reviews(unified_product_keys, start_date=None, end_date=None,
                country=None, min_rating=None, max_rating=None):
    cte = _resolve_apps_cte(unified_product_keys)
    date_filter = _date_clause('rv.DATE', start_date, end_date)
    country_filter = f"rv.COUNTRY_CODE = '{country}'" if country else ''
    rating_min = f"rv.RATING_VALUE >= {min_rating}" if min_rating else ''
    rating_max = f"rv.RATING_VALUE <= {max_rating}" if max_rating else ''
    where_parts = [p for p in [date_filter, country_filter, rating_min, rating_max] if p]
    where = ('WHERE ' + ' AND '.join(where_parts)) if where_parts else ''

    sql = f"""
    WITH {cte}
    SELECT dca.UNIFIED_PRODUCT_KEY, dca.UNIFIED_PRODUCT_NAME,
           rv.DATE, rv.COUNTRY_CODE, rv.RATING_VALUE,
           rv.TITLE, rv.CONTENT, rv.PRODUCT_VERSION, rv.DEVICE_CODE
    FROM dca
    JOIN {_CORE}.app_store_reviews rv ON dca.APP_ID = rv.APP_ID
    {where}
    ORDER BY rv.DATE DESC
    """

    logging.info(f'sensortower: get_reviews (country={country})')
    df = readdfsql(sql, app=SENSORTOWER_APP)
    df.columns = [c.lower() for c in df.columns]
    df['date'] = pd.to_datetime(df['date'])
    return df


# ─────────────────────────────────────────────────
#  Retention
# ─────────────────────────────────────────────────

def get_retention(unified_product_keys, start_date=None, end_date=None,
                  country='WW', frequency='daily'):
    cte = _resolve_apps_cte(unified_product_keys)
    freq = frequency.lower()
    date_filter = _date_clause('ret.DATE', start_date, end_date)
    country_filter = f"ret.COUNTRY_CODE = '{country}'" if country else ''
    where_parts = [p for p in [date_filter, country_filter] if p]
    where = ('WHERE ' + ' AND '.join(where_parts)) if where_parts else ''

    sql = f"""
    WITH {cte}
    SELECT dca.UNIFIED_PRODUCT_KEY, dca.UNIFIED_PRODUCT_NAME,
           ret.DATE, ret.COUNTRY_CODE, ret.DEVICE_CODE,
           ret.EST_RETENTION_D1, ret.EST_RETENTION_D7,
           ret.EST_RETENTION_D14, ret.EST_RETENTION_D30,
           ret.EST_RETENTION_D60, ret.EST_RETENTION_D90,
           ret.EST_RETENTION_D180, ret.EST_RETENTION_D365
    FROM dca
    JOIN {_CORE}.app_retentions_{freq} ret ON dca.APP_ID = ret.APP_ID
    {where}
    ORDER BY ret.DATE, dca.UNIFIED_PRODUCT_NAME
    """

    logging.info(f'sensortower: get_retention ({freq}, country={country})')
    df = readdfsql(sql, app=SENSORTOWER_APP)
    df.columns = [c.lower() for c in df.columns]
    df['date'] = pd.to_datetime(df['date'])
    return df


# ─────────────────────────────────────────────────
#  Download Channels (organic vs paid breakdown)
# ─────────────────────────────────────────────────

def get_download_channels(unified_product_keys, start_date=None, end_date=None, country='WW'):
    cte = _resolve_apps_cte(unified_product_keys)
    date_filter = _date_clause('dc.DATE', start_date, end_date)
    country_filter = f"dc.COUNTRY_CODE = '{country}'" if country else ''
    where_parts = [p for p in [date_filter, country_filter] if p]
    where = ('WHERE ' + ' AND '.join(where_parts)) if where_parts else ''

    sql = f"""
    WITH {cte}
    SELECT dca.UNIFIED_PRODUCT_KEY, dca.UNIFIED_PRODUCT_NAME,
           dc.DATE, dc.COUNTRY_CODE, dc.DEVICE_CODE,
           dc.EST_DOWNLOAD, dc.EST_ORGANIC_DOWNLOAD, dc.EST_PAID_DOWNLOAD,
           dc.EST_ORGANIC_BROWSE_DOWNLOAD, dc.EST_ORGANIC_SEARCH_DOWNLOAD,
           dc.EST_PAID_IN_APP_ADS_DOWNLOAD, dc.EST_PAID_SEARCH_DOWNLOAD,
           dc.EST_WEB_BROWSER_DOWNLOAD
    FROM dca
    JOIN {_CORE}.app_download_channel dc ON dca.APP_ID = dc.APP_ID
    {where}
    ORDER BY dc.DATE, dca.UNIFIED_PRODUCT_NAME
    """

    logging.info(f'sensortower: get_download_channels (country={country})')
    df = readdfsql(sql, app=SENSORTOWER_APP)
    df.columns = [c.lower() for c in df.columns]
    df['date'] = pd.to_datetime(df['date'])
    return df


# ─────────────────────────────────────────────────
#  Install Metrics (penetration, install base)
# ─────────────────────────────────────────────────

def get_install_metrics(unified_product_keys, start_date=None, end_date=None, country='WW'):
    cte = _resolve_apps_cte(unified_product_keys)
    date_filter = _date_clause('im.DATE', start_date, end_date)
    country_filter = f"im.COUNTRY_CODE = '{country}'" if country else ''
    where_parts = [p for p in [date_filter, country_filter] if p]
    where = ('WHERE ' + ' AND '.join(where_parts)) if where_parts else ''

    sql = f"""
    WITH {cte}
    SELECT dca.UNIFIED_PRODUCT_KEY, dca.UNIFIED_PRODUCT_NAME,
           im.DATE, im.COUNTRY_CODE, im.DEVICE_CODE,
           im.EST_USAGE_PENETRATION, im.EST_INSTALL_PENETRATION,
           im.EST_INSTALL_BASE, im.EST_OPEN_RATE
    FROM dca
    JOIN {_CORE}.app_install_metrics im ON dca.APP_ID = im.APP_ID
    {where}
    ORDER BY im.DATE, dca.UNIFIED_PRODUCT_NAME
    """

    logging.info(f'sensortower: get_install_metrics (country={country})')
    df = readdfsql(sql, app=SENSORTOWER_APP)
    df.columns = [c.lower() for c in df.columns]
    df['date'] = pd.to_datetime(df['date'])
    return df


# ─────────────────────────────────────────────────
#  App Performance (wide table with everything)
# ─────────────────────────────────────────────────

def get_performance(unified_product_keys, start_date=None, end_date=None,
                    country='WW', granularity='daily'):
    cte = _resolve_apps_cte(unified_product_keys)
    date_filter = _date_clause('ap.DATE', start_date, end_date)
    country_filter = f"ap.COUNTRY_CODE = '{country}'" if country else ''
    gran_filter = f"ap.GRANULARITY_CODE = '{granularity}'" if granularity else ''
    where_parts = [p for p in [date_filter, country_filter, gran_filter] if p]
    where = ('WHERE ' + ' AND '.join(where_parts)) if where_parts else ''

    sql = f"""
    WITH {cte}
    SELECT dca.UNIFIED_PRODUCT_KEY, dca.UNIFIED_PRODUCT_NAME,
           ap.DATE, ap.COUNTRY_CODE, ap.DEVICE_CODE, ap.GRANULARITY_CODE,
           ap.EST_DOWNLOAD, ap.EST_REVENUE, ap.EST_ACTIVE_USERS,
           ap.EST_FREE_APP_DOWNLOAD, ap.EST_PAID_APP_DOWNLOAD
    FROM dca
    JOIN {_CORE}.app_performance ap ON dca.APP_ID = ap.APP_ID
    {where}
    ORDER BY ap.DATE, dca.UNIFIED_PRODUCT_NAME
    """

    logging.info(f'sensortower: get_performance ({granularity}, country={country})')
    df = readdfsql(sql, app=SENSORTOWER_APP)
    df.columns = [c.lower() for c in df.columns]
    df['date'] = pd.to_datetime(df['date'])
    return df


# ─────────────────────────────────────────────────
#  SDK Intelligence
# ─────────────────────────────────────────────────

def get_sdks(unified_product_keys, installed_only=True):
    cte = _resolve_apps_cte(unified_product_keys)
    install_filter = "AND sdk.INSTALL_STATUS = TRUE" if installed_only else ''

    sql = f"""
    WITH {cte}
    SELECT dca.UNIFIED_PRODUCT_KEY, dca.UNIFIED_PRODUCT_NAME,
           sdk.SDK_NAME, sdk.SDK_TYPES, sdk.DEVICE_CODE,
           sdk.INSTALL_DATE, sdk.UNINSTALL_DATE, sdk.DAYS_INSTALLED,
           sdk.INSTALL_STATUS, sdk.EST_DOWNLOADS, sdk.EST_REVENUE
    FROM dca
    JOIN {_CORE}.app_sdk sdk ON dca.PRODUCT_KEY = sdk.PRODUCT_KEY
    WHERE 1=1 {install_filter}
    ORDER BY dca.UNIFIED_PRODUCT_NAME, sdk.SDK_NAME
    """

    logging.info('sensortower: get_sdks')
    df = readdfsql(sql, app=SENSORTOWER_APP)
    df.columns = [c.lower() for c in df.columns]
    return df


# ─────────────────────────────────────────────────
#  Cross-App Usage / Affinity
# ─────────────────────────────────────────────────

def get_cross_app_usage(unified_product_keys, start_date=None, end_date=None, country='US'):
    keys_sql = _keys_in(unified_product_keys)
    date_filter = _date_clause('DATE', start_date, end_date)
    country_filter = f"COUNTRY_CODE = '{country}'" if country else ''
    where_parts = [f"UNIFIED_APP_ID IN ({keys_sql})"]
    where_parts += [p for p in [date_filter, country_filter] if p]
    where = 'WHERE ' + ' AND '.join(where_parts)

    sql = f"""
    SELECT UNIFIED_APP_ID, CROSS_UNIFIED_APP_ID,
           DATE, COUNTRY_CODE, DEVICE_CODE,
           EST_USAGE_PENETRATION, EST_CROSS_AFFINITY
    FROM {_CORE}.unified_app_cross_usage
    {where}
    ORDER BY DATE, EST_CROSS_AFFINITY DESC
    """

    logging.info(f'sensortower: get_cross_app_usage (country={country})')
    df = readdfsql(sql, app=SENSORTOWER_APP)
    df.columns = [c.lower() for c in df.columns]
    df['date'] = pd.to_datetime(df['date'])
    return df


# ─────────────────────────────────────────────────
#  In-App Purchases
# ─────────────────────────────────────────────────

def get_iap(unified_product_keys, country=None):
    cte = _resolve_apps_cte(unified_product_keys)
    country_filter = f"AND iap.COUNTRY_CODE = '{country}'" if country else ''

    sql = f"""
    WITH {cte}
    SELECT dca.UNIFIED_PRODUCT_KEY, dca.UNIFIED_PRODUCT_NAME,
           iap.COUNTRY_CODE, iap.DEVICE_CODE,
           iap.IAP_TITLE, iap.IAP_PRICE,
           iap.DATE_FIRST_SEEN, iap.DATE_LAST_SEEN,
           iap.EST_IAP_REVENUE, iap.EST_IAP_VOLUME
    FROM dca
    JOIN {_CORE}.app_iap iap ON dca.PRODUCT_KEY = iap.PRODUCT_KEY
    WHERE 1=1 {country_filter}
    ORDER BY dca.UNIFIED_PRODUCT_NAME, iap.IAP_TITLE
    """

    logging.info('sensortower: get_iap')
    df = readdfsql(sql, app=SENSORTOWER_APP)
    df.columns = [c.lower() for c in df.columns]
    return df
