"""Company-scoped instrument aliases supported by exact source identifiers."""
from collections import defaultdict
from dataclasses import dataclass
import re

import pandas as pd

ALIAS_COLUMN = "factset_instrument_aliases"


def loan_debt_key_ctes_sql() -> str:
    """Resolve retained LoanX keys to one whole row in debt_observations.

    Test key ambiguity before excluding duplicate canonical source rows, so an
    excluded competitor cannot make an otherwise ambiguous alias look unique.
    """
    keys = "\nUNION ALL\n".join(
        f"SELECT UPPER(TRIM({column})) AS loanx_id, "
        "UPPER(TRIM(instrument_id_uniq)) AS canonical_instrument_id FROM debt_observations "
        f"WHERE {column} IS NOT NULL"
        for column in ("instrument_id_uniq", "instrument_id", "instrument_id_index", "instrument_id_uniq_index"))
    return f"""raw_debt_loanx_keys AS ({keys}), debt_base AS (
    SELECT * FROM debt_observations
    QUALIFY COUNT(*) OVER (PARTITION BY UPPER(TRIM(instrument_id_uniq)))=1
), unique_debt_loanx_keys AS (
    SELECT loanx_id,MIN(canonical_instrument_id) AS canonical_instrument_id
    FROM raw_debt_loanx_keys WHERE loanx_id LIKE 'LX%'
    GROUP BY loanx_id HAVING COUNT(DISTINCT canonical_instrument_id)=1
), debt_loanx_keys AS (
    SELECT * FROM unique_debt_loanx_keys WHERE canonical_instrument_id IN (
        SELECT canonical_instrument_id FROM raw_debt_loanx_keys WHERE loanx_id LIKE 'LX%'
        GROUP BY canonical_instrument_id HAVING COUNT(DISTINCT loanx_id)=1
    )
)"""


@dataclass(frozen=True, order=True)
class InstrumentAlias:
    portco_id: str
    factset_id: str
    instrument_id: str

    def __post_init__(self) -> None:
        if any(not re.fullmatch(r"[A-Za-z0-9_-]+", value)
               for value in (self.portco_id, self.factset_id, self.instrument_id)):
            raise ValueError("Instrument aliases require valid source identifiers")


def unambiguous_aliases(observations: tuple[InstrumentAlias, ...]) -> tuple[InstrumentAlias, ...]:
    targets: dict[tuple[str, str], set[str]] = defaultdict(set)
    for item in observations:
        targets[item.portco_id, item.factset_id].add(item.instrument_id)
    return tuple(sorted(item for item in set(observations)
                        if len(targets[item.portco_id, item.factset_id]) == 1))


def aliases_from_exact_rows(exact: pd.DataFrame) -> tuple[InstrumentAlias, ...]:
    if exact.empty:
        return ()
    rows = exact[["portco_id", "factset_instrument_id", "instrument_id_uniq"]].dropna().drop_duplicates()
    return unambiguous_aliases(tuple(InstrumentAlias(str(row.portco_id), str(row.factset_instrument_id),
                                                    str(row.instrument_id_uniq)) for row in rows.itertuples()))


def with_instrument_aliases(features: pd.DataFrame, observations: tuple[InstrumentAlias, ...]) -> pd.DataFrame:
    """Attach source-key metadata while preserving every vendor metric in its source row."""
    aliases = unambiguous_aliases(observations)
    if not aliases:
        return features
    rows = pd.DataFrame([(a.portco_id, a.instrument_id, a.factset_id) for a in aliases],
                        columns=["portco_id", "instrument_id_uniq", ALIAS_COLUMN])
    rows = rows.groupby(["portco_id", "instrument_id_uniq"], as_index=False)[ALIAS_COLUMN].agg(
        lambda values: "|".join(sorted(set(values))))
    return features.merge(rows, on=["portco_id", "instrument_id_uniq"], how="left", validate="many_to_one")


def canonical_dcs_instrument_ids(dcs: pd.DataFrame, features: pd.DataFrame) -> pd.Series:
    """Use only retained exact instrument aliases; parent/company names cannot authorize one."""
    if ALIAS_COLUMN not in features:
        return dcs.factset_instrument_id.copy()
    rows = features[["portco_id", "instrument_id_uniq", ALIAS_COLUMN]].dropna().drop_duplicates()
    observations = tuple(InstrumentAlias(str(row.portco_id), alias, str(row.instrument_id_uniq))
                         for row in rows.itertuples(index=False)
                         for alias in getattr(row, ALIAS_COLUMN).split("|"))
    mapping = {(a.portco_id, a.factset_id): a.instrument_id for a in unambiguous_aliases(observations)}
    return pd.Series([mapping.get((str(row.portco_id), str(row.factset_instrument_id)), row.factset_instrument_id)
                      for row in dcs.itertuples()], index=dcs.index, dtype="object")
