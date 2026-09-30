DEFAULT_TICKERS = ['CDX-NAHY', 'CDX-NAIG', 'ITRAXX-Europe', 'ITRAXX-Xover']

TAB_INDEX = {
    'vol-grids': 0,
    'trade-blotter': 1,
    'positions': 2,
    'expiry-charts': 3,
    'option-stack': 4,
    'relative-vol': 5,
}

VOL_GRID_KEYS = [
    'spot_ref', 'payer_prem', 'receiver_prem', 'vol_diff_1b', 'vol_diff_nb',
    'vols', 'bps_d', 'spread_vols', 'deltas', 'vegas',
]

VOL_GRID_LABELS = {
    'spot_ref': 'Spot Reference', 'payer_prem': 'Payer Premium',
    'receiver_prem': 'Receiver Premium', 'vol_diff_1b': 'Vol Change (1-day)',
    'vol_diff_nb': 'Vol Change (N-day)', 'vols': 'Current Vols',
    'bps_d': 'Bps / Day', 'spread_vols': 'Spread Vol %',
    'deltas': 'Delta', 'vegas': 'Vega',
}

BLOTTER_KEYS = ['intraday_trades_df', 'priorday_trades_df', 'all_trades_wtd_df']

BLOTTER_LABELS = {
    'intraday_trades_df': 'Intraday Trades',
    'priorday_trades_df': 'Prior Day Trades',
    'all_trades_wtd_df': 'WTD Trades (10d)',
}

POSITION_KEYS = [
    ('greeks_by_spread', 'Greeks by Spot Spread Scenario'),
    ('greeks_by_price', 'Greeks by Spot Price Scenario'),
    ('notional_by_strike', 'Notional by Strike'),
]

TENOR_OPTIONS = ['1m', '2m', '3m', '6m', '9m', '12m']
DELTA_OPTIONS = [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50]

MAX_OPTION_STACKS = 8
MAX_EXPIRY_CHART_SETS = 8
