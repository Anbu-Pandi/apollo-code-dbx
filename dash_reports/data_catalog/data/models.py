import base64
import json
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class AssetKey:
    source: str
    connection: str
    catalog: str
    schema: str
    name: str

    @property
    def full_name(self):
        return f"{self.catalog}.{self.schema}.{self.name}"

    @property
    def asset_id(self):
        raw = json.dumps(
            [self.source, self.connection, self.catalog, self.schema, self.name],
            separators=(",", ":"),
        ).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @classmethod
    def from_id(cls, asset_id):
        raw = base64.urlsafe_b64decode(asset_id + "=" * (-len(asset_id) % 4))
        return cls(*json.loads(raw))


@dataclass(frozen=True)
class SourceCapabilities:
    columns: bool = True
    tags: bool = False
    lineage: bool = False
    quality: bool = False
    usage: bool = False
    access: bool = True

    def to_dict(self):
        return asdict(self)


ASSET_COLUMNS = [
    "asset_id",
    "source",
    "connection",
    "catalog",
    "schema",
    "name",
    "full_name",
    "object_type",
    "owner",
    "comment",
    "tags",
    "vendor",
    "data_category",
    "category_description",
    "business_description",
    "example_fields",
    "asset_kind",
    "domain",
    "provider",
    "created",
    "last_altered",
    "data_format",
    "row_count",
    "bytes",
    "column_count",
    "column_search",
    "documented",
    "certified",
    "has_lineage",
    "has_quality",
    "has_usage",
    "queries_30d",
    "queries_90d",
    "last_accessed",
    "service_can_select",
    "ranking_tier",
    "ranking_score",
    "capabilities",
]


def asset_record(key: AssetKey, capabilities: SourceCapabilities, **values: Any):
    record = {column: "" for column in ASSET_COLUMNS}
    record.update(
        asset_id=key.asset_id,
        source=key.source,
        connection=key.connection,
        catalog=key.catalog,
        schema=key.schema,
        name=key.name,
        full_name=key.full_name,
        capabilities=capabilities.to_dict(),
    )
    record.update(values)
    return record
