import logging
import time
from contextlib import contextmanager
from threading import Lock
from typing import Optional

import blpapi
import pandas as pd
import pdblp
import pytz

from common.utils import dtutil
from common.utils.dfutil import tolist

# Global connection instance and metadata
_bbg_conn: Optional[pdblp.BCon] = None
_bbg_conn_last_used: float = 0
_bbg_conn_lock = Lock()
_BBG_CONN_TTL = 300  # Time to live in seconds (5 minutes)


def get_bbg_conn() -> pdblp.BCon:
    """
    Returns a Bloomberg connection, reusing an existing connection if possible.
    The connection will be kept alive for _BBG_CONN_TTL seconds since last use.
    """
    global _bbg_conn, _bbg_conn_last_used

    with _bbg_conn_lock:
        current_time = time.time()

        # Create new connection if none exists or if existing one has expired
        if _bbg_conn is None or (current_time - _bbg_conn_last_used > _BBG_CONN_TTL):
            if _bbg_conn is not None:
                try:
                    _bbg_conn.stop()
                except Exception as e:
                    logging.warning(f"Error stopping stale Bloomberg connection: {e}")

            logging.info("Creating new Bloomberg connection")
            _bbg_conn = pdblp.BCon(timeout=10 * 60 * 1000)
            _bbg_conn.start()

        # Update last used timestamp
        _bbg_conn_last_used = current_time

        return _bbg_conn


@contextmanager
def with_bbg_conn() -> pdblp.BCon:
    """
    Context manager for safely using the Bloomberg connection.
    Does not close the connection after use, just updates the last used timestamp.
    """
    try:
        conn = get_bbg_conn()
        yield conn
    except Exception as e:
        logging.error(f"Error with Bloomberg connection: {e}")
        # Force reconnection on next use
        global _bbg_conn
        with _bbg_conn_lock:
            if _bbg_conn is not None:
                try:
                    _bbg_conn.stop()
                except Exception:
                    pass
                _bbg_conn = None
        raise


def shutdown_bbg_conn():
    """Explicitly shutdown the Bloomberg connection if it exists."""
    global _bbg_conn
    with _bbg_conn_lock:
        if _bbg_conn is not None:
            try:
                _bbg_conn.stop()
            except Exception as e:
                logging.warning(f"Error stopping Bloomberg connection: {e}")
            _bbg_conn = None


def bbg_ref_request(tickers, attrib, ovrds=None):
    logging.info(f"bbg ref request for attribute: {attrib}, tickers: {tickers}")
    with with_bbg_conn() as conn:
        df = conn.ref(tolist(tickers), attrib, ovrds)
    return df


def bbg_ref_hist_request(tickers, attrib, dates):
    logging.info(f"bbg ref request for attribute: {attrib}, tickers: {tickers}, dates: {dates}")
    with with_bbg_conn() as conn:
        df = conn.ref_hist(tolist(tickers), attrib, dates=dates)
    return df


def _unstack_bbg_bulkdf(df, uniqcols=[]):
    df = df[uniqcols + ['ticker', 'name', 'value']].drop_duplicates()
    df = df.set_index(uniqcols + ['ticker', 'name'])[['value']] \
        .unstack() \
        .dropna(how='all', axis=0) \
        .reset_index() \
        .dropna(how='all', axis=1)
    df.columns = [x[1] if x[0] == 'value' else x[0] for x in df.columns]
    return df


def bbg_bulkref_request(tickers, attrib, ovrds=None, unstack=True):
    logging.info(f"bbg bulk ref request for attribute: {attrib}, ovrds: {ovrds},tickers: {tickers}")
    with with_bbg_conn() as conn:
        df = conn.bulkref(list(set(tolist(tickers))), attrib, ovrds)
        if unstack:
            df = _unstack_bbg_bulkdf(df)
    return df


def bbg_bdh_request(tickers, attrib, start_date, end_date, flatten_cols=True, **kwargs):
    logging.info(
        f"bbg bdh request for attribute: {attrib}, tickers: {tickers}, startdate: {start_date}, enddate: {end_date}")
    start_date = dtutil.todatestr(start_date)
    end_date = dtutil.todatestr(end_date)
    with with_bbg_conn() as conn:
        df = conn.bdh(tolist(tickers), attrib, start_date, end_date, **kwargs)

    if flatten_cols:
        df.columns = ['_'.join(tolist(k)) for k in df.columns]
    return df


def bbg_bds_request(securities, fields, start_date=None, end_date=None):
    """
    Fetch bulk data from Bloomberg using BDS (Bloomberg Data Set).

    Parameters:
    - securities (list): List of Bloomberg tickers (e.g., ["FDTR Index"]).
    - fields (list): List of Bloomberg bulk fields (e.g., ["ECO_FUTURE_RELEASE_DATE_LIST"]).
    - start_date (str): Optional start date (e.g., "20250101").
    - end_date (str): Optional end date (e.g., "20251231").

    Returns:
    - pd.DataFrame: DataFrame with extracted Bloomberg bulk data.
    """
    # Note: This function uses direct blpapi rather than pdblp
    # We could rewrite it to use the connection pool, but for now
    # we'll keep its original implementation

    session_options = blpapi.SessionOptions()
    session_options.setServerHost('localhost')  # Adjust if needed
    session_options.setServerPort(8194)  # Bloomberg default API port

    session = blpapi.Session(session_options)
    if not session.start():
        raise ConnectionError("Failed to start Bloomberg session")

    try:
        session.openService("//blp/refdata")
        ref_data_service = session.getService("//blp/refdata")

        request = ref_data_service.createRequest("ReferenceDataRequest")

        for security in securities:
            request.append("securities", security)
        for field in fields:
            request.append("fields", field)

        # Add optional overrides for date filtering
        if start_date and end_date:
            overrides = request.getElement("overrides")
            start_override = overrides.appendElement()
            start_override.setElement("fieldId", "START_DT")
            start_override.setElement("value", start_date)

            end_override = overrides.appendElement()
            end_override.setElement("fieldId", "END_DT")
            end_override.setElement("value", end_date)

        session.sendRequest(request)

        # Process response
        data_list = []
        while True:
            event = session.nextEvent()
            for msg in event:
                if msg.hasElement("securityData"):
                    security_data_array = msg.getElement("securityData")
                    for sec_data in security_data_array.values():
                        security_name = sec_data.getElementAsString("security")
                        field_data = sec_data.getElement("fieldData")

                        for field in fields:
                            if field_data.hasElement(field):
                                bulk_data = field_data.getElement(field)
                                for i in range(bulk_data.numValues()):
                                    row = {
                                        "Security": security_name,
                                        "Field": field,
                                        "Value": bulk_data.getValueAsElement(i).getElementAsString("value")
                                    }
                                    data_list.append(row)

            if event.eventType() == blpapi.Event.RESPONSE:
                break

        # Convert to DataFrame
        df = pd.DataFrame(data_list)

        if df.empty:
            logging.warning("No data returned from Bloomberg BDS request.")

        return df
    finally:
        session.stop()


def bbg_intraday(security, event_type, start_datetime, end_datetime, interval=1, tz='America/New_York'):
    start_datetime_local = pd.to_datetime(start_datetime).tz_localize(tz)
    end_datetime_local = pd.to_datetime(end_datetime).tz_localize(tz)

    # Similar to bbg_bds_request, this uses direct blpapi
    session_options = blpapi.SessionOptions()
    session_options.setServerHost("localhost")
    session_options.setServerPort(8194)
    session = blpapi.Session(session_options)

    if not session.start():
        print("Failed to start session.")
        return pd.DataFrame()

    try:
        if not session.openService("//blp/refdata"):
            print("Failed to open //blp/refdata")
            return pd.DataFrame()

        ref_data_service = session.getService("//blp/refdata")
        request = ref_data_service.createRequest("IntradayBarRequest")
        request.set("security", security)
        request.set("eventType", event_type)
        request.set("interval", interval)  # Bar interval in minutes
        request.set("startDateTime", start_datetime_local)
        request.set("endDateTime", end_datetime_local)

        session.sendRequest(request)

        # Collect bars in a list
        bars = []
        while True:
            event = session.nextEvent()
            if event.eventType() in [blpapi.Event.RESPONSE, blpapi.Event.PARTIAL_RESPONSE]:
                for msg in event:
                    for bar in msg.getElement("barData").getElement("barTickData").values():
                        bar_time = bar.getElementAsDatetime("time")
                        open_price = bar.getElementAsFloat("open")
                        high_price = bar.getElementAsFloat("high")
                        low_price = bar.getElementAsFloat("low")
                        close_price = bar.getElementAsFloat("close")
                        volume = bar.getElementAsInteger("volume")

                        bars.append([bar_time, open_price, high_price, low_price, close_price, volume])

            if event.eventType() == blpapi.Event.RESPONSE:
                break

        # Convert list to DataFrame
        columns = ["DateTime", "Open", "High", "Low", "Close", "Volume"]
        df = pd.DataFrame(bars, columns=columns)
        df['DateTime'] = pd.to_datetime(df['DateTime']) \
            .dt.tz_localize(pytz.utc) \
            .dt.tz_convert(tz) \
            .dt.tz_localize(None)
        return df
    finally:
        session.stop()


def has_bbg():
    global _bbg_conn
    with _bbg_conn_lock:
        if _bbg_conn is not None and (time.time() - _bbg_conn_last_used <= _BBG_CONN_TTL):
            try:
                # Simple test request with existing connection
                _bbg_conn.ref(["SPX Index"], ["PX_LAST"])
                return True
            except Exception:
                # Existing connection failed, will try a new one
                pass

    # No valid existing connection, try to establish a new one
    try:
        # Check if the Bloomberg service is running by attempting a minimal connection
        session_options = blpapi.SessionOptions()
        session_options.setServerHost("localhost")
        session_options.setServerPort(8194)
        session = blpapi.Session(session_options)

        if session.start():
            session.stop()
            return True
        return False
    except Exception as e:
        logging.info(f"Bloomberg API unavailable: {e}")
        return False


if __name__ == '__main__':
    try:
        tickers = ['SPY US EQUITY', 'AAPL US EQUITY']
        dt = dtutil.today()
        end_dt = dtutil.dateadd(dt, '-1y')

        overrides = [
            ('BETA_OVERRIDE_START_DT', end_dt.strftime('%Y%m%d')),
            ('BETA_OVERRIDE_END_DT', dt.strftime('%Y%m%d')),
            ('BETA_OVERRIDE_PERIOD', 'W'),
            ('BETA_OVERRIDE_REL_INDEX', 'SPX Index')
        ]

        attrib = 'BETA_RAW_OVERRIDABLE'
        df = bbg_bdh_request(tickers, attrib, dt, dt, ovrds=overrides)

        print(df)
    finally:
        # Make sure to shutdown connection when script exits
        shutdown_bbg_conn()
