"""Evidence-backed upgrades for legacy decisions before changing serving views."""

import logging
import re
from dataclasses import dataclass

import pandas as pd

from common.utils.sqlutil import execsql, getconn
from data_access.dbtables.sponsor_study.website_company_classification import (
    CLASSIFICATION_COLUMNS, CLASSIFICATION_VERSION, PageEvidence, classify_holding_candidates,
)
from data_access.dbtables.sponsor_study.website_delta_io import read_website_rows


KEYS = ["crawl_run_id", "website_holding_candidate_id"]


def classification_view_definition(definition: str, *, compatible: bool) -> str:
    """Keep legacy decisions readable while their version is upgraded in batches."""
    version = re.escape(CLASSIFICATION_VERSION)
    pattern = re.compile(r"(?P<column>(?:\b\w+\.)?company_classification_version)\s*(?:"
        rf"=\s*'(?:website_company_v1|{version})'|"
        rf"IN\s*\(\s*'website_company_v1'\s*,\s*'{version}'\s*\))", re.I)
    predicate = (f"IN ('website_company_v1', '{CLASSIFICATION_VERSION}')" if compatible
                 else f"= '{CLASSIFICATION_VERSION}'")
    updated, count = pattern.subn(lambda match: f"{match['column']} {predicate}", definition)
    if not count:
        raise ValueError("Unrecognized website classification predicate; view requires explicit migration")
    return updated


def migrate_classification_views(*, catalog: str, table_prefix: str, app: str, compatible: bool) -> None:
    if not all(re.fullmatch(r"[A-Za-z_]\w*", value) for value in (catalog, table_prefix)):
        raise ValueError("Classification view migration requires safe catalog and prefix identifiers")
    views = read_website_rows(f"SELECT table_schema, table_name, view_definition FROM {catalog}.information_schema.views "
        "WHERE table_schema IN ('dbo', 'gold') AND "
        f"STARTSWITH(table_name, '{table_prefix}') AND view_definition LIKE '%company_classification_version%'", app=app)
    # Prepare every definition before mutating any view. ALTER retains view metadata and grants.
    definitions = [(row.table_schema, row.table_name, classification_view_definition(row.view_definition, compatible=compatible))
                   for row in views.itertuples(index=False)]
    for schema, name, definition in definitions:
        qualified = '.'.join('`' + value.replace('`', '``') + '`' for value in (catalog, schema, name))
        execsql(f"ALTER VIEW {qualified} AS {definition}", app=app, msg="update website classification serving compatibility")


@dataclass(frozen=True)
class ClassificationUpgrade:
    before: pd.DataFrame
    after: pd.DataFrame
    updates: pd.DataFrame

    @property
    def decision_changes(self) -> int:
        return int(self.before.company_classification.ne(self.after.company_classification).sum())


def plan_classification_upgrade(candidates: pd.DataFrame, snapshots: pd.DataFrame) -> ClassificationUpgrade:
    if candidates[KEYS].isna().any().any():
        raise ValueError("Classification upgrade requires non-null candidate identities")
    if candidates.crawl_run_id.str.contains(r"__[0-9a-f]{32}$", regex=True).any():
        raise ValueError("Verified attempt decisions are immutable; replay into a new attempt")
    evidence_keys = ["crawl_run_id", "site_snapshot_id"]
    duplicates = snapshots.loc[snapshots.duplicated(evidence_keys, keep=False)]
    for _, copies in duplicates.groupby(evidence_keys):
        if len({PageEvidence.from_record(row) for row in copies.to_dict("records")}) != 1:
            raise ValueError("Classification upgrade has ambiguous snapshot evidence")
    evidence = pd.MultiIndex.from_frame(snapshots[evidence_keys])
    if not pd.MultiIndex.from_frame(candidates[evidence_keys]).isin(evidence).all():
        raise ValueError("Classification upgrade requires every original snapshot")
    before = candidates.reset_index(drop=True)
    after = classify_holding_candidates(before, snapshots)
    updates = after[[*KEYS, *CLASSIFICATION_COLUMNS]].copy()
    updates["previous_version"] = before.company_classification_version.fillna("")
    updates = updates.drop_duplicates()
    if updates.duplicated([*KEYS, "previous_version"]).any():
        raise ValueError("Legacy candidate identity has conflicting classification decisions")
    return ClassificationUpgrade(before, after, updates)


def pending_classifications(candidate_table: str, *, app: str) -> pd.DataFrame:
    return read_website_rows(f"SELECT * FROM {candidate_table} WHERE "
        f"COALESCE(company_classification_version, '') <> '{CLASSIFICATION_VERSION}'", app=app)


def assert_classification_cutover_ready(candidate_table: str, *, app: str) -> None:
    pending = read_website_rows(f"SELECT COUNT(*) AS pending FROM {candidate_table} WHERE "
        f"COALESCE(company_classification_version, '') <> '{CLASSIFICATION_VERSION}'", app=app)
    if int(pending.iloc[0].pending):
        raise ValueError("Legacy website classifications require an evidence-backed upgrade before view cutover; "
                         "run deploy_website_crawl_delta with --upgrade-classification")


def apply_classification_upgrade(plan: ClassificationUpgrade, candidate_table: str, *, app: str) -> None:
    """Compare-and-set only classification columns; raw evidence and IDs stay intact."""
    if not re.fullmatch(r"[A-Za-z_][\w]*\.[A-Za-z_][\w]*\.[A-Za-z_][\w]*", candidate_table):
        raise ValueError("Classification migration requires a qualified table name")
    if plan.before.empty:
        return
    columns = [*KEYS, "previous_version", *CLASSIFICATION_COLUMNS]
    rows = plan.updates
    assignments = ", ".join(f"t.{column} = s.{column}" for column in CLASSIFICATION_COLUMNS)
    with getconn(app) as connection:
        with connection.cursor() as cursor:
            for start in range(0, len(rows), 100):
                batch = rows.iloc[start:start + 100][columns]
                values = ", ".join("(" + ", ".join("?" for _ in columns) + ")" for _ in range(len(batch)))
                cursor.execute(f"MERGE INTO {candidate_table} t USING "
                    f"(SELECT * FROM VALUES {values} AS staged ({', '.join(columns)})) s "
                    "ON t.crawl_run_id = s.crawl_run_id AND t.website_holding_candidate_id = s.website_holding_candidate_id "
                    "AND COALESCE(t.company_classification_version, '') = s.previous_version "
                    f"WHEN MATCHED THEN UPDATE SET {assignments}",
                    parameters=[value for row in batch.itertuples(index=False, name=None) for value in row])
    runs = ", ".join("'" + value.replace("'", "''") + "'" for value in plan.before.crawl_run_id.unique())
    stored = read_website_rows(f"SELECT * FROM {candidate_table} WHERE crawl_run_id IN ({runs})", app=app)
    actual = stored.merge(plan.before[KEYS].drop_duplicates(), on=KEYS, how="inner", validate="many_to_one")
    ordering = list(plan.after.columns)
    expected = plan.after.sort_values(ordering).reset_index(drop=True)
    actual = actual[ordering].sort_values(ordering).reset_index(drop=True)
    pd.testing.assert_frame_equal(actual, expected, check_dtype=False, check_like=True)
    logging.info("Upgraded legacy website classifications rows=%s changed_decisions=%s version=%s",
                 len(plan.after), plan.decision_changes, CLASSIFICATION_VERSION)
