from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from common.utils.sqlutil import readdfsql
from data_access.dbtables import FINDOX_APP


COMPLIANCE_MODE_OBSERVE = "observe"

CONTROL_COLUMNS = [
    "portco_id",
    "company_name",
    "findox_issuer_id",
    "factset_entity_id",
    "bbg_company_id",
    "private_data_flag",
    "direct_lending_only_flag",
    "pe_investment_team_restricted_flag",
    "disqualified_lender_blocked_flag",
    "allowed_group_names",
    "blocked_group_names",
    "restriction_reason",
    "restriction_source",
    "effective_start_date",
    "effective_end_date",
]

SNAPSHOT_COLUMNS = [
    "portco_id",
    "company_name",
    "private_data_flag",
    "direct_lending_only_flag",
    "pe_investment_team_restricted_flag",
    "disqualified_lender_blocked_flag",
    "visible_to_all_placeholder_flag",
    "allowed_group_names",
    "blocked_group_names",
    "restriction_reason",
    "restriction_source",
    "effective_start_date",
    "effective_end_date",
]

FINDOX_PRIVACY_SQL = """
WITH current_models AS (
    SELECT * FROM dbo.findox_agg_universe
    QUALIFY ROW_NUMBER() OVER (PARTITION BY id ORDER BY financial_data_updated DESC)=1
)
SELECT CAST(findox_id AS STRING) AS findox_issuer_id,
       BOOL_AND(COALESCE(lower(model_type_label) <> 'public', true)) AS private_only,
       BOOL_OR(COALESCE(lower(model_type_label) <> 'public', true)) AS contains_private_data
FROM current_models
WHERE CAST(findox_id AS STRING) IN ({issuer_ids})
GROUP BY findox_id
"""

INTERNAL_ENRICHMENT_TABLES = [
    "source_internal_financial_snapshot",
    "source_internal_market_trading_snapshot",
    "source_internal_debt_instrument_snapshot",
    "source_internal_debt_issuer_bridge",
    "fact_internal_debt_feature_snapshot",
    "source_factset_dcs_debt_detail",
]


@dataclass(frozen=True)
class ComplianceEntity:
    portco_id: str
    company_name: str
    private_data_flag: bool
    direct_lending_only_flag: bool
    pe_investment_team_restricted_flag: bool
    disqualified_lender_blocked_flag: bool
    visible_to_all_placeholder_flag: bool
    allowed_group_names: str
    blocked_group_names: str
    restriction_reason: str
    restriction_source: str
    effective_start_date: pd.Timestamp | None
    effective_end_date: pd.Timestamp | None


def _empty(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


def _norm_name(value) -> str:
    if pd.isna(value):
        return ""
    return " ".join(str(value).strip().lower().split())


def _as_bool(value, default: bool = False) -> bool:
    if pd.isna(value):
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"", "0", "false", "no", "n"}:
        return False
    if text in {"1", "true", "yes", "y", "x"}:
        return True
    return default


def _clean_id(value) -> str | None:
    if pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _quote_ids(values: list[str]) -> str:
    return ", ".join("'" + str(value).replace("'", "''") + "'" for value in values)


def _parse_date(value) -> pd.Timestamp | None:
    if pd.isna(value) or value in {"", None}:
        return None
    return pd.to_datetime(value, errors="coerce")


def _control_active(row: pd.Series, asof: pd.Timestamp) -> bool:
    start = _parse_date(row.get("effective_start_date"))
    end = _parse_date(row.get("effective_end_date"))
    if start is not None and asof < start:
        return False
    if end is not None and asof > end:
        return False
    return True


def read_compliance_control_file(path: str | Path | None) -> pd.DataFrame:
    if not path:
        return _empty(CONTROL_COLUMNS)
    file_path = Path(path)
    if not file_path.exists():
        logging.warning("Compliance control file not found, using empty controls: %s", file_path)
        return _empty(CONTROL_COLUMNS)
    if file_path.suffix.lower() in {".xlsx", ".xlsm"}:
        df = pd.read_excel(file_path)
    else:
        df = pd.read_csv(file_path)
    df.columns = [str(col).strip().lower() for col in df.columns]
    for col in CONTROL_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA
    return df[CONTROL_COLUMNS].copy()


def fetch_findox_privacy_metadata(issuer_ids: list[str], app: str = FINDOX_APP) -> pd.DataFrame:
    clean_ids = sorted({_clean_id(value) for value in issuer_ids if _clean_id(value)})
    if not clean_ids:
        return pd.DataFrame(columns=["findox_issuer_id", "private_only", "contains_private_data"])
    sql = FINDOX_PRIVACY_SQL.format(issuer_ids=_quote_ids(clean_ids))
    df = readdfsql(sql, app=app)
    df.columns = [str(col).lower() for col in df.columns]
    return df


def _default_entity(portco_id: str, company_name: str) -> ComplianceEntity:
    return ComplianceEntity(
        portco_id=portco_id,
        company_name=company_name,
        private_data_flag=False,
        direct_lending_only_flag=False,
        pe_investment_team_restricted_flag=False,
        disqualified_lender_blocked_flag=False,
        visible_to_all_placeholder_flag=True,
        allowed_group_names="",
        blocked_group_names="",
        restriction_reason="",
        restriction_source="default_all_visible",
        effective_start_date=None,
        effective_end_date=None,
    )


def _entity_to_row(entity: ComplianceEntity) -> dict:
    return {
        "portco_id": entity.portco_id,
        "company_name": entity.company_name,
        "private_data_flag": entity.private_data_flag,
        "direct_lending_only_flag": entity.direct_lending_only_flag,
        "pe_investment_team_restricted_flag": entity.pe_investment_team_restricted_flag,
        "disqualified_lender_blocked_flag": entity.disqualified_lender_blocked_flag,
        "visible_to_all_placeholder_flag": entity.visible_to_all_placeholder_flag,
        "allowed_group_names": entity.allowed_group_names,
        "blocked_group_names": entity.blocked_group_names,
        "restriction_reason": entity.restriction_reason,
        "restriction_source": entity.restriction_source,
        "effective_start_date": entity.effective_start_date,
        "effective_end_date": entity.effective_end_date,
    }


def _apply_control_row(entity: ComplianceEntity, control: pd.Series, source: str) -> ComplianceEntity:
    private_flag = _as_bool(control.get("private_data_flag"), entity.private_data_flag)
    direct_lending_flag = _as_bool(control.get("direct_lending_only_flag"), entity.direct_lending_only_flag)
    pe_restricted = _as_bool(control.get("pe_investment_team_restricted_flag"), entity.pe_investment_team_restricted_flag)
    disqualified = _as_bool(control.get("disqualified_lender_blocked_flag"), entity.disqualified_lender_blocked_flag)
    return ComplianceEntity(
        portco_id=entity.portco_id,
        company_name=entity.company_name,
        private_data_flag=private_flag or entity.private_data_flag,
        direct_lending_only_flag=direct_lending_flag or entity.direct_lending_only_flag,
        pe_investment_team_restricted_flag=pe_restricted or entity.pe_investment_team_restricted_flag,
        disqualified_lender_blocked_flag=disqualified or entity.disqualified_lender_blocked_flag,
        visible_to_all_placeholder_flag=not (
            private_flag or direct_lending_flag or pe_restricted or disqualified
        ),
        allowed_group_names=str(control.get("allowed_group_names") or entity.allowed_group_names or ""),
        blocked_group_names=str(control.get("blocked_group_names") or entity.blocked_group_names or ""),
        restriction_reason=str(control.get("restriction_reason") or entity.restriction_reason or ""),
        restriction_source=source,
        effective_start_date=_parse_date(control.get("effective_start_date")) or entity.effective_start_date,
        effective_end_date=_parse_date(control.get("effective_end_date")) or entity.effective_end_date,
    )


def _match_controls_to_portco(
    portco_id: str,
    company_name: str,
    xref_row: pd.Series | None,
    controls: pd.DataFrame,
    asof: pd.Timestamp,
) -> list[pd.Series]:
    if controls.empty:
        return []
    active = controls[controls.apply(lambda row: _control_active(row, asof), axis=1)].copy()
    if active.empty:
        return []
    matches = []
    company_key = _norm_name(company_name)
    for _, control in active.iterrows():
        control_portco = _clean_id(control.get("portco_id"))
        if control_portco and control_portco == portco_id:
            matches.append(control)
            continue
        if company_key and company_key == _norm_name(control.get("company_name")):
            matches.append(control)
            continue
        if xref_row is None:
            continue
        for xref_col, control_col in [
            ("findox_issuer_id", "findox_issuer_id"),
            ("factset_entity_id", "factset_entity_id"),
            ("bbg_company_id", "bbg_company_id"),
        ]:
            xref_val = _clean_id(xref_row.get(xref_col))
            control_val = _clean_id(control.get(control_col))
            if xref_val and control_val and xref_val == control_val:
                matches.append(control)
                break
    return matches


def build_compliance_snapshot(
    dataset: dict[str, pd.DataFrame],
    controls: pd.DataFrame | None = None,
    *,
    asof_date: str | pd.Timestamp | None = None,
    findox_app: str = FINDOX_APP,
) -> pd.DataFrame:
    stage = dataset.get("stage_consolidated_company_snapshot", pd.DataFrame())
    if stage.empty:
        return _empty(SNAPSHOT_COLUMNS)
    xref = dataset.get("xref_portco_internal_ids", pd.DataFrame())
    xref_by_portco = (
        xref.set_index("portco_id", drop=False) if not xref.empty and "portco_id" in xref.columns else pd.DataFrame()
    )
    controls = controls if controls is not None else _empty(CONTROL_COLUMNS)
    asof = pd.to_datetime(asof_date or stage["study_asof_date"].dropna().max() or date.today())

    issuer_ids = []
    if not xref.empty and "findox_issuer_id" in xref.columns:
        issuer_ids = xref["findox_issuer_id"].dropna().astype(str).tolist()
    findox_privacy = fetch_findox_privacy_metadata(issuer_ids, app=findox_app) if issuer_ids else pd.DataFrame()
    privacy_by_issuer = (
        findox_privacy.set_index("findox_issuer_id", drop=False)
        if not findox_privacy.empty
        else pd.DataFrame()
    )

    rows = []
    seen_portcos = set()
    for _, row in stage.drop_duplicates("portco_id").iterrows():
        portco_id = str(row["portco_id"])
        company_name = str(row.get("company") or row.get("company_name") or "")
        seen_portcos.add(portco_id)
        entity = _default_entity(portco_id, company_name)
        xref_row = xref_by_portco.loc[portco_id] if portco_id in xref_by_portco.index else None
        if xref_row is not None and not isinstance(xref_row, pd.Series):
            xref_row = xref_row.iloc[0]
        for control in _match_controls_to_portco(portco_id, company_name, xref_row, controls, asof):
            entity = _apply_control_row(entity, control, "manual_control")
        if xref_row is not None:
            issuer_id = _clean_id(xref_row.get("findox_issuer_id"))
            if issuer_id and issuer_id in privacy_by_issuer.index:
                privacy = privacy_by_issuer.loc[issuer_id]
                if not isinstance(privacy, pd.Series):
                    privacy = privacy.iloc[0]
                if _as_bool(privacy.get("private_only")) or _as_bool(privacy.get("contains_private_data")):
                    entity = _apply_control_row(
                        entity,
                        pd.Series({
                            "private_data_flag": True,
                            "direct_lending_only_flag": True,
                            "restriction_reason": "findox_private_metadata",
                            "restriction_source": "findox_agg_universe",
                        }),
                        "findox_agg_universe",
                    )
        rows.append(_entity_to_row(entity))

    # Include control-only portcos not present in stage (audit trail).
    for _, control in controls.iterrows():
        if not _control_active(control, asof):
            continue
        portco_id = _clean_id(control.get("portco_id"))
        if not portco_id or portco_id in seen_portcos:
            continue
        entity = _default_entity(portco_id, str(control.get("company_name") or ""))
        entity = _apply_control_row(entity, control, "manual_control_only")
        rows.append(_entity_to_row(entity))

    out = pd.DataFrame(rows, columns=SNAPSHOT_COLUMNS)
    for col in ["effective_start_date", "effective_end_date"]:
        out[col] = pd.to_datetime(out[col], errors="coerce")
    return out.drop_duplicates("portco_id").reset_index(drop=True)


def blocked_portco_ids(snapshot: pd.DataFrame) -> set[str]:
    if snapshot.empty:
        return set()
    blocked = snapshot[snapshot["disqualified_lender_blocked_flag"].map(_as_bool)]
    return set(blocked["portco_id"].astype(str).tolist())


def suppress_internal_enrichment(dataset: dict[str, pd.DataFrame], snapshot: pd.DataFrame) -> dict[str, pd.DataFrame]:
    out = dict(dataset)
    blocked = blocked_portco_ids(snapshot)
    if not blocked:
        return out
    for table_name in INTERNAL_ENRICHMENT_TABLES:
        df = out.get(table_name, pd.DataFrame())
        if df.empty or "portco_id" not in df.columns:
            continue
        out[table_name] = df[~df["portco_id"].astype(str).isin(blocked)].reset_index(drop=True)
    return out


def merge_compliance_flags(analysis: pd.DataFrame, snapshot: pd.DataFrame) -> pd.DataFrame:
    if analysis.empty:
        return analysis
    if snapshot.empty:
        for col in SNAPSHOT_COLUMNS:
            if col in {"portco_id", "company_name"}:
                continue
            if col.endswith("_flag"):
                analysis[col] = False
            else:
                analysis[col] = pd.NA
        analysis["visible_to_all_placeholder_flag"] = True
        return analysis
    keep_cols = [col for col in SNAPSHOT_COLUMNS if col != "company_name"]
    merged = analysis.merge(snapshot[keep_cols], on="portco_id", how="left", suffixes=("", "_compliance"))
    for col in [
        "private_data_flag",
        "direct_lending_only_flag",
        "pe_investment_team_restricted_flag",
        "disqualified_lender_blocked_flag",
        "visible_to_all_placeholder_flag",
    ]:
        if col in merged:
            merged[col] = merged[col].map(lambda value: _as_bool(value, default=col != "visible_to_all_placeholder_flag"))
    merged["visible_to_all_placeholder_flag"] = merged["visible_to_all_placeholder_flag"].fillna(True)
    return merged


def filter_dashboard_visible(analysis: pd.DataFrame, snapshot: pd.DataFrame) -> pd.DataFrame:
    merged = merge_compliance_flags(analysis, snapshot)
    if merged.empty or snapshot.empty:
        return merged
    return merged[~merged["disqualified_lender_blocked_flag"]].reset_index(drop=True)


def add_compliance_outputs(
    dataset: dict[str, pd.DataFrame],
    *,
    control_file: str | Path | None = None,
    compliance_mode: str = COMPLIANCE_MODE_OBSERVE,
    findox_app: str = FINDOX_APP,
) -> dict[str, pd.DataFrame]:
    out = dict(dataset)
    controls = read_compliance_control_file(control_file)
    out["source_compliance_company_control"] = controls
    snapshot = build_compliance_snapshot(out, controls, findox_app=findox_app)
    out["fact_company_compliance_snapshot"] = snapshot
    if compliance_mode == COMPLIANCE_MODE_OBSERVE:
        out = suppress_internal_enrichment(out, snapshot)
    else:
        logging.warning("Unknown compliance mode %s; defaulting to observe", compliance_mode)
        out = suppress_internal_enrichment(out, snapshot)
    return out
