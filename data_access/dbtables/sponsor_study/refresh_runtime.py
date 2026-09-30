"""Run-scoped, immutable checkpoints for the Databricks sponsor refresh."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime, timezone
from functools import wraps
from hashlib import sha256
from io import BytesIO
import json
import logging
import re
import sys
from typing import Callable, Iterator
from uuid import uuid4

import pandas as pd

from data_access.dbtables.sponsor_study import DEFAULT_DELTA_BLOB_DIR

@dataclass(frozen=True)
class RunIdentity:
    run_id: str
    cutoff: str
    release: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", self.run_id):
            raise ValueError("Invalid refresh identifier")
        date.fromisoformat(self.cutoff)
        if not self.release:
            raise ValueError("Checkpoint reuse requires an immutable code release")


def fingerprint(value: object) -> str:
    """Hash explicit call inputs, including data, without persisting review inputs in code."""
    if isinstance(value, pd.DataFrame):
        content = value.to_json(orient="split", date_format="iso", default_handler=str)
        return sha256((content + str(value.dtypes.to_dict())).encode()).hexdigest()
    if isinstance(value, dict):
        value = sorted((str(key), fingerprint(item)) for key, item in value.items())
    elif isinstance(value, (tuple, list)):
        value = [fingerprint(item) for item in value]
    elif isinstance(value, (set, frozenset)):
        value = sorted(fingerprint(item) for item in value)
    elif is_dataclass(value):
        return fingerprint(asdict(value))
    return sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


class RefreshRuntime:
    def __init__(self, identity: RunIdentity, spark, container, *, catalog: str,
                 storage_account: str, blob_container: str, blob_dir: str = DEFAULT_DELTA_BLOB_DIR) -> None:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", catalog):
            raise ValueError("Invalid checkpoint catalog")
        self.identity, self.spark, self.container = identity, spark, container
        if (
            container.account_name != storage_account.split(".", 1)[0]
            or container.container_name != blob_container
        ):
            raise ValueError("Checkpoints require the configured sponsor-study storage account and container")
        self.catalog = catalog
        self.blob_dir = blob_dir.strip("/")
        self.root = f"{self.blob_dir}/_checkpoints/pe_sponsor/{identity.run_id}"
        self.audit_table = f"{catalog}.dbo.pe_sponsor_meta_stage_attempt"
        self.spark.sql(f"CREATE TABLE IF NOT EXISTS {self.audit_table} ("
            "refresh_run_id STRING, stage STRING, batch_id STRING, attempt_id STRING, "
            "status STRING, event_at TIMESTAMP, details_json STRING) USING DELTA "
            f"LOCATION '{self.location(f'{self.blob_dir}/pe_sponsor_meta_stage_attempt')}'")
        manifest = self.container.get_blob_client(f"{self.root}/run.json")
        if manifest.exists():
            saved = json.loads(manifest.download_blob().readall())
            if saved["identity"] != asdict(identity):
                raise ValueError("Repair attempted with a different cutoff or code release")
        else:
            saved = {"identity": asdict(identity), "started_at": datetime.now(timezone.utc).isoformat()}
            manifest.upload_blob(json.dumps(saved), overwrite=False)
        self.started_at = pd.Timestamp(saved["started_at"]).tz_convert(None)
        self.event("run", "", "", "started", asdict(identity))

    def event(self, stage: str, batch: str, attempt: str, status: str, details: dict) -> None:
        self.spark.createDataFrame([(
            self.identity.run_id, stage, batch, attempt, status,
            datetime.now(timezone.utc).replace(tzinfo=None), json.dumps(details, default=str),
        )], "refresh_run_id string, stage string, batch_id string, attempt_id string, "
            "status string, event_at timestamp, details_json string").write.mode("append").saveAsTable(self.audit_table)
        logging.info("Sponsor checkpoint stage=%s batch=%s status=%s details=%s", stage, batch, status, details)

    def location(self, relative: str) -> str:
        return (f"abfss://{self.container.container_name}@{self.container.account_name}"
                f".dfs.core.windows.net/{relative}")

    def source_table(self, table: str):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", table):
            raise ValueError("Invalid history source")
        marker = self.container.get_blob_client(f"{self.root}/sources/{table}.json")
        if marker.exists():
            version = json.loads(marker.download_blob().readall())["version"]
        else:
            version = int(self.spark.sql(f"DESCRIBE HISTORY {table} LIMIT 1").first()["version"])
            marker.upload_blob(json.dumps({"version": version}), overwrite=False)
        self.event("source_version", table, "", "pinned", {"version": version})
        return self.spark.read.option("versionAsOf", version).table(table)

    def materialize(self, stage: str, key: str, producer: Callable):
        """Commit a distributed Delta result before publishing its completion manifest."""
        batch = fingerprint(key)
        marker = self.container.get_blob_client(f"{self.root}/{stage}/{batch}/complete.json")
        if marker.exists():
            saved = json.loads(marker.download_blob().readall())
            result = self.spark.read.format("delta").load(saved["path"])
            if result.count() != saved["rows"]:
                raise ValueError(f"Checkpoint row count mismatch: {stage}")
            self.event(stage, batch, "", "reused", saved)
            return result
        attempt = uuid4().hex
        self.event(stage, batch, attempt, "running", {})
        path = self.location(f"{self.root}/{stage}/{batch}/{attempt}")
        result = producer()
        result.write.format("delta").mode("error").save(path)
        result = self.spark.read.format("delta").load(path)
        saved = {"path": path, "rows": result.count()}
        if stage in {"entity_relationship_history", "entity_relationship_resolution", "capital_stack_resolution"}:
            from pyspark.sql import functions as F
            table = f"{self.catalog}.dbo.pe_sponsor_{stage}"
            published = result.withColumn("refresh_run_id", F.lit(self.identity.run_id)).withColumn("batch_id", F.lit(batch))
            published.write.format("delta").mode("overwrite").partitionBy("refresh_run_id", "batch_id").option(
                "replaceWhere", f"refresh_run_id = '{self.identity.run_id}' AND batch_id = '{batch}'").option(
                "path", self.location(f"{self.blob_dir}/pe_sponsor_{stage}")).saveAsTable(table)
        marker.upload_blob(json.dumps(saved), overwrite=False)
        self.event(stage, batch, attempt, "completed", saved)
        return result

    def call(self, stage: str, args: tuple, kwargs: dict, producer: Callable):
        batch = fingerprint((args, kwargs))
        marker = self.container.get_blob_client(f"{self.root}/{stage}/{batch}/complete.json")
        if marker.exists():
            saved = json.loads(marker.download_blob().readall())
            frames = {}
            for name, item in saved["frames"].items():
                content = self.container.get_blob_client(item["path"]).download_blob().readall()
                if sha256(content).hexdigest() != item["sha256"]:
                    raise ValueError(f"Corrupt checkpoint: {stage}/{name}")
                frame = pd.read_parquet(BytesIO(content))
                if len(frame) != item["rows"]:
                    raise ValueError(f"Incomplete checkpoint: {stage}/{name}")
                frame.attrs.update(item["attrs"])
                frames[name] = frame
            self.event(stage, batch, "", "reused", {"frames": len(frames)})
            return frames["result"] if saved["single"] else frames
        attempt = uuid4().hex
        self.event(stage, batch, attempt, "running", {})
        result = producer(*args, **kwargs)
        single = isinstance(result, pd.DataFrame)
        frames = {"result": result} if single else result
        if not isinstance(frames, dict) or not all(isinstance(v, pd.DataFrame) for v in frames.values()):
            raise TypeError(f"Checkpoint stage {stage} must return DataFrames")
        saved = {"single": single, "frames": {}}
        for name, frame in frames.items():
            path = f"{self.root}/{stage}/{batch}/{attempt}/{fingerprint(name)}.parquet"
            content = frame.to_parquet(index=True)
            self.container.get_blob_client(path).upload_blob(content, overwrite=False)
            saved["frames"][name] = {"path": path, "rows": len(frame),
                "sha256": sha256(content).hexdigest(), "attrs": frame.attrs}
        marker.upload_blob(json.dumps(saved, default=str), overwrite=False)
        self.event(stage, batch, attempt, "completed", {"rows": sum(len(f) for f in frames.values())})
        return result


_ACTIVE: ContextVar[RefreshRuntime | None] = ContextVar("sponsor_refresh_runtime", default=None)


def active_runtime() -> RefreshRuntime | None:
    return _ACTIVE.get()


@contextmanager
def refresh_runtime(runtime: RefreshRuntime) -> Iterator[None]:
    token = _ACTIVE.set(runtime)
    try:
        yield
    finally:
        error = sys.exception()
        try:
            runtime.event("run", "", "", "failed" if error else "completed",
                          {"error": str(error)[:2000]} if error else {})
        finally:
            _ACTIVE.reset(token)


def checkpointed(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        runtime = active_runtime()
        if runtime is None:
            from data_access.dbtables.sponsor_study.diagnostic_trace import trace_call
            with trace_call(function.__module__ + "." + function.__name__):
                return function(*args, **kwargs)
        stage = function.__module__.rsplit(".", 1)[-1] + "." + function.__name__
        return runtime.call(stage, args, kwargs, function)
    return wrapped
