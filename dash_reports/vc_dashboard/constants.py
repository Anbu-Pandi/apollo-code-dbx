WINDOW_OPTIONS = [
    {"label": "1D", "value": 1},
    {"label": "1W", "value": 7},
    {"label": "1M", "value": 30},
    {"label": "3M", "value": 90},
    {"label": "1Y", "value": 365},
]
DEFAULT_WINDOW = 90
NEWS_DEFAULT_WINDOW = 7

HEATMAP_AXIS_OPTIONS = [
    {"label": "Round Stage", "value": "round_type"},
    {"label": "Sub-sector", "value": "sub_sector"},
    {"label": "Region", "value": "region"},
    {"label": "Deal Size", "value": "deal_size_band"},
    {"label": "Time", "value": "time_bucket"},
]
DEFAULT_AXIS = "round_type"

HEATMAP_METRIC_OPTIONS = [
    {"label": "Number of deals", "value": "count"},
    {"label": "Deal value", "value": "size"},
]

BACKERS_MIN_SIZE_OPTIONS = [
    {"label": "All sizes", "value": 0},
    {"label": "≥ $25M", "value": 25e6},
    {"label": "≥ $50M", "value": 50e6},
    {"label": "≥ $100M", "value": 100e6},
    {"label": "≥ $250M", "value": 250e6},
    {"label": "≥ $1B", "value": 1e9},
]

TAB_FUNDING = "funding"
TAB_FIRMS = "firms"
TAB_DEALS = "deals"
TAB_GEO = "geo"
TAB_NEWS = "news"
TAB_SOURCES = "sources"

SOURCE_TYPES = ["blog", "newsletter", "media", "x_handle"]
