import hashlib
import os
from pathlib import Path

import pandas as pd

from data_access.dbtables.sponsor_study.compliance import filter_dashboard_visible
from data_access.dbtables.sponsor_study.screen_rules import build_company_analysis_snapshot
from data_access.dbtables.sponsor_study.persistence import read_dataset_files
from data_importer.sponsor_study.constants import DEFAULT_OUTPUT_DIR  # deprecated local CSV root
from data_access.storage import SharedFrameCache


DEFAULT_DATASET_TABLES = [
    "stage_consolidated_company_snapshot",
    "fact_financial_snapshot",
    "fact_debt_instrument_snapshot",
    "fact_market_trading_snapshot",
    "source_internal_financial_snapshot",
    "source_internal_market_trading_snapshot",
    "source_internal_debt_instrument_snapshot",
    "fact_internal_debt_feature_snapshot",
    "source_internal_debt_issuer_bridge",
    "xref_portco_internal_ids",
    "fact_company_compliance_snapshot",
    "fact_opportunity_screen",
    "validation_reconciliation",
    "validation_website_bridge_reconciliation",
    "source_sponsor_site_holding_candidate",
    "xref_website_holding_to_portco",
    "source_sponsor_site_portfolio_bridge",
]

EXTERNAL_DATASET_TABLES = [
    "source_external_sponsor_scope",
    "dim_external_fund",
    "xref_external_fund_source",
    "fact_external_holding",
    "xref_external_portco_factset",
    "source_external_sponsor_website_holding",
    "source_external_financial_snapshot",
    "source_external_market_trading_snapshot",
    "source_external_debt_instrument_snapshot",
    "source_external_debt_issuer_bridge",
    "fact_external_debt_feature_snapshot",
    "validation_external_enrichment",
]
_shared_cache = None


def _cache():
    global _shared_cache
    if _shared_cache is None:
        _shared_cache = SharedFrameCache.local()
    return _shared_cache


def _dataset_generation(data_dir: Path):
    digest = hashlib.sha256()
    for path in sorted(data_dir.rglob("*.csv")):
        stat = path.stat()
        digest.update(str(path.relative_to(data_dir)).encode())
        digest.update(f"{stat.st_size}:{stat.st_mtime_ns}".encode())
    return digest.hexdigest()


def _dataset_dir() -> Path | None:
    explicit = os.environ.get("SPONSOR_STUDY_DATA_DIR", "").strip()
    if explicit:
        return Path(explicit)
    if DEFAULT_OUTPUT_DIR:
        return Path(DEFAULT_OUTPUT_DIR)
    return None


def load_local_dataset() -> dict[str, pd.DataFrame]:
    data_dir = _dataset_dir()
    if data_dir is None or not data_dir.exists():
        return {}
    generation = _dataset_generation(data_dir)
    cache = _cache()
    manifest = cache.read("sponsor_study", generation, "__manifest__", "tables")
    if manifest is not None:
        entries = cache.read_entries("sponsor_study", generation)
        return {
            table: entries[(table, "frame")]
            for table in manifest["table"].tolist()
        }
    dataset = read_dataset_files(data_dir, file_format="csv")
    cache.write_many([
        ("sponsor_study", generation, table, "frame", frame)
        for table, frame in dataset.items()
    ] + [(
        "sponsor_study",
        generation,
        "__manifest__",
        "tables",
        pd.DataFrame({"table": list(dataset)}),
    )])
    return dataset


def load_sql_dataset(table_names: list[str] | None = None) -> dict[str, pd.DataFrame]:
    from data_access.dbtables.sponsor_study.query import read_table

    dataset = {}
    for table_name in table_names or DEFAULT_DATASET_TABLES:
        dataset[table_name] = read_table(table_name)
    return dataset


def load_dataset() -> dict[str, pd.DataFrame]:
    mode = os.environ.get("SPONSOR_STUDY_DATA_MODE", "").strip().lower()
    if mode == "external":
        return load_external_dataset()
    if os.environ.get("SPONSOR_STUDY_DATA_SOURCE", "").strip().lower() in {"sql", "delta", "databricks"}:
        return load_sql_dataset()
    return load_local_dataset()


def load_external_dataset(table_names: list[str] | None = None) -> dict[str, pd.DataFrame]:
    if os.environ.get("SPONSOR_STUDY_DATA_SOURCE", "").strip().lower() in {"sql", "delta", "databricks"}:
        return load_sql_dataset(table_names or EXTERNAL_DATASET_TABLES)
    data_dir = _dataset_dir()
    if data_dir is None or not data_dir.exists():
        return {}
    dataset = read_dataset_files(data_dir, file_format="csv")
    return {name: dataset[name] for name in (table_names or EXTERNAL_DATASET_TABLES) if name in dataset}


def sponsor_summary(dataset: dict[str, pd.DataFrame] | None = None) -> pd.DataFrame:
    dataset = dataset or load_dataset()
    compliance = dataset.get("fact_company_compliance_snapshot", pd.DataFrame())
    analysis = filter_dashboard_visible(build_company_analysis_snapshot(dataset), compliance)
    if analysis.empty:
        return pd.DataFrame()
    screens = dataset.get("fact_opportunity_screen", pd.DataFrame())
    screen_counts = pd.DataFrame()
    if not screens.empty:
        screen_counts = screens.groupby("sponsor", dropna=False).agg(
            stressed_count=("health_status", lambda x: int((x == "Stressed").sum())),
            distressed_count=("health_status", lambda x: int((x == "Distressed").sum())),
        ).reset_index()
    active = analysis[analysis["current_status"].astype("string").str.lower().eq("active")].copy()
    active["nearest_maturity_date"] = pd.to_datetime(active["nearest_maturity_date"], errors="coerce")
    active["current_net_leverage"] = pd.to_numeric(active["current_net_leverage"], errors="coerce")
    active["first_lien_ytm"] = pd.to_numeric(active["first_lien_ytm"], errors="coerce")
    grouped = active.groupby("sponsor", dropna=False).agg(
        active_investments=("portco_id", "nunique"),
        median_net_leverage=("current_net_leverage", "median"),
        nearest_maturity_date=("nearest_maturity_date", "min"),
        debt_trading_coverage=("first_lien_ytm", lambda x: int(x.notna().sum())),
    ).reset_index()
    if not screen_counts.empty:
        grouped = grouped.merge(screen_counts, on="sponsor", how="left")
    else:
        grouped["stressed_count"] = 0
        grouped["distressed_count"] = 0
    grouped[["stressed_count", "distressed_count"]] = grouped[["stressed_count", "distressed_count"]].fillna(0).astype(int)
    grouped["at_risk_count"] = grouped["stressed_count"] + grouped["distressed_count"]
    grouped["at_risk_pct"] = grouped["at_risk_count"] / grouped["active_investments"]
    return grouped.sort_values(["at_risk_count", "active_investments"], ascending=[False, False]).reset_index(drop=True)


def opportunity_screens(dataset: dict[str, pd.DataFrame] | None = None) -> pd.DataFrame:
    dataset = dataset or load_dataset()
    return dataset.get("fact_opportunity_screen", pd.DataFrame())


def sponsor_portfolio_detail(dataset: dict[str, pd.DataFrame] | None = None, sponsor: str | None = None) -> pd.DataFrame:
    dataset = dataset or load_dataset()
    compliance = dataset.get("fact_company_compliance_snapshot", pd.DataFrame())
    out = filter_dashboard_visible(build_company_analysis_snapshot(dataset), compliance)
    if out.empty:
        return pd.DataFrame()
    if sponsor:
        out = out[out["sponsor"].astype("string").eq(str(sponsor))]
    bridge = dataset.get("source_internal_debt_issuer_bridge", pd.DataFrame())
    if not bridge.empty and "portco_id" in out.columns:
        bridge_cols = [
            "portco_factset_entity_id",
            "debt_factset_entity_id",
            "debt_bridge_match_method",
            "debt_bridge_confidence",
            "debt_bridge_source_system",
            "issuer_name",
        ]
        bridge_cols = [col for col in bridge_cols if col in bridge.columns]
        if bridge_cols:
            bridge_agg = (
                bridge.sort_values(["portco_id", "debt_bridge_confidence"], na_position="last")
                .drop_duplicates("portco_id", keep="last")[["portco_id", *bridge_cols]]
            )
            out = out.merge(bridge_agg, on="portco_id", how="left", suffixes=("", "_bridge"))
    cols = [
        "sponsor",
        "fund",
        "fund_used",
        "company",
        "current_status",
        "industry",
        "investment_date",
        "deal_size_tev",
        "entry_multiple",
        "current_ltm_revenue",
        "current_ltm_ebitda",
        "current_net_leverage",
        "first_lien_ytm",
        "second_lien_unsecured_ytm",
        "nearest_maturity_date",
        "health_status",
        "financial_metric_source_system",
        "market_metric_source_system",
        "debt_metric_source_system",
        "portco_factset_entity_id",
        "debt_factset_entity_id",
        "debt_bridge_match_method",
        "debt_bridge_confidence",
        "debt_bridge_source_system",
        "issuer_name",
        "private_data_flag",
        "direct_lending_only_flag",
        "pe_investment_team_restricted_flag",
        "disqualified_lender_blocked_flag",
        "visible_to_all_placeholder_flag",
    ]
    cols = [col for col in cols if col in out.columns]
    return out[cols].sort_values(["sponsor", "fund", "company"], na_position="last").reset_index(drop=True)


def website_holding_candidates(dataset: dict[str, pd.DataFrame] | None = None, sponsor: str | None = None) -> pd.DataFrame:
    dataset = dataset or load_dataset()
    candidates = dataset.get("source_sponsor_site_holding_candidate", pd.DataFrame())
    if candidates.empty:
        return pd.DataFrame()
    out = candidates.copy()
    if sponsor:
        out = out[out["sponsor"].astype("string").eq(str(sponsor))]
    return out.sort_values(["sponsor", "company_name"], na_position="last").reset_index(drop=True)


def website_holding_matches(dataset: dict[str, pd.DataFrame] | None = None, sponsor: str | None = None) -> pd.DataFrame:
    dataset = dataset or load_dataset()
    matches = dataset.get("xref_website_holding_to_portco", pd.DataFrame())
    if matches.empty:
        return pd.DataFrame()
    out = matches.copy()
    if sponsor:
        out = out[out["sponsor"].astype("string").eq(str(sponsor))]
    return out.sort_values(["sponsor", "website_company_name"], na_position="last").reset_index(drop=True)


def website_portfolio_bridge(dataset: dict[str, pd.DataFrame] | None = None, sponsor: str | None = None) -> pd.DataFrame:
    dataset = dataset or load_dataset()
    bridge = dataset.get("source_sponsor_site_portfolio_bridge", pd.DataFrame())
    if bridge.empty:
        return pd.DataFrame()
    out = bridge.copy()
    if sponsor:
        out = out[out["sponsor"].astype("string").eq(str(sponsor))]
    return out.sort_values(["sponsor", "fund_name", "company_name"], na_position="last").reset_index(drop=True)


def website_bridge_reconciliation(dataset: dict[str, pd.DataFrame] | None = None) -> pd.DataFrame:
    dataset = dataset or load_dataset()
    return dataset.get("validation_website_bridge_reconciliation", pd.DataFrame())


def debt_issuer_bridge(dataset: dict[str, pd.DataFrame] | None = None, sponsor: str | None = None) -> pd.DataFrame:
    dataset = dataset or load_dataset()
    bridge = dataset.get("source_internal_debt_issuer_bridge", pd.DataFrame())
    if bridge.empty:
        return pd.DataFrame()
    out = bridge.copy()
    if sponsor:
        stage = dataset.get("stage_consolidated_company_snapshot", pd.DataFrame())
        if not stage.empty and "portco_id" in stage.columns:
            sponsor_portcos = stage.loc[
                stage["sponsor"].astype("string").eq(str(sponsor)),
                "portco_id",
            ].dropna().astype("string").unique()
            out = out[out["portco_id"].astype("string").isin(sponsor_portcos)]
    sort_cols = [col for col in ["portco_id", "debt_bridge_confidence", "issuer_name"] if col in out.columns]
    return out.sort_values(sort_cols, na_position="last").reset_index(drop=True)


def data_quality(dataset: dict[str, pd.DataFrame] | None = None) -> pd.DataFrame:
    dataset = dataset or load_dataset()
    reconciliation_df = dataset.get("validation_reconciliation", pd.DataFrame())
    if reconciliation_df.empty:
        return pd.DataFrame()
    metrics = reconciliation_df[reconciliation_df["metric"].astype("string").str.contains("coverage|match|maturity", case=False, na=False)]
    return metrics.reset_index(drop=True)


def reconciliation(dataset: dict[str, pd.DataFrame] | None = None) -> pd.DataFrame:
    dataset = dataset or load_dataset()
    return dataset.get("validation_reconciliation", pd.DataFrame())


def external_holdings(dataset: dict[str, pd.DataFrame] | None = None, sponsor: str | None = None) -> pd.DataFrame:
    dataset = dataset or load_external_dataset()
    holdings = dataset.get("fact_external_holding", pd.DataFrame())
    if holdings.empty:
        return pd.DataFrame()
    out = holdings[holdings["canonical_holding_flag"].eq(True)].copy() if "canonical_holding_flag" in holdings.columns else holdings.copy()
    if sponsor:
        out = out[out["sponsor_name"].astype("string").eq(str(sponsor))]
    return out.sort_values(["sponsor_name", "fund_name", "company_name"], na_position="last").reset_index(drop=True)


def external_company_analysis(dataset: dict[str, pd.DataFrame] | None = None, sponsor: str | None = None) -> pd.DataFrame:
    holdings = external_holdings(dataset, sponsor=sponsor)
    if holdings.empty:
        return pd.DataFrame()
    financial = (dataset or load_external_dataset()).get("source_external_financial_snapshot", pd.DataFrame())
    market = (dataset or load_external_dataset()).get("source_external_market_trading_snapshot", pd.DataFrame())
    debt = (dataset or load_external_dataset()).get("source_external_debt_instrument_snapshot", pd.DataFrame())
    out = holdings.copy()
    if not financial.empty:
        out = out.merge(financial, on="external_portco_id", how="left", suffixes=("", "_fin"))
    if not market.empty:
        out = out.merge(market, on="external_portco_id", how="left", suffixes=("", "_mkt"))
    if not debt.empty:
        out = out.merge(debt, on="external_portco_id", how="left", suffixes=("", "_debt"))
    return out


def external_source_coverage(dataset: dict[str, pd.DataFrame] | None = None) -> pd.DataFrame:
    dataset = dataset or load_external_dataset()
    return dataset.get("validation_external_enrichment", pd.DataFrame())

