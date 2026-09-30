"""Canonical holding-source priority for sponsor-study representatives."""

from __future__ import annotations

from collections.abc import Mapping


HOLDING_SOURCE_PRIORITY = (
    "factset_pevc",
    "dealogic",
    "sponsor_website",
    "pitchbook",
)
HOLDING_SOURCE_PRIORITY_BY_SYSTEM: Mapping[str, int] = {
    source: rank for rank, source in enumerate(HOLDING_SOURCE_PRIORITY)
}
UNKNOWN_HOLDING_SOURCE_PRIORITY = 9


def holding_source_priority(source_system: object) -> int:
    return HOLDING_SOURCE_PRIORITY_BY_SYSTEM.get(
        str(source_system),
        UNKNOWN_HOLDING_SOURCE_PRIORITY,
    )


def holding_source_priority_sql(column: str) -> str:
    branches = "\n".join(
        f"    WHEN '{source}' THEN {rank}"
        for source, rank in HOLDING_SOURCE_PRIORITY_BY_SYSTEM.items()
    )
    return (
        f"CASE {column}\n"
        f"{branches}\n"
        f"    ELSE {UNKNOWN_HOLDING_SOURCE_PRIORITY}\n"
        "END"
    )
