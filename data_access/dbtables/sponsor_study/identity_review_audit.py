"""Run the serving SQL against a pinned local snapshot for review and previews."""

from __future__ import annotations

import pandas as pd

from data_access.dbtables.sponsor_study.external_prod_contracts import external_prod_table_contracts
from data_access.dbtables.sponsor_study.query import (
    _latest_external_refresh_id_sql, pe_sponsor_holding_identity_resolved_view_sql,
)


def holding_audit(dataset: dict[str, pd.DataFrame], *, refresh_run_id: str,
                  audit_sql: str | None = None) -> pd.DataFrame:
    import duckdb

    sql = audit_sql if audit_sql is not None else pe_sponsor_holding_identity_resolved_view_sql(
        catalog="review", audit=True).split(" AS\n", 1)[1]
    latest = _latest_external_refresh_id_sql("review", "dbo", "pe_sponsor_")
    sql = sql.replace(latest, "'" + refresh_run_id.replace("'", "''") + "'")
    contracts = external_prod_table_contracts()
    with duckdb.connect() as connection:
        for name, columns in {
            "source_external_sponsor_website_holding": ["website_holding_id", "crawl_run_id", "website_holding_candidate_id"],
            "source_sponsor_site_holding_candidate": ["crawl_run_id", "website_holding_candidate_id",
                "company_classification", "company_classification_version", "company_classification_reason"],
        }.items():
            frame = dataset.get(name, pd.DataFrame(columns=columns)).reindex(columns=columns).astype("string")
            connection.register(name, frame)
            sql = sql.replace(f"review.dbo.pe_sponsor_{name}", name)
        for name in ("fact_external_holding", "xref_external_company_name_decision"):
            frame = dataset[name].reindex(columns=contracts[name].columns).copy()
            types = contracts[name].attrs["delta_schema_types"]
            for column in frame:
                kind = types.get(column, "STRING")
                if kind == "STRING":
                    frame[column] = frame[column].astype("string")
                elif kind == "TIMESTAMP":
                    frame[column] = pd.to_datetime(frame[column], utc=True).dt.tz_localize(None)
                elif kind == "BOOLEAN":
                    # SQL reads and concatenated manual rows can mix 0/1 floats and booleans.
                    values = pd.to_numeric(frame[column]).astype("Float64")
                    if not values.dropna().isin([0, 1]).all():
                        raise ValueError(f"Invalid boolean values in {name}.{column}")
                    frame[column] = values.astype("boolean")
                else:
                    frame[column] = pd.to_numeric(frame[column]).astype("Float64")
            frame["refresh_run_id"] = refresh_run_id
            connection.register(name, frame)
            sql = sql.replace(f"review.dbo.pe_sponsor_{name}", name)
        connection.execute("CREATE MACRO COLLECT_SET(x) AS list_distinct(list(x))")
        connection.execute("CREATE MACRO SORT_ARRAY(x) AS list_sort(x)")
        connection.execute("CREATE MACRO SHA2(x, bits) AS sha256(x)")
        sql = sql.replace("<=>", "IS NOT DISTINCT FROM").replace("* EXCEPT (", "* EXCLUDE (")
        audit = connection.execute(sql).df()
    if len(audit) != len(dataset["fact_external_holding"]) or audit.external_holding_id.duplicated().any():
        raise ValueError("Holding audit does not reconcile one-to-one with input holdings")
    return audit
