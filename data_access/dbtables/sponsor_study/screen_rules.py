import pandas as pd

from data_access.dbtables.sponsor_study.canonical import stable_id
from data_access.dbtables.sponsor_study.compliance import filter_dashboard_visible, merge_compliance_flags


RULE_VERSION = "2026_seed_v1"

SCREEN_RULES = [
    {"screen_name": "distressed", "rule_version": RULE_VERSION, "definition": "Total debt > $500mm and 1L YTM > 14% or 2L/unsecured YTM > 20%."},
    {"screen_name": "stressed", "rule_version": RULE_VERSION, "definition": "Total debt > $500mm and 10% < 1L YTM < 14% or 13% < 2L/unsecured YTM < 20%."},
    {"screen_name": "deleveraging", "rule_version": RULE_VERSION, "definition": "Total debt > $750mm, net leverage > 3.0x, nearest funded debt maturity on or before configurable cutoff."},
    {"screen_name": "equity_partnership", "rule_version": RULE_VERSION, "definition": "Total debt > $500mm, net leverage > 3.0x, and investment hold > 3 years."},
    {"screen_name": "declining_credit", "rule_version": RULE_VERSION, "definition": "Total debt > $500mm and YoY 1L YTM increase >= 5 points or YoY 2L/unsecured YTM increase >= 10 points."},
    {"screen_name": "improving_credit", "rule_version": RULE_VERSION, "definition": "Total debt > $500mm and YoY 1L YTM decrease >= 5 points or YoY 2L/unsecured YTM decrease >= 10 points."},
    {"screen_name": "s3", "rule_version": RULE_VERSION, "definition": "Seeded from workbook S3 candidate formula pending PE rule signoff."},
    {"screen_name": "amend_extend", "rule_version": RULE_VERSION, "definition": "Seeded from workbook Amend & Extend candidate formula pending PE rule signoff."},
    {"screen_name": "debt_down", "rule_version": RULE_VERSION, "definition": "Seeded from workbook Debt Down candidate formula pending PE rule signoff."},
    {"screen_name": "debt_up", "rule_version": RULE_VERSION, "definition": "Seeded from workbook Debt Up candidate formula pending PE rule signoff."},
]


def _is_true(value) -> bool:
    if pd.isna(value):
        return False
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    return text in {"1", "x", "y", "yes", "true", "candidate"}


def _years_held(row: pd.Series, asof_date: pd.Timestamp) -> float | None:
    investment_date = row.get("investment_date")
    if pd.isna(investment_date):
        return None
    return float((asof_date - investment_date).days / 365.25)


def _has_maturity_before(row: pd.Series, cutoff: pd.Timestamp) -> bool:
    maturity = row.get("nearest_maturity_date")
    return pd.notna(maturity) and maturity <= cutoff


def classify_health(row: pd.Series) -> str:
    total_debt = row.get("current_total_debt")
    first_lien_ytm = row.get("first_lien_ytm")
    second_lien_ytm = row.get("second_lien_unsecured_ytm")
    if pd.isna(total_debt) or total_debt <= 500:
        return "Performing / Other"
    if (pd.notna(first_lien_ytm) and first_lien_ytm > 0.14) or (pd.notna(second_lien_ytm) and second_lien_ytm > 0.20):
        return "Distressed"
    if (pd.notna(first_lien_ytm) and 0.10 < first_lien_ytm < 0.14) or (pd.notna(second_lien_ytm) and 0.13 < second_lien_ytm < 0.20):
        return "Stressed"
    return "Performing / Other"


def _latest_by(df: pd.DataFrame, keys: list[str], date_col: str = "study_asof_date") -> pd.DataFrame:
    if df.empty:
        return df.copy()
    out = df.copy()
    if date_col in out.columns:
        out[date_col] = pd.to_datetime(out[date_col], errors="coerce")
        out = out.sort_values([*keys, date_col], na_position="last")
    return out.drop_duplicates(subset=keys, keep="last").reset_index(drop=True)


def _market_snapshot_wide(market: pd.DataFrame) -> pd.DataFrame:
    if market.empty:
        return pd.DataFrame()
    latest = _latest_by(market, ["portco_id", "tranche_type"])
    records = []
    for _, row in latest.iterrows():
        prefix = "first_lien" if str(row.get("tranche_type") or "").startswith("1L") else "second_lien_unsecured"
        records.append({
            "portco_id": row.get("portco_id"),
            f"{prefix}_price": row.get("price"),
            f"{prefix}_ytw": row.get("ytw"),
            f"{prefix}_ytm": row.get("ytm"),
            f"prior_{prefix}_price": row.get("prior_price"),
            f"prior_{prefix}_ytm": row.get("prior_ytm"),
            f"yoy_{prefix}_ytm_change": row.get("ytm") - row.get("prior_ytm")
            if pd.notna(row.get("ytm")) and pd.notna(row.get("prior_ytm"))
            else pd.NA,
        })
    if not records:
        return pd.DataFrame()
    return pd.DataFrame(records).groupby("portco_id", dropna=False).first().reset_index()


def _coalesce_overlay(
    base: pd.DataFrame,
    overlay: pd.DataFrame,
    keys: list[str],
    columns: list[str],
    source_col: str,
    *,
    fallback_to_base: bool = True,
) -> pd.DataFrame:
    if overlay.empty:
        for col in columns:
            if col in base:
                continue
            base[col] = pd.NA
        base[source_col] = "workbook" if fallback_to_base else pd.NA
        return base
    overlay_cols = [col for col in columns if col in overlay.columns]
    if not overlay_cols:
        return base
    latest = _latest_by(overlay, keys, date_col="asof_date")
    keep_cols = [*keys, *overlay_cols]
    if "source_system" in latest:
        keep_cols.append("source_system")
    out = base.merge(latest[keep_cols], on=keys, how="left", suffixes=("", "_live"))
    live_source = out.get("source_system", pd.Series(pd.NA, index=out.index))
    for col in overlay_cols:
        live_col = f"{col}_live"
        if live_col in out:
            out[col] = (
                out[live_col].combine_first(out[col] if col in out else pd.Series(pd.NA, index=out.index))
                if fallback_to_base
                else out[live_col]
            )
            out = out.drop(columns=live_col)
    out[source_col] = live_source.where(live_source.notna(), "workbook") if fallback_to_base else live_source
    return out.drop(columns=["source_system"], errors="ignore")


def build_company_analysis_snapshot(dataset: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Join dated fact tables to ownership rows for screens/views at query time."""
    base = dataset.get("stage_consolidated_company_snapshot", pd.DataFrame()).copy()
    if base.empty:
        return base

    base = _coalesce_overlay(
        base,
        dataset.get("source_internal_financial_snapshot", pd.DataFrame()),
        ["portco_id"],
        [
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
        ],
        "financial_metric_source_system",
        fallback_to_base=False,
    )

    market = _market_snapshot_wide(dataset.get("fact_market_trading_snapshot", pd.DataFrame()))
    if not market.empty:
        base = base.merge(market, on="portco_id", how="left")
    internal_market = _market_snapshot_wide(dataset.get("source_internal_market_trading_snapshot", pd.DataFrame()))
    base = _coalesce_overlay(
        base,
        internal_market.assign(source_system="internal_market") if not internal_market.empty else internal_market,
        ["portco_id"],
        [
            "first_lien_price",
            "first_lien_ytw",
            "first_lien_ytm",
            "prior_first_lien_price",
            "prior_first_lien_ytm",
            "second_lien_unsecured_price",
            "second_lien_unsecured_ytw",
            "second_lien_unsecured_ytm",
            "prior_second_lien_unsecured_price",
            "prior_second_lien_unsecured_ytm",
            "yoy_first_lien_ytm_change",
            "yoy_second_lien_unsecured_ytm_change",
        ],
        "market_metric_source_system",
    )

    debt = dataset.get("fact_debt_instrument_snapshot", pd.DataFrame())
    if not debt.empty:
        debt_latest = debt.sort_values("nearest_maturity_date", na_position="last").drop_duplicates(
            subset=["ownership_id", "portco_id"],
            keep="first",
        )
        base = base.merge(
            debt_latest[["ownership_id", "portco_id", "nearest_maturity_date", "nearest_maturity_type"]],
            on=["ownership_id", "portco_id"],
            how="left",
        )
    internal_debt = dataset.get("source_internal_debt_instrument_snapshot", pd.DataFrame())
    if not internal_debt.empty:
        internal_debt_latest = internal_debt.sort_values(
            ["portco_id", "nearest_maturity_date", "asof_date"],
            na_position="last",
        ).drop_duplicates(subset=["portco_id"], keep="first")
        base = base.merge(
            internal_debt_latest[["portco_id", "nearest_maturity_date", "nearest_maturity_type", "source_system"]],
            on="portco_id",
            how="left",
            suffixes=("", "_live"),
        )
        for col in ["nearest_maturity_date", "nearest_maturity_type"]:
            base[col] = base[f"{col}_live"].combine_first(base[col] if col in base else pd.Series(pd.NA, index=base.index))
            base = base.drop(columns=f"{col}_live")
        base["debt_metric_source_system"] = base["source_system"].where(base["source_system"].notna(), "workbook")
        base = base.drop(columns=["source_system"], errors="ignore")
    else:
        base["debt_metric_source_system"] = "workbook"

    compliance = dataset.get("fact_company_compliance_snapshot", pd.DataFrame())
    return merge_compliance_flags(base, compliance)


def evaluate_screens(
    company_snapshot: pd.DataFrame,
    asof_date: str | pd.Timestamp | None = None,
    maturity_cutoff: str | pd.Timestamp = "2028-12-31",
    compliance_snapshot: pd.DataFrame | None = None,
) -> pd.DataFrame:
    df = company_snapshot.copy()
    if compliance_snapshot is not None and not compliance_snapshot.empty:
        df = filter_dashboard_visible(df, compliance_snapshot)
    if df.empty:
        return pd.DataFrame()

    asof = pd.to_datetime(asof_date or df["study_asof_date"].dropna().max())
    maturity_cutoff = pd.to_datetime(maturity_cutoff)
    records = []
    for _, row in df.iterrows():
        health = classify_health(row)
        years_held = _years_held(row, asof)
        total_debt = row.get("current_total_debt")
        leverage = row.get("current_net_leverage")
        yoy_1l = row.get("yoy_first_lien_ytm_change")
        yoy_2l = row.get("yoy_second_lien_unsecured_ytm_change")

        rules = {
            "distressed": health == "Distressed",
            "stressed": health == "Stressed",
            "deleveraging": pd.notna(total_debt) and total_debt > 750 and pd.notna(leverage) and leverage > 3.0 and _has_maturity_before(row, maturity_cutoff),
            "equity_partnership": pd.notna(total_debt) and total_debt > 500 and pd.notna(leverage) and leverage > 3.0 and years_held is not None and years_held > 3.0,
            "declining_credit": pd.notna(total_debt) and total_debt > 500 and ((pd.notna(yoy_1l) and yoy_1l >= 0.05) or (pd.notna(yoy_2l) and yoy_2l >= 0.10)),
            "improving_credit": pd.notna(total_debt) and total_debt > 500 and ((pd.notna(yoy_1l) and yoy_1l <= -0.05) or (pd.notna(yoy_2l) and yoy_2l <= -0.10)),
            "s3": _is_true(row.get("workbook_s3_candidate")),
            "amend_extend": _is_true(row.get("workbook_amend_extend_candidate")),
            "debt_down": _is_true(row.get("workbook_debt_down_candidate")),
            "debt_up": _is_true(row.get("workbook_debt_up_candidate")),
        }

        for screen_name, is_candidate in rules.items():
            if not is_candidate:
                continue
            records.append({
                "opportunity_screen_id": stable_id("screen", row.get("ownership_id"), screen_name, RULE_VERSION),
                "rule_version": RULE_VERSION,
                "screen_name": screen_name,
                "ownership_id": row.get("ownership_id"),
                "portco_id": row.get("portco_id"),
                "sponsor_id": row.get("sponsor_id"),
                "company": row.get("company"),
                "sponsor": row.get("sponsor"),
                "fund": row.get("fund_used") or row.get("fund"),
                "industry": row.get("industry"),
                "health_status": health,
                "total_debt": total_debt,
                "net_leverage": leverage,
                "first_lien_price": row.get("first_lien_price"),
                "first_lien_ytm": row.get("first_lien_ytm"),
                "second_lien_unsecured_price": row.get("second_lien_unsecured_price"),
                "second_lien_unsecured_ytm": row.get("second_lien_unsecured_ytm"),
                "nearest_maturity_date": row.get("nearest_maturity_date"),
                "investment_date": row.get("investment_date"),
                "years_held": years_held,
                "source_excel_row": row.get("source_excel_row"),
            })
    out = pd.DataFrame(records)
    if out.empty:
        return out
    sort_cols = ["screen_name", "total_debt", "net_leverage"]
    out = out.sort_values(sort_cols, ascending=[True, False, False], na_position="last").reset_index(drop=True)
    out["screen_rank"] = out.groupby("screen_name").cumcount() + 1
    return out


def add_screen_outputs(dataset: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    out = dict(dataset)
    compliance = out.get("fact_company_compliance_snapshot", pd.DataFrame())
    analysis_snapshot = build_company_analysis_snapshot(out)
    out["dim_opportunity_screen_rule"] = pd.DataFrame(SCREEN_RULES)
    out["fact_opportunity_screen"] = evaluate_screens(
        analysis_snapshot,
        compliance_snapshot=compliance,
    )
    return out

