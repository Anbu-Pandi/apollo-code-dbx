import math
import re
from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class Principal:
    subject: str
    email: str
    groups: tuple = ()

    def __post_init__(self):
        if not self.subject or not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", self.email):
            raise ValueError("Sign in with a work email to manage subscriptions.")


@dataclass(frozen=True)
class Filter:
    key: str
    label: str
    kind: str = "text"
    default: object = ""
    choices: tuple = ()
    minimum: float = 0
    maximum: float = 1e12

    def clean(self, value):
        value = self.default if value is None else value
        if self.kind == "bool":
            if not isinstance(value, bool):
                raise ValueError(f"Invalid {self.label}.")
        elif self.kind == "number":
            try:
                value = float(value)
            except (TypeError, ValueError):
                raise ValueError(f"Invalid {self.label}.") from None
            if not math.isfinite(value) or not self.minimum <= value <= self.maximum:
                raise ValueError(f"{self.label} must be between {self.minimum:g} and {self.maximum:g}.")
        elif self.kind == "choice":
            if value not in [item[0] for item in self.choices]:
                raise ValueError(f"Invalid {self.label}.")
        else:
            if not isinstance(value, str) or len(value) > 250:
                raise ValueError(f"{self.label} must be at most 250 characters.")
            value = value.strip()
        return value


@dataclass(frozen=True)
class Digest:
    html: str
    text: str
    fingerprint: str
    count: int


@dataclass(frozen=True)
class Report:
    id: str
    title: str
    description: str
    path: str
    build: Callable
    filters: tuple[Filter, ...] = ()
    delivery_authorizer: Callable | None = None

    def clean_filters(self, values):
        if not isinstance(values, dict) or set(values) - {item.key for item in self.filters}:
            raise ValueError("Unknown report filters.")
        return {item.key: item.clean(values.get(item.key)) for item in self.filters}


@dataclass
class Registry:
    reports: dict = field(default_factory=dict)

    def register(self, report):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", report.id) or report.id in self.reports:
            raise ValueError("Report identifiers must be unique and use lowercase letters, numbers or underscores.")
        self.reports[report.id] = report

    def get(self, report_id):
        if report_id not in self.reports:
            raise ValueError("Choose an available report.")
        return self.reports[report_id]
