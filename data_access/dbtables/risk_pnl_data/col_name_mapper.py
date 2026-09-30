import re

from common.utils import dfutil

# Source-specific overrides passed to to_db_col_name() for each data source.
# Used by callers that build col maps on the fly from df.columns.
_SOURCE_OVERRIDES = {
    'pnl': {'As Of Date': 'asof_date'},
    'risk': {'As Of Date': 'risk_date', 'Option Delta Percent': 'option_delta_percent'},
}

# ── Pre-processing: token substitutions applied BEFORE to_naming_convention_string() ──
# Order matters — longer/more specific tokens first
_PRE_SUBSTITUTIONS = [
    ('P&L', 'pnl'),  # & stripped → 'pl'; need 'pnl'
    ('S&P', 'snp'),  # & stripped → 'sp'; need 'snp'
    ('PnL', 'pnl'),  # camelCase splits → 'pn_l'; need 'pnl'
    ('LoanX', 'loanx'),  # camelCase splits → 'loan_x'; need 'loanx'
    ('Covid-19', 'covid19'),  # hyphen-number → 'covid_19'; need 'covid19'
    # NOTE: ' - ' is intentionally NOT substituted here.
    # When used as a name separator (e.g. 'Analyst xxx - Current',
    # 'Yield To Worst - Func') the function naturally strips it to '_'.
    # The one case needing 'minus' ('YTW - Prior BD YTW') is in _OVERRIDES.
    ('%', ' pct '),
]

# Case-insensitive whole-word substitutions applied BEFORE to_naming_convention_string()
_WORD_SUBSTITUTIONS = [
    (r'\bPercent\b', 'pct', re.IGNORECASE),  # 'Widen 10 Percent' → '10 pct'
    (r'\bx\b', 'ex', 0),  # standalone 'x' (excluding) → 'ex'
]

# Explicit file_col_name → table_col_name overrides for columns that cannot be
# derived programmatically. Checked FIRST before any transformation.
_OVERRIDES = {
    # ── $ prefix (already snake_case; $ → 'usd_' doesn't route cleanly through function)
    '$dtd_fin_cost': 'usd_dtd_fin_cost',
    '$dtd_contr': 'usd_dtd_contr',
    '$dtd_mtm': 'usd_dtd_mtm',
    '$dtd_carry': 'usd_dtd_carry',
    '$dtd_contr_exFX': 'usd_dtd_contr_ex_fx',

    # ── Named-scenario P&L USD columns: table drops '_total' before '_usd'
    # (time-period ones like Weekly/MTD/DTD keep '_total'; named scenarios don't)
    'P&L Recession Total USD': 'pnl_recession_usd',
    'P&L 2008 Lehman Default Total USD': 'pnl_2008_lehman_default_usd',
    'P&L Sell Off Total USD': 'pnl_sell_off_usd',
    'P&L Bull Market Total USD': 'pnl_bull_market_usd',
    'P&L Fed Severely Adverse Total USD': 'pnl_fed_severely_adverse_usd',
    'P&L 1991 Recession Total USD': 'pnl_1991_recession_usd',
    'P&L 2007 Bear Stearns Default Total USD': 'pnl_2007_bear_stearns_default_usd',
    'P&L Correction Total USD': 'pnl_correction_usd',
    'P&L 2011 Euro Sovereign Crisis Total USD': 'pnl_2011_euro_sovereign_crisis_usd',
    'P&L Bear Market Total USD': 'pnl_bear_market_usd',
    'P&L Rally Total USD': 'pnl_rally_usd',
    'P&L 1994 Rising Rates Total USD': 'pnl_1994_rising_rates_usd',
    'P&L Fed Adverse Total USD': 'pnl_fed_adverse_usd',

    # ── source=pnl columns that keep 'pl_' prefix (not 'pnl_')
    'P&L Weekly Total Func': 'pl_weekly_total_func',
    'P&L MTD Total USD': 'pl_mtd_total_usd',
    'P&L MTD Total Func': 'pl_mtd_total_func',
    'P&L YTD Total USD': 'pl_ytd_total_usd',
    'P&L YTD Total Func': 'pl_ytd_total_func',
    'P&L DTD Total USD': 'pl_dtd_total_usd',
    'P&L DTD Total Func': 'pl_dtd_total_func',
    'P&L DTD Other USD': 'pl_dtd_other_usd',
    'P&L DTD Other Local': 'pl_dtd_other_local',
    'P&L DTD Fees USD': 'pl_dtd_fees_usd',

    # ── % dropped entirely for these columns (not → _pct)
    'Spread %': 'spread',
    'DTD ROR %': 'dtd_ror',
    'MTD ROR %': 'mtd_ror',
    'QTD ROR %': 'qtd_ror',
    'YTD ROR %': 'ytd_ror',

    # ── 'Percent' kept as-is in table name for this column
    'ABF IG Percent': 'abf_ig_percent',

    # ── The one dash that means 'minus' (all other ' - ' are just separators)
    'YTW - Prior BD YTW': 'ytw_minus_prior_bd_ytw',

    # ── Compound diff expression where the two operands differ
    '(MV Equivalent USD) - (Prior Business Day (Sum(_Market Value Equivalent Usd_)))':
        'mv_equivalent_usd_minus_prev_bus_day_sum',

    # ── Table schema artefacts (double-underscore / trailing underscore in table)
    'P&L DTD Total x FX USD': 'pnl_dtd_total_ex_fx__usd',
    'Percent to Total (MV Equivalent USD)': 'pct_to_total_mv_equivalent_usd_',

    # ── Source-dependent columns — pass the correct mapping via overrides= at call-site:
    #
    #   risk source:
    #       {'As Of Date': 'risk_date', 'Option Delta Percent': 'option_delta_percent'}
    #   pnl source:
    #       {'As Of Date': 'asof_date'}
    #       ('Option Delta Percent' → 'option_delta_pct' is handled by the Percent→pct rule)
}


def _pre_process(s: str) -> str:
    for old, new in _PRE_SUBSTITUTIONS:
        s = s.replace(old, new)
    for pattern, repl, flags in _WORD_SUBSTITUTIONS:
        s = re.sub(pattern, repl, s, flags=flags)
    return s


def _post_process(s: str) -> str:
    # Collapse __ caused by already-underscored inputs like 'covid_sector_flag',
    # 'NAV_wght', 'Pdv01_10y', 'FO_STRATEGY_ID', 'Price_Chng_MTD', etc.
    s = re.sub(r'_{2,}', '_', s)
    # Combine number + tenor unit: '10_y' → '10y', '3_m' → '3m'
    # (caused by camelCase-split of '10Y', '3M', '6M' etc.)
    s = re.sub(r'(\d)_([ymd])(?=_|$)', r'\1\2', s)
    return s.strip('_')


# Columns added programmatically (already snake_case) that must NOT be renamed
# by to_db_col_name in worker functions, and must be dropped before from_db_col_name
# is applied in Phase 2 (they are read as scalars before the rename step).
_INTERNAL_COLS = {'as_of_date', 'is_shadow_security', 'covid_sector_flag'}

# Tokens that were uppercased in source column headers and must be restored on reversal.
_UPPER_WORDS = {
    'cusip', 'isin', 'nav', 'usd', 'fx', 'ir', 'vpm', 'gcc',
    'itd', 'mtd', 'ytd', 'dtd', 'qtd', 'bom', 'scr', 'mv',
    'gics', 'eig', 'spac', 'abf', 'fo', 'hf', 'ror', 'id',
}

# Reverse of _PRE_SUBSTITUTIONS for non-override columns.
# P&L source columns are all explicit in _OVERRIDES so any non-override 'pnl' token
# came from a computed column originally named with 'PnL'.
_PRE_SUBST_INV = {
    'pnl': 'PnL',
    'snp': 'S&P',
    'loanx': 'LoanX',
    'covid19': 'Covid-19',
}


def from_db_col_name(db_col_name: str, overrides: dict = None) -> str:
    """Reverse of to_db_col_name: convert a DB table column name back to its
    original source-file or computed column header.

    Handles:
      - Explicit _OVERRIDES inverse (covers all P&L, S&P, $ columns).
      - Source-specific overrides inverse (e.g. 'asof_date' → 'As Of Date').
      - Known acronym tokens restored to uppercase (CUSIP, ISIN, NAV, USD …).
      - Known pre-substitution tokens restored (pnl→PnL, snp→S&P, loanx→LoanX).
      - Internal '_*' columns returned unchanged.
    """
    if db_col_name.startswith('_'):
        return db_col_name

    effective_overrides = {**_OVERRIDES, **(overrides or {})}
    overrides_inv = {v: k for k, v in effective_overrides.items()}
    if db_col_name in overrides_inv:
        return overrides_inv[db_col_name]

    words = []
    for token in db_col_name.split('_'):
        if token in _UPPER_WORDS:
            words.append(token.upper())
        elif token in _PRE_SUBST_INV:
            words.append(_PRE_SUBST_INV[token])
        else:
            words.append(token.capitalize())
    return ' '.join(words)


def to_db_col_name(file_col_name: str, overrides: dict = None) -> str:
    """Convert a source-file column header to its database table column name.

    Wraps dfutil.to_naming_convention_string() with pre/post processing for
    systematic token patterns and an explicit override dict for edge cases,
    producing a result that matches table_col_name in the column mapping sheet.

    Pre-processing handles: P&L→pnl, S&P→snp, PnL→pnl, LoanX→loanx,
    Covid-19→covid19, %→pct, Percent→pct, standalone x→ex (for 'x FX').

    Post-processing handles: double-underscore collapse (already-snake_case
    inputs), number+tenor-unit merge (10_y→10y).

    Compound diff-expressions are resolved via regex:
        '(X) - (Prior Business Day (X))'                  → 'x_minus_prev_bus_day'
        '(X) - (Last Business Day of Prior Month (X))'    → 'x_minus_prev_month_end'
        'Prior Business Day (X)'                          → 'prev_bus_day_x'
        'Last Business Day of Prior Month (X)'            → 'prev_month_end_x'

    Args:
        file_col_name: Original column header from the source file.
        overrides:     Optional {file_col_name: table_col_name} merged on top
                       of the base overrides. Required for source-specific cases:
                           risk source: {'As Of Date': 'risk_date',
                                         'Option Delta Percent': 'option_delta_percent'}
                           pnl  source: {'As Of Date': 'asof_date'}

    Returns:
        The matching database table column name.
    """
    effective_overrides = {**_OVERRIDES, **(overrides or {})}

    # Explicit override wins — checked before any transformation
    if file_col_name in effective_overrides:
        return effective_overrides[file_col_name]

    # Compound diff: (X) - (Prior Business Day (X))
    m = re.match(r'^\((.+?)\) - \(Prior Business Day \(.+?\)\)$', file_col_name)
    if m:
        return to_db_col_name(m.group(1), overrides) + '_minus_prev_bus_day'

    # Compound diff: (X) - (Last Business Day of Prior Month (X))
    m = re.match(r'^\((.+?)\) - \(Last Business Day of Prior Month \(.+?\)\)$', file_col_name)
    if m:
        return to_db_col_name(m.group(1), overrides) + '_minus_prev_month_end'

    # Prefix: Prior Business Day (X)
    m = re.match(r'^Prior Business Day \((.+?)\)$', file_col_name)
    if m:
        return 'prev_bus_day_' + to_db_col_name(m.group(1), overrides)

    # Prefix: Last Business Day of Prior Month (X)
    m = re.match(r'^Last Business Day of Prior Month \((.+?)\)$', file_col_name)
    if m:
        return 'prev_month_end_' + to_db_col_name(m.group(1), overrides)

    # Standard path: pre-process → naming convention → post-process
    s = _pre_process(file_col_name)
    s = dfutil.to_naming_convention_string(s)
    s = _post_process(s)
    return s
