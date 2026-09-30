import json
from importlib.resources import files


_RULES = json.loads(
    files(__package__).joinpath("data_catalog_rules.json").read_text(encoding="utf-8")
)

DATA_CATALOG_PREFIX_RULES = tuple(map(tuple, _RULES["prefix_rules"]))
DATA_CATALOG_PROVIDER_RULES = tuple(map(tuple, _RULES["provider_rules"]))
DATA_CATALOG_CATEGORY_DESCRIPTIONS = _RULES["category_descriptions"]
