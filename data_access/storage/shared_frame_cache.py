from __future__ import annotations

import io
import time

import pandas as pd
import pyarrow as pa
from deltalake import DeltaTable, write_deltalake
from deltalake.exceptions import CommitFailedError


class SharedFrameCache:
    TABLE = "dash_reports_frame_cache"

    def __init__(self, path: str, storage_options: dict[str, str] | None = None):
        self.path = path
        self.storage_options = storage_options or {}
        self._ensure_table()

    @classmethod
    def local(cls) -> SharedFrameCache:
        from data_access.spark.azuredb import AzureDB

        database = AzureDB(local_dir="local")
        return cls(database.read_path(cls.TABLE), database.storage_options)

    def _ensure_table(self) -> None:
        schema = pa.schema([
            ("cache_key", pa.string()),
            ("namespace", pa.string()),
            ("asof_date", pa.string()),
            ("entity_id", pa.string()),
            ("data_key", pa.string()),
            ("payload", pa.binary()),
            ("row_count", pa.int64()),
            ("updated_at", pa.float64()),
        ])
        seed = pa.Table.from_pylist([{
            "cache_key": "__schema__",
            "namespace": "",
            "asof_date": "",
            "entity_id": "",
            "data_key": "",
            "payload": b"",
            "row_count": 0,
            "updated_at": time.time(),
        }], schema=schema)
        write_deltalake(
            self.path,
            seed,
            mode="ignore",
            engine="rust",
            storage_options=self.storage_options,
        )

    def _table(self) -> DeltaTable:
        return DeltaTable(self.path, storage_options=self.storage_options)

    @staticmethod
    def key(namespace: str, asof_date, entity_id: str, data_key: str) -> str:
        return "|".join((namespace, str(asof_date), entity_id, data_key))

    def read(
        self,
        namespace: str,
        asof_date,
        entity_id: str,
        data_key: str,
        max_age_seconds: float | None = None,
    ) -> pd.DataFrame | None:
        cache_key = self.key(namespace, asof_date, entity_id, data_key)
        table = self._table().to_pyarrow_table(filters=[("cache_key", "=", cache_key)])
        if table.num_rows == 0:
            return None
        if (
            max_age_seconds is not None
            and time.time() - float(table.column("updated_at")[0].as_py()) > max_age_seconds
        ):
            return None
        payload = table.column("payload")[0].as_py()
        return pd.read_parquet(io.BytesIO(payload))

    def write(
        self,
        namespace: str,
        asof_date,
        entity_id: str,
        data_key: str,
        frame: pd.DataFrame,
    ) -> None:
        self.write_many([(namespace, asof_date, entity_id, data_key, frame)])

    def write_many(self, entries: list[tuple]) -> None:
        if not entries:
            return
        rows = []
        now = time.time()
        for namespace, asof_date, entity_id, data_key, frame in entries:
            buffer = io.BytesIO()
            frame.to_parquet(buffer)
            rows.append({
                "cache_key": self.key(namespace, asof_date, entity_id, data_key),
                "namespace": namespace,
                "asof_date": str(asof_date),
                "entity_id": entity_id,
                "data_key": data_key,
                "payload": buffer.getvalue(),
                "row_count": len(frame),
                "updated_at": now,
            })
        source = pa.Table.from_pylist(rows)
        for _ in range(5):
            try:
                (
                    self._table()
                    .merge(
                        source,
                        "target.cache_key = source.cache_key",
                        source_alias="source",
                        target_alias="target",
                    )
                    .when_matched_update(updates={
                        column: f"source.{column}"
                        for column in source.column_names
                        if column != "cache_key"
                    })
                    .when_not_matched_insert(updates={
                        column: f"source.{column}"
                        for column in source.column_names
                    })
                    .execute()
                )
                return
            except CommitFailedError:
                time.sleep(0.02)
                continue
        raise RuntimeError(f"Delta cache write conflict: {source['cache_key'][0].as_py()}")

    def read_entries(
        self,
        namespace: str,
        asof_date,
        entity_ids: list[str] | None = None,
    ) -> dict[tuple[str, str], pd.DataFrame]:
        filters = [
            ("namespace", "=", namespace),
            ("asof_date", "=", str(asof_date)),
        ]
        if entity_ids is not None:
            filters.append(("entity_id", "in", list(entity_ids)))
        table = self._table().to_pyarrow_table(
            columns=["entity_id", "data_key", "payload"],
            filters=filters,
        )
        return {
            (entity.as_py(), data_key.as_py()): pd.read_parquet(io.BytesIO(payload.as_py()))
            for entity, data_key, payload in zip(
                table.column("entity_id"),
                table.column("data_key"),
                table.column("payload"),
            )
        }

    def read_many(self, namespace: str, asof_date, data_key: str) -> list[pd.DataFrame]:
        table = self._table().to_pyarrow_table(filters=[
            ("namespace", "=", namespace),
            ("asof_date", "=", str(asof_date)),
            ("data_key", "=", data_key),
        ])
        return [
            pd.read_parquet(io.BytesIO(payload.as_py()))
            for payload in table.column("payload")
        ]

    def purge_before(self, namespace: str, asof_date) -> None:
        value = str(asof_date).replace("'", "''")
        name = namespace.replace("'", "''")
        self._delete(f"namespace = '{name}' AND asof_date < '{value}'")

    def purge_older_than(self, namespace: str, age_seconds: float) -> None:
        if age_seconds <= 0:
            raise ValueError("age_seconds must be positive")
        name = namespace.replace("'", "''")
        cutoff = time.time() - age_seconds
        self._delete(f"namespace = '{name}' AND updated_at < {cutoff}")

    def _delete(self, predicate: str) -> None:
        for _ in range(5):
            try:
                self._table().delete(predicate=predicate)
                return
            except CommitFailedError:
                time.sleep(0.02)
                continue
        raise RuntimeError(f"Delta cache delete conflict: {predicate}")
