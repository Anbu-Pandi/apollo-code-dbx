PREFIX = 'ccb'

INDEX_KEYS = ['us_hy', 'us_ig', 'eu_hy', 'eu_ig', 'eu_finsen', 'eu_finsub']

INDEX_META = {
    'us_hy': {'label': 'US HY', 'tickers': ['CDX-NAHY'], 'short': 'HY', 'region': 'US'},
    'us_ig': {'label': 'US IG', 'tickers': ['CDX-NAIG'], 'short': 'IG', 'region': 'US'},
    'eu_hy': {'label': 'EU HY (Xover)', 'tickers': ['ITRAXX-Xover'], 'short': 'XO', 'region': 'EU'},
    'eu_ig': {'label': 'EU IG (Main)', 'tickers': ['ITRAXX-Europe'], 'short': 'MN', 'region': 'EU'},
    'eu_finsen': {'label': 'EU Fin Sen', 'tickers': ['ITRAXX-FinSen'], 'short': 'FN', 'region': 'EU'},
    'eu_finsub': {'label': 'EU Fin Sub', 'tickers': ['ITRAXX-FinSub'], 'short': 'FS', 'region': 'EU'},
}

ALL_TICKERS = sorted({t for m in INDEX_META.values() for t in m['tickers']})
TENOR = '5Y'
DEFAULT_LOOKBACK = '2y'
