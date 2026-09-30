"""Cash debt rows eligible for priced instruments and capital-structure totals."""
import pandas as pd

CASH_DEBT_TYPES = frozenset({"bond", "loan", "convertible bond", "asset-backed", "municipal bonds"})
DCS_CASH_DEBT_TYPES = frozenset({"BD", "BC", "LN", "AB", "MB", "MU", "FM", "CA"})


def identifiable_cash_debt_rows(features: pd.DataFrame) -> pd.DataFrame:
    if features.empty:
        return features.copy()
    rows = features.rename(columns=lambda name: str(name).lower())
    required = {"instrument_type", "instrument_id_uniq"}
    missing = required - set(rows.columns)
    if missing:
        raise ValueError(f"Debt instrument eligibility requires {sorted(missing)}")
    kinds = rows.instrument_type.astype("string").str.strip().str.lower()
    identifiers = rows.instrument_id_uniq.astype("string").str.strip()
    return rows.loc[kinds.isin(CASH_DEBT_TYPES) & identifiers.notna() & identifiers.ne("")].copy()


def identifiable_dcs_cash_debt_rows(detail: pd.DataFrame) -> pd.DataFrame:
    """DCS adjustments, preferred equity and balance-sheet subtotals are not instruments."""
    if detail.empty:
        return detail.copy()
    from data_access.dbtables.sponsor_study.factset_filing_attribution import admitted_dcs_attribution_rows
    rows = admitted_dcs_attribution_rows(detail.rename(columns=lambda name: str(name).lower()))
    missing = {"issue_type", "factset_instrument_id", "debt_description"} - set(rows.columns)
    if missing:
        raise ValueError(f"DCS instrument eligibility requires {sorted(missing)}")
    kinds = rows.issue_type.astype("string").str.strip().str.upper()
    identifiers = rows.factset_instrument_id.astype("string").str.strip()
    adjustment = rows.debt_description.astype("string").str.contains(r"(?i)adjustment|\bavail adj\b", na=False)
    return rows.loc[kinds.isin(DCS_CASH_DEBT_TYPES) & ~adjustment & identifiers.notna() & identifiers.ne("")].copy()
