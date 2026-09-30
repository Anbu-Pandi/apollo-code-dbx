import logging

import numpy as np
import pandas as pd

TABLE = 'dbo.security_ts_hist'
TICKERS = {'HYG US Equity': 'HYG UP', 'LQD US Equity': 'LQD UP',
           'SPX Index': 'SPX', 'IWM US Equity': 'IWM UP'}
SPOT_FIELDS = {'PX_LAST': 'spot_price', 'YAS_MOD_DUR': 'duration', 'YAS_YLD_SPREAD': 'spot_spread'}
RETURN_FIELD = 'DAY_TO_DAY_TOT_RETURN_GROSS_DVDS'
FIELDS = [*SPOT_FIELDS, RETURN_FIELD]
KEYS = ['sec_id', 'attribute', 'frequency', 'asof_date', 'source']
REQUIRED = {(ticker.upper(), field) for ticker in TICKERS
            for field in (FIELDS if ticker.startswith(('HYG', 'LQD')) else ['PX_LAST', RETURN_FIELD])}


def from_bloomberg(raw):
    records = []
    for (ticker, field), values in raw.items():
        if ticker.upper() not in {t.upper() for t in TICKERS} or field not in FIELDS:
            raise ValueError(f'Unexpected Bloomberg input: {ticker} / {field}')
        records.append(pd.DataFrame({'sec_id': ticker.upper(), 'attribute': field,
                                     'frequency': 'DAILY', 'source': 'BBG',
                                     'asof_date': pd.to_datetime(values.index).normalize(),
                                     'value': pd.to_numeric(values, errors='raise').to_numpy()}))
    frame = pd.concat(records, ignore_index=True) if records else pd.DataFrame(columns=[*KEYS, 'value'])
    frame = frame.dropna(subset=['value'])
    if not np.isfinite(frame.value.astype(float)).all():
        raise ValueError('Bloomberg returned non-finite values')
    frame = frame.drop_duplicates()
    if frame.duplicated(KEYS).any():
        raise ValueError('Conflicting Bloomberg values for the same input/date')
    return frame


def save(frame):
    from common.utils.sqlutil import execsql
    frame = frame[[*KEYS, 'value']]
    if frame.empty:
        raise ValueError('No Bloomberg observations to save')
    if frame.duplicated(KEYS).any():
        raise ValueError('Duplicate Bloomberg input/date keys')
    rows = []
    for ticker, field, frequency, day, source, value in frame.itertuples(index=False, name=None):
        if (ticker not in {t.upper() for t in TICKERS} or field not in FIELDS
                or frequency != 'DAILY' or source != 'BBG' or not np.isfinite(value) or pd.isna(day)):
            raise ValueError('Invalid Bloomberg observation')
        rows.append(f"('{ticker}', '{field}', 'DAILY', DATE '{pd.Timestamp(day):%Y-%m-%d}', 'BBG', {float(value)!r})")
    columns = ', '.join([*KEYS, 'value'])
    match = ' AND '.join(f't.{key} = s.{key}' for key in KEYS)
    dates = pd.to_datetime(frame.asof_date)
    match += f" AND t.asof_date BETWEEN DATE '{dates.min():%Y-%m-%d}' AND DATE '{dates.max():%Y-%m-%d}'"
    values = ',\n'.join(rows)
    sql = (f'MERGE INTO {TABLE} AS t USING (SELECT * FROM VALUES {values} AS v ({columns})) AS s ON {match} '
           'WHEN MATCHED THEN UPDATE SET value = s.value '
           f'WHEN NOT MATCHED THEN INSERT ({columns}) VALUES ('
           + ', '.join(f's.{col}' for col in [*KEYS, 'value']) + ')')
    execsql(sql, app='databricks', msg='Save CDX Bloomberg inputs')
    logging.info('Saved %s Bloomberg observations, %s through %s', len(frame),
                 frame.asof_date.min().date(), frame.asof_date.max().date())


def read(start, end, fields=None):
    from common.utils.sqlutil import readdfsql
    fields = list(fields or FIELDS)
    if not set(fields) <= set(FIELDS):
        raise ValueError('Unknown Bloomberg input fields')
    tickers_sql = ', '.join(f"'{ticker.upper()}'" for ticker in TICKERS)
    fields_sql = ', '.join(f"'{field}'" for field in fields)
    frame = readdfsql(
        f'SELECT upper(sec_id) AS sec_id, asof_date, upper(attribute) AS attribute, value FROM {TABLE} '
        f'WHERE upper(sec_id) IN ({tickers_sql}) AND upper(attribute) IN ({fields_sql}) '
        "AND upper(frequency) = 'DAILY' AND upper(source) = 'BBG' AND value IS NOT NULL "
        f"AND asof_date BETWEEN DATE '{pd.Timestamp(start):%Y-%m-%d}' AND DATE '{pd.Timestamp(end):%Y-%m-%d}'",
        app='databricks')
    frame['asof_date'] = pd.to_datetime(frame.asof_date).dt.normalize()
    missing = {(ticker, field) for ticker, field in REQUIRED if field in fields} - set(zip(frame.sec_id, frame.attribute))
    if missing:
        raise ValueError('Saved Bloomberg inputs are missing for this range: '
                         + ', '.join(f'{t}/{f}' for t, f in sorted(missing))
                         + '. Run the local CDX Bloomberg loader with --start and --end for this range.')
    if frame.duplicated(['sec_id', 'attribute', 'asof_date']).any():
        raise ValueError('Duplicate saved Bloomberg input/date keys')
    return frame


def equity_spots(start, end):
    frame = read(start, end, SPOT_FIELDS)
    result = frame.pivot(index=['asof_date', 'sec_id'], columns='attribute', values='value')
    result = result.reindex(columns=SPOT_FIELDS).rename(columns=SPOT_FIELDS).reset_index()
    result['bbg_id'] = result.pop('sec_id').map({t.upper(): name for t, name in TICKERS.items()})
    return result


def equity_returns(start, end):
    frame = read(start, end, [RETURN_FIELD])
    result = frame.pivot(index='asof_date', columns='sec_id', values='value') / 100
    return result.rename(columns={t.upper(): name.removesuffix(' UP') for t, name in TICKERS.items()}).sort_index()
