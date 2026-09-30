"""Lossless, parameterized Delta writes for website evidence and holdings."""

import logging
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal

from databricks import sql
import numpy as np
import pandas as pd
from common.utils.sqlutil import getconn


SqlParameter = str | int | float | bool | date | datetime | Decimal | bytes | None
SqlRow = tuple[SqlParameter, ...]
MAX_PARAMETER_PAYLOAD = 750_000  # Headroom below the warehouse's 1 MiB parameter limit.
MAX_PARAMETERS = 5000


def _sql_parameter(value: object) -> SqlParameter:
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    if value is None or isinstance(value, (str, int, float, bool, date, datetime, Decimal, bytes)):
        return value
    raise TypeError(f'Unsupported website SQL parameter type: {type(value).__name__}')


def _parameter_batches(rows: tuple[SqlRow, ...], row_limit: int) -> Iterator[tuple[SqlRow, ...]]:
    sizes = [sum(len(value) if isinstance(value, bytes) else len(str(value).encode('utf-8'))
                 for value in row) + 64 * len(row) for row in rows]
    oversized = next((index for index, size in enumerate(sizes) if size > MAX_PARAMETER_PAYLOAD), None)
    if oversized is not None:
        raise ValueError(f'Website evidence row {oversized} exceeds the SQL payload limit; use bulk ingestion')
    start, size = 0, 0
    for index, row_size in enumerate(sizes):
        if index > start and (index - start >= row_limit or size + row_size > MAX_PARAMETER_PAYLOAD):
            yield rows[start:index]
            start, size = index, 0
        size += row_size
    if start < len(rows):
        yield rows[start:]


def read_website_rows(statement: str, *, app: str) -> pd.DataFrame:
    """Preserve timestamp precision and decimals for publication verification."""
    with getconn(app) as connection:
        with connection.cursor() as cursor:
            cursor.execute(statement)
            frame = cursor.fetchall_arrow().to_pandas()
    logging.info("Read website verification rows=%s", len(frame))
    return frame


def insert_website_rows(table: str, frame: pd.DataFrame, *, app: str, maxrow_batch: int, msg: str, cleantable: bool = False) -> None:
    if cleantable:
        raise ValueError("Website inserts cannot clear a whole table")
    if maxrow_batch < 1:
        raise ValueError('Website batch size must be positive')
    if frame.empty:
        return
    if len(frame.columns) > MAX_PARAMETERS:
        raise ValueError('Website row exceeds the SQL parameter count limit')
    columns = ', '.join('`' + str(column).replace('`', '``') + '`' for column in frame.columns)
    marker = '?' if int(sql.__version__.split('.')[0]) >= 3 else '%s'
    row_marker = '(' + ', '.join(marker for _ in frame.columns) + ')'
    batch_size = min(maxrow_batch, MAX_PARAMETERS // len(frame.columns))
    values = frame.astype(object).where(pd.notna(frame), None)
    rows = tuple(tuple(_sql_parameter(value) for value in row)
                 for row in values.itertuples(index=False, name=None))
    batches = tuple(_parameter_batches(rows, batch_size))
    with getconn(app) as connection:
        with connection.cursor() as cursor:
            inserted = 0
            for batch in batches:
                parameters = [value for row in batch for value in row]
                statement = f'INSERT INTO {table} ({columns}) VALUES ' + ', '.join(row_marker for _ in range(len(batch)))
                cursor.execute(statement, parameters=parameters)
                inserted += len(batch)
                logging.info('%s rows=%s/%s', msg, inserted, len(frame))


def update_website_row(table: str, row: pd.Series, *, keys: tuple[str, ...], app: str) -> None:
    """Update one control row atomically, including its publication marker."""
    if not keys or any(pd.isna(row[key]) for key in keys):
        raise ValueError("A control update requires non-null keys")
    marker = '?' if int(sql.__version__.split('.')[0]) >= 3 else '%s'
    columns = [column for column in row.index if column not in keys]
    quoted = lambda name: '`' + name.replace('`', '``') + '`'
    assignments = ', '.join(f'{quoted(column)} = {marker}' for column in columns)
    predicate = ' AND '.join(f'{quoted(key)} = {marker}' for key in keys)
    parameters = [None if pd.isna(row[column]) else _sql_parameter(row[column])
                  for column in [*columns, *keys]]
    with getconn(app) as connection:
        with connection.cursor() as cursor:
            cursor.execute(f'UPDATE {table} SET {assignments} WHERE {predicate}', parameters=parameters)
