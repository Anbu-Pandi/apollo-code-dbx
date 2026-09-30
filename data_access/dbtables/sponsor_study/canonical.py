import hashlib
import re
from pathlib import Path
from typing import Any

import pandas as pd

from data_importer.sponsor_study.constants import STUDY_ASOF_DATE
from data_importer.sponsor_study.excel_openxml import excel_serial_to_datetime


DATE_COLS = [
    "investment_date",
    "first_lien_maturity",
    "second_lien_unsecured_maturity",
    "prior_first_lien_maturity",
    "prior_second_lien_unsecured_maturity",
    "nearest_maturity_date",
    "exit_date",
]

NUMERIC_COLS = [
    "deal_size_tev",
    "entry_ltm_revenue",
    "entry_ltm_ebitda",
    "entry_total_debt",
    "entry_net_debt",
    "equity_commitment",
    "entry_multiple",
    "entry_total_leverage",
    "entry_net_leverage",
    "current_total_debt",
    "current_total_cash",
    "current_net_debt",
    "current_ltm_revenue",
    "current_ltm_ebitda",
    "current_ebitda_margin",
    "current_gross_leverage",
    "current_net_leverage",
    "first_lien_tranche_size",
    "first_lien_price",
    "first_lien_rate",
    "first_lien_ytw",
    "first_lien_ytm",
    "second_lien_unsecured_tranche_size",
    "second_lien_unsecured_price",
    "second_lien_unsecured_rate",
    "second_lien_unsecured_ytw",
    "second_lien_unsecured_ytm",
    "prior_total_debt",
    "prior_total_cash",
    "prior_net_debt",
    "prior_ltm_revenue",
    "prior_ltm_ebitda",
    "prior_gross_leverage",
    "prior_net_leverage",
    "prior_first_lien_tranche_size",
    "prior_first_lien_price",
    "prior_first_lien_rate",
    "prior_first_lien_ytw",
    "prior_first_lien_ytm",
    "prior_second_lien_unsecured_tranche_size",
    "prior_second_lien_unsecured_price",
    "prior_second_lien_unsecured_rate",
    "prior_second_lien_unsecured_ytw",
    "prior_second_lien_unsecured_ytm",
    "yoy_first_lien_ytm_change",
    "yoy_second_lien_unsecured_ytm_change",
    "yoy_revenue_growth",
    "yoy_ebitda_growth",
    "ltv",
]

STAGE_BASE_COLS = [
    "source_row_num",
    "source_excel_row",
    "study_asof_date",
    "sponsor_id",
    "fund_id",
    "portco_id",
    "ownership_id",
    "sponsor",
    "fund_tab",
    "portco_tab",
    "company",
    "company_slug",
    "current_status",
    "hq",
    "private_public",
    "industry",
    "industry_other",
    "investment_date",
    "fund",
    "fund_used",
    "vintage",
    "active_flag",
    "investment_year",
]

WORKBOOK_SCREEN_COLS = [
    "workbook_distressed_candidate",
    "workbook_stressed_candidate",
    "workbook_deleveraging_candidate",
    "workbook_equity_partnership_candidate",
    "workbook_s3_candidate",
    "workbook_amend_extend_candidate",
    "workbook_debt_down_candidate",
    "workbook_debt_up_candidate",
    "workbook_improving_credit_candidate",
]

EXIT_TYPES = {"IPO", "Other", "Secondary", "Sponsor", "Strategic", "Undisclosed"}


def slug(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text or "unknown"


def stable_id(prefix: str, *parts: Any) -> str:
    payload = "|".join("" if pd.isna(p) else str(p).strip().lower() for p in parts)
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _first_present(*values: Any) -> Any:
    for value in values:
        if pd.notna(value):
            text = str(value).strip()
            if text and text.lower() not in {"nan", "none", "na", "n/a"}:
                return value
    return pd.NA


def normalize_exit_type(value: Any, other_value: Any = None) -> Any:
    text = str(value or "").strip()
    if not text or text in {"0", "nan", "None", "-"}:
        text = "Other" if pd.notna(other_value) and str(other_value or "").strip() not in {"", "0", "nan", "None", "-"} else ""
    clean = re.sub(r"\s+", " ", text).strip().lower()
    mapping = {
        "ipo": "IPO",
        "other": "Other",
        "secondary": "Secondary",
        "sponsor": "Sponsor",
        "strategic": "Strategic",
        "undisclosed": "Undisclosed",
    }
    return mapping.get(clean, "Other" if clean else pd.NA)


def _clean_text(series: pd.Series) -> pd.Series:
    out = series.astype("string").str.strip()
    return out.where(~out.str.lower().isin(["", "0", "nan", "none", "-", "–"]), pd.NA)


def _clean_numeric(series: pd.Series) -> pd.Series:
    cleaned = (
        series.astype("string")
        .str.replace(",", "", regex=False)
        .str.replace("$", "", regex=False)
        .str.replace("x", "", regex=False)
        .str.replace("%", "", regex=False)
        .str.strip()
    )
    cleaned = cleaned.where(~cleaned.str.lower().isin(["", "nan", "none", "-", "–"]), pd.NA)
    return pd.to_numeric(cleaned, errors="coerce")


def _clean_pct(series: pd.Series) -> pd.Series:
    out = _clean_numeric(series)
    return out.where(out.abs() <= 1, out / 100)


def normalize_consolidated_df(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in NUMERIC_COLS + DATE_COLS + WORKBOOK_SCREEN_COLS:
        if col not in out:
            out[col] = pd.NA
    for col in ["source_row_num", "source_excel_row", "fund_tab", "portco_tab", "current_status", "active_flag", "investment_year"]:
        if col not in out:
            out[col] = pd.NA
    for col in out.columns:
        if col not in NUMERIC_COLS and col not in DATE_COLS:
            out[col] = _clean_text(out[col])
    for col in NUMERIC_COLS:
        if col in out:
            out[col] = _clean_numeric(out[col])
            out[col] = out[col].where(out[col] != 0)
    pct_cols = [c for c in out.columns if c.endswith("_ytm") or c.endswith("_ytw") or "growth" in c or c.endswith("_rate") or c == "current_ebitda_margin"]
    for col in pct_cols:
        out[col] = _clean_pct(out[col])
    for col in DATE_COLS:
        if col in out:
            out[col] = out[col].map(excel_serial_to_datetime)

    out["study_asof_date"] = pd.Timestamp(STUDY_ASOF_DATE)
    if "active_flag" in out:
        out["active_flag"] = out["active_flag"].astype("string").str.lower().isin(["true", "1", "active", "yes", "y"])
    if "investment_year" in out:
        out["investment_year"] = out["investment_year"].where(pd.notna(out["investment_year"]), out["investment_date"].dt.year)
    out["sponsor_id"] = out["sponsor"].map(lambda x: stable_id("sponsor", x))
    out["fund_id"] = out.apply(lambda r: stable_id("fund", r.get("sponsor"), _first_present(r.get("fund_used"), r.get("fund")), r.get("vintage")), axis=1)
    out["portco_id"] = out.apply(lambda r: stable_id("portco", r.get("company"), r.get("hq"), r.get("sponsor")), axis=1)
    out["ownership_id"] = out.apply(lambda r: stable_id("own", r.get("sponsor"), _first_present(r.get("fund_used"), r.get("fund")), r.get("company"), r.get("investment_date")), axis=1)
    out["company_slug"] = out["company"].map(slug)
    return out


def build_dim_sponsor(df: pd.DataFrame) -> pd.DataFrame:
    cols = ["sponsor_id", "sponsor"]
    return (
        df[cols]
        .dropna(subset=["sponsor"])
        .drop_duplicates()
        .rename(columns={"sponsor": "sponsor_name"})
        .sort_values("sponsor_name")
        .reset_index(drop=True)
    )


def build_stage_consolidated_company_snapshot(df: pd.DataFrame) -> pd.DataFrame:
    """Persist ownership/source identity only; dated metrics live in fact tables."""
    cols = [col for col in [*STAGE_BASE_COLS, *WORKBOOK_SCREEN_COLS] if col in df.columns]
    return df[cols].dropna(subset=["portco_id", "ownership_id"]).drop_duplicates().reset_index(drop=True)


def build_dim_fund(df: pd.DataFrame) -> pd.DataFrame:
    out = df[["fund_id", "sponsor_id", "fund", "fund_used", "vintage", "study_asof_date"]].copy()
    out["fund_name"] = out["fund_used"].combine_first(out["fund"])
    out = out.dropna(subset=["fund_name"]).drop_duplicates()
    holdings = df[["fund_id", "portco_id", "current_status"]].dropna(subset=["fund_id", "portco_id"]).copy()
    holdings["is_active_holding"] = holdings["current_status"].astype("string").str.lower().eq("active")
    fund_counts = holdings.groupby("fund_id", dropna=False).agg(
        total_portfolio_company_count=("portco_id", "nunique"),
        active_portfolio_company_count=("is_active_holding", "sum"),
    ).reset_index()
    out = out.merge(fund_counts, on="fund_id", how="left")
    out["total_portfolio_company_count"] = out["total_portfolio_company_count"].fillna(0).astype(int)
    out["active_portfolio_company_count"] = out["active_portfolio_company_count"].fillna(0).astype(int)
    out["active_fund_flag"] = out["active_portfolio_company_count"] > 0
    out["knowledge_date"] = out["study_asof_date"]
    return out[[
        "fund_id",
        "sponsor_id",
        "knowledge_date",
        "fund_name",
        "vintage",
        "total_portfolio_company_count",
        "active_portfolio_company_count",
        "active_fund_flag",
    ]].reset_index(drop=True)


def build_dim_portco(df: pd.DataFrame) -> pd.DataFrame:
    cols = ["portco_id", "company", "company_slug", "hq", "private_public", "industry", "industry_other"]
    out = df[cols].dropna(subset=["company"]).drop_duplicates()
    return out.rename(columns={"company": "company_name"}).reset_index(drop=True)


def _exit_lookup(exit_df: pd.DataFrame) -> pd.DataFrame:
    if exit_df is None or exit_df.empty:
        return pd.DataFrame(columns=["company_slug", "sponsor_slug", "exit_type", "exit_date", "exit_source_excel_row"])
    out = exit_df.copy()
    out["company_slug"] = out["company"].map(slug)
    out["sponsor_slug"] = out["sponsor"].map(slug)
    out["exit_type"] = out.apply(lambda row: normalize_exit_type(row.get("exit_method"), row.get("exit_other")), axis=1)
    out["exit_date"] = out.get("exit_date", pd.Series(dtype="object")).map(excel_serial_to_datetime)
    out = out.rename(columns={"source_excel_row": "exit_source_excel_row"})
    return out[["company_slug", "sponsor_slug", "exit_type", "exit_date", "exit_source_excel_row"]].drop_duplicates()


def build_bridge_fund_portco(df: pd.DataFrame, exit_df: pd.DataFrame | None = None) -> pd.DataFrame:
    cols = [
        "ownership_id",
        "fund_id",
        "portco_id",
        "sponsor_id",
        "sponsor",
        "company",
        "company_slug",
        "investment_date",
        "current_status",
        "active_flag",
        "source_excel_row",
    ]
    out = df[cols].dropna(subset=["portco_id", "sponsor_id"]).drop_duplicates()
    out["sponsor_slug"] = out["sponsor"].map(slug)
    exits = _exit_lookup(exit_df if exit_df is not None else pd.DataFrame())
    if not exits.empty:
        out = out.merge(exits, on=["company_slug", "sponsor_slug"], how="left")
    else:
        out["exit_type"] = pd.NA
        out["exit_date"] = pd.NaT
        out["exit_source_excel_row"] = pd.NA
    active = out["current_status"].astype("string").str.lower().eq("active")
    out.loc[active, ["exit_type", "exit_date", "exit_source_excel_row"]] = pd.NA
    out["buyout_inclusion_flag"] = True
    out["source_confidence"] = "seed_workbook"
    return out[[
        "ownership_id",
        "fund_id",
        "portco_id",
        "sponsor_id",
        "investment_date",
        "exit_date",
        "exit_type",
        "current_status",
        "active_flag",
        "buyout_inclusion_flag",
        "source_confidence",
        "source_excel_row",
        "exit_source_excel_row",
    ]].reset_index(drop=True)


def build_fact_transaction_entry(df: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "ownership_id",
        "study_asof_date",
        "deal_size_tev",
        "entry_ltm_revenue",
        "entry_ltm_ebitda",
        "entry_total_debt",
        "entry_net_debt",
        "equity_commitment",
        "entry_multiple",
        "entry_total_leverage",
        "entry_net_leverage",
        "source_excel_row",
    ]
    return df[cols].dropna(subset=["ownership_id"]).drop_duplicates().reset_index(drop=True)


def build_fact_financial_snapshot(df: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "portco_id",
        "ownership_id",
        "study_asof_date",
        "current_total_debt",
        "current_total_cash",
        "current_net_debt",
        "current_ltm_revenue",
        "current_ltm_ebitda",
        "current_ebitda_margin",
        "current_gross_leverage",
        "current_net_leverage",
        "yoy_revenue_growth",
        "yoy_ebitda_growth",
        "ltv",
        "source_excel_row",
    ]
    return df[cols].dropna(subset=["portco_id"]).drop_duplicates().reset_index(drop=True)


def build_fact_debt_instrument_snapshot(df: pd.DataFrame) -> pd.DataFrame:
    records = []
    for _, row in df.iterrows():
        for lien, prefix in [("1L", "first_lien"), ("2L / Unsecured", "second_lien_unsecured")]:
            size = row.get(f"{prefix}_tranche_size")
            maturity = row.get(f"{prefix}_maturity")
            if pd.isna(size) and pd.isna(maturity):
                continue
            instrument_id = stable_id("debt", row.get("portco_id"), lien, maturity, size)
            records.append({
                "debt_instrument_id": instrument_id,
                "portco_id": row.get("portco_id"),
                "ownership_id": row.get("ownership_id"),
                "study_asof_date": row.get("study_asof_date"),
                "tranche_type": lien,
                "tranche_size": size,
                "rate": row.get(f"{prefix}_rate"),
                "maturity_date": maturity,
                "nearest_maturity_date": row.get("nearest_maturity_date"),
                "nearest_maturity_type": row.get("nearest_maturity_type"),
                "source_excel_row": row.get("source_excel_row"),
            })
    return pd.DataFrame(records)


def build_fact_market_trading_snapshot(df: pd.DataFrame) -> pd.DataFrame:
    records = []
    for _, row in df.iterrows():
        for lien, prefix in [("1L", "first_lien"), ("2L / Unsecured", "second_lien_unsecured")]:
            if pd.isna(row.get(f"{prefix}_price")) and pd.isna(row.get(f"{prefix}_ytm")):
                continue
            records.append({
                "market_snapshot_id": stable_id("mkt", row.get("portco_id"), lien, row.get("study_asof_date")),
                "debt_instrument_id": stable_id("debt", row.get("portco_id"), lien, row.get(f"{prefix}_maturity"), row.get(f"{prefix}_tranche_size")),
                "portco_id": row.get("portco_id"),
                "study_asof_date": row.get("study_asof_date"),
                "tranche_type": lien,
                "price": row.get(f"{prefix}_price"),
                "ytw": row.get(f"{prefix}_ytw"),
                "ytm": row.get(f"{prefix}_ytm"),
                "prior_price": row.get(f"prior_{prefix}_price"),
                "prior_ytm": row.get(f"prior_{prefix}_ytm"),
                "source_excel_row": row.get("source_excel_row"),
            })
    return pd.DataFrame(records)


def build_fact_exit_liquidity_event(exit_df: pd.DataFrame, portco_df: pd.DataFrame) -> pd.DataFrame:
    if exit_df.empty:
        return pd.DataFrame(columns=["exit_event_id", "portco_id", "sponsor_id", "company", "sponsor", "exit_method", "exit_type", "exit_date", "source_excel_row"])
    exit_df = exit_df.copy()
    exit_df["exit_type"] = exit_df.apply(lambda row: normalize_exit_type(row.get("exit_method"), row.get("exit_other")), axis=1)
    exit_df["exit_date"] = exit_df.get("exit_date", pd.Series(dtype="object")).map(excel_serial_to_datetime)
    lookup = portco_df[["portco_id", "sponsor_id", "company", "sponsor"]].drop_duplicates()
    out = exit_df.merge(lookup, on=["company", "sponsor"], how="left")
    out["exit_event_id"] = out.apply(lambda r: stable_id("exit", r.get("company"), r.get("sponsor"), r.get("exit_method"), r.get("source_excel_row")), axis=1)
    return out[["exit_event_id", "portco_id", "sponsor_id", "company", "sponsor", "exit_method", "exit_type", "exit_date", "source_excel_row"]]


def build_source_evidence(df: pd.DataFrame, workbook_path: str | Path) -> pd.DataFrame:
    source_file = Path(workbook_path).name
    source_cols = ["ownership_id", "portco_id", "source_excel_row"]
    if "source_sheet" in df:
        source_cols.append("source_sheet")
    if "source_system" in df:
        source_cols.append("source_system")
    if "source_file" in df:
        source_cols.append("source_file")
    rows = df[source_cols].drop_duplicates().copy()
    if "source_system" not in rows:
        rows["source_system"] = "sponsor_study_workbook"
    if "source_file" not in rows:
        rows["source_file"] = source_file
    if "source_sheet" not in rows:
        rows["source_sheet"] = "Consolidate Data"
    rows["extraction_method"] = rows["source_system"].map({
        "preqin": "snowflake_structured_feed",
    }).fillna("openxml_cached_values")
    rows["reviewer_status"] = "seed_unreviewed"
    return rows.reset_index(drop=True)


def build_canonical_dataset(
    consolidated_df: pd.DataFrame,
    exit_df: pd.DataFrame | None = None,
    workbook_path: str | Path | None = None,
) -> dict[str, pd.DataFrame]:
    normalized = normalize_consolidated_df(consolidated_df)
    exit_df = exit_df if exit_df is not None else pd.DataFrame()
    dataset = {
        "stage_consolidated_company_snapshot": build_stage_consolidated_company_snapshot(normalized),
        "dim_sponsor": build_dim_sponsor(normalized),
        "dim_fund": build_dim_fund(normalized),
        "dim_portco": build_dim_portco(normalized),
        "bridge_fund_portco": build_bridge_fund_portco(normalized, exit_df),
        "fact_transaction_entry": build_fact_transaction_entry(normalized),
        "fact_financial_snapshot": build_fact_financial_snapshot(normalized),
        "fact_debt_instrument_snapshot": build_fact_debt_instrument_snapshot(normalized),
        "fact_market_trading_snapshot": build_fact_market_trading_snapshot(normalized),
        "fact_exit_liquidity_event": build_fact_exit_liquidity_event(exit_df, normalized),
        "source_evidence": build_source_evidence(normalized, workbook_path or ""),
    }
    return dataset

