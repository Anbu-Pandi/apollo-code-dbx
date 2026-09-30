import os

APP = 'industry_tracker_server'
DATA_APP = 'factset'

DEFAULT_INDUSTRY = 'consumer'

PARQUET_PUBLIC_COMPS = 'public_comps.parquet'
PARQUET_LT_HISTORICAL = 'lt_historical.parquet'
PARQUET_CREDIT_LOANS = 'credit_loans.parquet'
PARQUET_CREDIT_BONDS = 'credit_bonds.parquet'
PARQUET_EARNINGS_CALLS = 'earnings_calls.parquet'
PARQUET_EARNINGS_EPS = 'earnings_eps.parquet'
JSON_WWR_WWO = 'wwr_wwo.json'
JSON_MACRO_ALTD = 'macro_altd.json'
JSON_EARNINGS_COMMENTARY = 'earnings_commentary.json'

CREDIT_LOAN_APP = 'databricks'
CREDIT_BOND_APP = 'databricks'

URL_PATH = '/industry-tracker'
FLASK_HTML_PATH = '/industry-tracker-html'

TAB_KEYS = ['wwr', 'wwo', 'macro', 'public-comps', 'credit-comps', 'lt-historical']
TAB_LABELS = {
    'wwr': "What We're Reading",
    'wwo': "What We're Working On",
    'macro': 'Macro Overview',
    'public-comps': 'Public Comps',
    'credit-comps': 'Credit Comps',
    'lt-historical': 'LT Historical Trading',
}
DEFAULT_TAB = 'public-comps'

CACHE_SUBFOLDER = 'industry_tracker'


def get_cache_dir(industry_key: str) -> str:
    from apo_config.config import config
    from data_access.dbtables import BETA_EST_SUBAPP
    base = config.getconfig(BETA_EST_SUBAPP, 'local_cache_dir') or '.'
    folder = os.path.join(base, CACHE_SUBFOLDER, industry_key)
    os.makedirs(folder, exist_ok=True)
    return folder
