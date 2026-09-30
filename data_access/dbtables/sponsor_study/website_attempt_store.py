"""Append evidence under new attempt IDs, then publish verified sponsor outputs."""

import json
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from data_access.dbtables.sponsor_study.persistence import _qualified_name
from data_access.dbtables.sponsor_study.website_attempts import (
    SponsorAttempt, assert_persisted_frame, compare_company_sets, frame_digest, published_sponsor_predicate,
)
from data_access.dbtables.sponsor_study.website_crawl_persistence import (
    WEBSITE_CRAWL_DERIVED_TABLES, _run_id_literal, _validate_run_rows,
    ensure_website_crawl_tables, website_crawl_contract_dataset,
)
from data_access.dbtables.sponsor_study.website_delta_io import insert_website_rows, read_website_rows, update_website_row
from data_access.dbtables.sponsor_study.website_quality import CompanyReference
from data_access.dbtables.sponsor_study.website_sources import clean_site_snapshots
from data_access.dbtables.sponsor_study.website_company_classification import CLASSIFICATION_VERSION


SPONSOR_COLUMN = {name: "sponsor" for name in WEBSITE_CRAWL_DERIVED_TABLES}
SPONSOR_COLUMN["source_sponsor_website_holding"] = "sponsor_name"


@dataclass(frozen=True)
class WebsiteAttemptStore:
    environment: str
    catalog: str
    schema: str
    app: str
    table_prefix: str

    def ensure_tables(self) -> None:
        ensure_website_crawl_tables(**self.__dict__)

    def table(self, name: str) -> str:
        return _qualified_name(self.catalog, self.schema, name, self.table_prefix)

    def read_scope(self, name: str, run_id: str, sponsor: str | None = None) -> pd.DataFrame:
        predicate = f"crawl_run_id = {_run_id_literal(run_id)}"
        if sponsor is not None:
            predicate += f" AND {SPONSOR_COLUMN.get(name, 'sponsor')} = {_run_id_literal(sponsor)}"
        return read_website_rows(f"SELECT * FROM {self.table(name)} WHERE {predicate}", app=self.app)

    def insert(self, name: str, frame: pd.DataFrame) -> None:
        if not frame.empty:
            insert_website_rows(self.table(name), frame, app=self.app, maxrow_batch=100,
                                msg=f"append website attempt {name}")

    def update_control(self, name: str, frame: pd.DataFrame) -> None:
        keys = ("crawl_run_id", "sponsor_website_id") if name.endswith("_sponsor") else ("crawl_run_id",)
        if len(frame) != 1:
            raise ValueError("An attempt control update requires one row")
        update_website_row(self.table(name), frame.iloc[0], keys=keys, app=self.app)

    def history(self, logical_run_id: str) -> pd.DataFrame:
        return read_website_rows(f"SELECT * FROM {self.table('control_website_crawl_run_sponsor')} "
                         f"WHERE logical_crawl_run_id = {_run_id_literal(logical_run_id)}", app=self.app)

    def previous_companies(self, sponsor: str) -> tuple[CompanyReference, ...]:
        sponsor_runs = self.table("control_website_crawl_run_sponsor")
        runs = self.table("control_website_crawl_run")
        holdings = self.table("source_sponsor_website_holding")
        candidates = self.table("source_sponsor_site_holding_candidate")
        previous = read_website_rows(f"""
WITH latest AS (
    SELECT s.crawl_run_id, s.sponsor
    FROM {sponsor_runs} s JOIN {runs} r ON r.crawl_run_id = s.crawl_run_id
    WHERE s.sponsor = {_run_id_literal(sponsor)} AND {published_sponsor_predicate('s', 'r')}
    QUALIFY ROW_NUMBER() OVER (ORDER BY s.run_started_at DESC, s.crawl_run_id DESC) = 1
)
SELECT DISTINCT h.company_name, h.company_url
FROM {holdings} h JOIN latest l ON h.crawl_run_id = l.crawl_run_id AND h.sponsor_name = l.sponsor
JOIN {candidates} c ON c.crawl_run_id = h.crawl_run_id
    AND c.website_holding_candidate_id = h.website_holding_candidate_id
    AND c.company_classification = 'company' AND c.company_classification_version = '{CLASSIFICATION_VERSION}'
""", app=self.app)
        return company_references(previous)

    def begin_sponsor(self, attempt: SponsorAttempt) -> None:
        name = "control_website_crawl_run_sponsor"
        if not self.read_scope(name, attempt.crawl.run_id, attempt.sponsor).empty:
            raise ValueError("Sponsor attempt already exists; retries require a new attempt ID")
        frame = website_crawl_contract_dataset()[name].copy()
        frame.loc[0] = {"crawl_run_id": attempt.crawl.run_id,
                        "logical_crawl_run_id": attempt.crawl.logical_run_id,
                        "sponsor_website_id": attempt.website_id, "sponsor": attempt.sponsor,
                        "configuration_fingerprint": attempt.fingerprint,
                        "attempt_count": attempt.number, "status": "running", "retry_eligible": True,
                        "run_started_at": attempt.crawl.started_at}
        self.insert(name, frame)
        assert_persisted_frame(frame, self.read_scope(name, attempt.crawl.run_id, attempt.sponsor), table=name)

    def append_verified(self, name: str, frame: pd.DataFrame, attempt: SponsorAttempt) -> None:
        _validate_run_rows(name, frame, crawl_run_id=attempt.crawl.run_id)
        if not frame.empty and not frame[SPONSOR_COLUMN[name]].eq(attempt.sponsor).all():
            raise ValueError(f"{name} crosses the sponsor checkpoint boundary")
        existing = self.read_scope(name, attempt.crawl.run_id, attempt.sponsor)
        if not existing.empty:
            raise ValueError(f"{name} already contains attempt rows; evidence cannot be replaced")
        self.insert(name, frame)
        assert_persisted_frame(frame, self.read_scope(name, attempt.crawl.run_id, attempt.sponsor), table=name)

    def checkpoint_capture(self, snapshots: pd.DataFrame, attempt: SponsorAttempt) -> None:
        self.append_verified("source_sponsor_site_snapshot", clean_site_snapshots(snapshots), attempt)

    def finish_sponsor(self, dataset: dict[str, pd.DataFrame], attempt: SponsorAttempt,
                       *, maximum_loss_fraction: float = 0.1) -> pd.DataFrame:
        name = "control_website_crawl_run_sponsor"
        existing = self.read_scope(name, attempt.crawl.run_id, attempt.sponsor)
        if len(existing) != 1 or existing.iloc[0].status != "running":
            raise ValueError("Only an open sponsor attempt can be finished")
        control = dataset[name].copy()
        if len(control) != 1 or control.iloc[0].sponsor_website_id != attempt.website_id:
            raise ValueError("Sponsor control does not match the attempt")
        snapshots = dataset["source_sponsor_site_snapshot"]
        candidates = dataset["source_sponsor_site_holding_candidate"]
        holdings = dataset["source_sponsor_website_holding"]
        validate_observation_lineage(snapshots, candidates, holdings)
        loss = compare_company_sets(self.previous_companies(attempt.sponsor), company_references(holdings),
                                    maximum_fraction=maximum_loss_fraction)
        control["logical_crawl_run_id"] = attempt.crawl.logical_run_id
        control["configuration_fingerprint"] = attempt.fingerprint
        control["attempt_count"] = attempt.number
        control["previous_company_count"] = loss.previous_count
        control["lost_company_count"] = len(loss.missing)
        control["lost_company_names_json"] = json.dumps(loss.missing)
        if control.iloc[0].status == "succeeded" and not loss.acceptable:
            control["status"] = "partial"
            control["retry_eligible"] = True
            control["last_error"] = f"{len(loss.missing)}/{loss.previous_count} previously accepted companies missing"
        # The raw checkpoint must still be intact before derived writes begin.
        assert_persisted_frame(snapshots, self.read_scope("source_sponsor_site_snapshot", attempt.crawl.run_id,
                                                        attempt.sponsor), table="source_sponsor_site_snapshot")
        for table in WEBSITE_CRAWL_DERIVED_TABLES:
            if table != "source_sponsor_site_snapshot":
                self.append_verified(table, dataset[table], attempt)
        control["verification_manifest_json"] = json.dumps({
            table: {"columns": list(dataset[table].columns), "rows": len(dataset[table]),
                    "sha256": frame_digest(dataset[table], dataset[table].attrs.get("delta_schema_types", {}))}
            for table in WEBSITE_CRAWL_DERIVED_TABLES}, sort_keys=True)
        control["publication_verified_at"] = (
            datetime.now(timezone.utc) if control.iloc[0].status in {"succeeded", "skipped_vendor_only"} else pd.NaT)
        # One atomic UPDATE exposes the sponsor only after all evidence passed readback.
        self.update_control(name, control)
        assert_persisted_frame(control, self.read_scope(name, attempt.crawl.run_id, attempt.sponsor), table=name)
        return control

    def load_completed(self, control: pd.Series) -> dict[str, pd.DataFrame]:
        dataset = website_crawl_contract_dataset()
        manifest = json.loads(control.verification_manifest_json)
        for table in WEBSITE_CRAWL_DERIVED_TABLES:
            stored = self.read_scope(table, str(control.crawl_run_id), str(control.sponsor))
            expected = manifest[table]
            stored = stored[expected["columns"]]
            stored.attrs = dataset[table].attrs.copy()
            if len(stored) != expected["rows"] or frame_digest(stored, stored.attrs.get("delta_schema_types", {})) != expected["sha256"]:
                raise ValueError(f"Previously published {control.sponsor} failed readback verification: {table}")
            dataset[table] = stored
        dataset["control_website_crawl_run_sponsor"] = control.to_frame().T
        return dataset


def company_references(holdings: pd.DataFrame) -> tuple[CompanyReference, ...]:
    return tuple(CompanyReference(str(row.company_name), "" if pd.isna(row.company_url)
                                  else str(row.company_url)) for row in holdings.itertuples())


def validate_observation_lineage(snapshots: pd.DataFrame, candidates: pd.DataFrame, holdings: pd.DataFrame) -> None:
    if not candidates.empty:
        if candidates.site_snapshot_id.isna().any() or not candidates.site_snapshot_id.isin(snapshots.site_snapshot_id).all():
            raise ValueError("Candidate evidence is missing from the persisted capture")
    if not holdings.empty:
        companies = candidates.loc[candidates.company_classification.eq("company"), "website_holding_candidate_id"]
        if not holdings.website_holding_candidate_id.isin(companies).all():
            raise ValueError("A website observation requires an accepted company candidate")


def completed_sponsor(history: pd.DataFrame, website_id: str, fingerprint: str) -> pd.Series | None:
    if history.empty:
        return None
    matches = history.loc[
        history.sponsor_website_id.eq(website_id) & history.configuration_fingerprint.eq(fingerprint)
        & history.status.isin(["succeeded", "skipped_vendor_only"]) & history.publication_verified_at.notna()]
    return matches.sort_values("run_started_at").iloc[-1] if not matches.empty else None


def next_attempt_number(history: pd.DataFrame, website_id: str) -> int:
    if history.empty:
        return 1
    previous = history.loc[history.sponsor_website_id.eq(website_id), "attempt_count"]
    return int(previous.max()) + 1 if not previous.empty else 1
