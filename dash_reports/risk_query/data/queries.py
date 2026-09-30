from data_access.utils import get_where_clause, join_where_clauses

RISK_QUERY_SQL = '''
WITH p AS (SELECT cast(asof_date as date) as asof_date, sum(mv_usd) as ptemp {3}																	
{4}
FROM v_pnl_enhanced
WHERE
asof_date BETWEEN '{0}' AND '{1}'
{2}																		
GROUP BY cast(asof_date as date) {3}																		
), r AS (SELECT cast(asof_date as date) as asof_date, sum(mv_usd) as rtemp {3}																	
{5}
FROM v_risk_enhanced
WHERE
asof_date BETWEEN '{0}' AND '{1}'
{2}																		
GROUP BY cast(asof_date as date) {3}																		
)																			
SELECT {8}
FROM p FULL OUTER JOIN r ON p.asof_date=r.asof_date {6}																		
ORDER BY asof_date DESC {7}
'''

SUMMARY_RISK_QUERY_SQL = '''
WITH p AS (SELECT sum(mv_usd) as ptemp 																	
{4}
FROM v_pnl_enhanced
WHERE
asof_date BETWEEN '{0}' AND '{1}'
{2}																		
), r AS (SELECT sum(mv_usd) as rtemp 																	
{5}
FROM v_risk_enhanced
WHERE
asof_date BETWEEN '{0}' AND '{1}'
{2}																		
)																			
SELECT *																		
FROM p FULL OUTER JOIN r ON 1=1																		
'''


def wt_avg_col(f):
    return f'sum({f}*fund_wght)'


def wt_avg_col_pct(f):
    return f'100*sum({f}*fund_wght)'


def agg_col(f, agg_func='sum'):
    return f'{agg_func}({f})'


def round_col(f, round_digits=0):
    return f'round({f}, {round_digits})'


def bps_col(f):
    return f'sum({f})/ avg(nav_bom_usd) * 1e4'


def pct_col(f):
    return f'sum({f})/ avg(nav_bom_usd) * 1e2'


def norm_col_fund_wght(f):
    return f'({f} / (CASE WHEN sum(fund_wght)=0 THEN 1 ELSE sum(fund_wght) END))'


RISKMETRIC_COL_AGG_MAP = {
    'mtd_pnl': ('p', lambda x: agg_col('pl_mtd_total_usd')),
    'ytd_pnl': ('p', lambda x: agg_col('pl_ytd_total_usd')),
    'daily_pnl': ('p', lambda x: agg_col('pl_dtd_total_usd')),
    'daily_ex_fx_pnl': ('p', lambda x: agg_col('pnl_dtd_total_ex_fx__usd')),
    'daily_rate_pnl': ('p', lambda x: agg_col('rate_pnl')),
    'daily_fx_pnl': ('p', lambda x: agg_col('pl_dtd_total_usd') + '-' + agg_col('pnl_dtd_total_ex_fx__usd')),
    'daily_beta_pnl': ('p', lambda x: agg_col('beta_pnl')),
    'daily_alpha_pnl': ('p', lambda x: agg_col('alpha_pnl')),
    'daily_pnl_bps': ('p', lambda x: bps_col('pl_dtd_total_usd')),
    'rate_pnl_bps': ('p', lambda x: bps_col('rate_pnl')),
    'spread_pnl_bps': ('p',
                       lambda x: round_col(bps_col('pnl_dtd_total_ex_fx__usd') + '-' + bps_col('rate_pnl'), 4)),
    'daily_cdx_move_bps': ('p',
                           lambda x: "avg(CASE WHEN reference_index = 'CDX_HY' THEN index_move ELSE NULL END) * 1e4"),
    'daily_spy_move_bps': ('p',
                           lambda
                               x: "avg(CASE WHEN reference_index = 'SPY Equity' THEN index_move ELSE NULL END) * 1e4"),
    'daily_loan_trs_move_bps': ('p',
                                lambda
                                    x: "avg(CASE WHEN reference_index = 'LOAN_TRS' THEN index_move ELSE 0 END) * 1e4"),

    'mv_usd': ('r', agg_col),
    'mv_equivalent_usd': ('r', agg_col),
    'gross_mv_equiv_usd': ('r', agg_col),
    'mv_5y_equivalent_usd': ('r', lambda x: agg_col('`5y_mv_equivalent_usd`')),
    'gross_mv_5y_equiv_usd': ('r', lambda x: agg_col(agg_col('`5y_mv_equivalent_usd`',
                                                             agg_func='abs'),
                                                     agg_func='sum')),

    'nav_daily_usd': ('r', lambda x: agg_col('nav_daily_usd', agg_func='avg')),
    'fund_wght': ('r', lambda x: round_col(agg_col(f'{x}*100'), 4)),
    'quantity': ('r', agg_col),
    'price_local': ('r', wt_avg_col),
    'cost_local': ('r', wt_avg_col),

    'beta_contribution': (
        'r', lambda x: round_col(agg_col(f'(coalesce(beta_contribution,0)+coalesce(ir_beta_contrib,0))*100'), 4)),
    'ir_beta_contribution': (
        'r', lambda x: round_col(agg_col(f'coalesce(ir_beta_contrib,0)*100'), 4)),
    'beta': ('r', wt_avg_col),
    'beta_contrib_mult': ('r', wt_avg_col),

    'ir_delta': ('r', lambda x: agg_col('ir_delta_usd')),
    'equity_delta': ('r', lambda x: agg_col('equity_delta_usd')),
    'credit_delta': ('r', lambda x: agg_col('credit_delta_final')),
    'relative_credit_delta': ('r', lambda x: agg_col('relative_credit_delta_usd')),
    'fx_delta_usd': ('r', lambda x: agg_col('fx_delta_usd')),

    'modified_duration': ('r', wt_avg_col),
    'spread_duration': ('r', wt_avg_col),
    'rate_duration': ('r', lambda x: wt_avg_col('rate_duration')),
    'yield_to_worst': ('r', wt_avg_col_pct),
    'yield_to_maturity': ('r', wt_avg_col_pct),
    'credit_spread': ('r', wt_avg_col_pct),
    'credit_spd_bps': ('r', wt_avg_col),
    'daily_theta_usd': ('r', lambda x: agg_col('theta_usd')),
    'daily_carry_usd': ('r', lambda x: agg_col('carry_usd')),
    'mean_days_to_liquidate': ('r', wt_avg_col),
    'liquidity_score': ('r', lambda x: wt_avg_col('final_liquidity_score')),

    'pnl_2008_lehman_default_usd': ('r', agg_col),
    'pnl_2011_euro_sovereign_crisis_usd': ('r', agg_col),
    'pnl_recession_usd': ('r', agg_col),
    'pnl_rally_usd': ('r', agg_col),
    'pnl_sell_off_usd': ('r', agg_col),
    'pnl_bull_market_usd': ('r', agg_col),
    'pnl_bear_market_usd': ('r', agg_col),
    'pnl_correction_usd': ('r', agg_col),
    'pnl_fed_adverse_usd': ('r', agg_col),
    'pnl_fed_severely_adverse_usd': ('r', agg_col),
    'pnl_custom_scenario_total_usd': ('r', agg_col),

    'pnl_2008_lehman_default_pct': ('r', lambda x: pct_col('pnl_2008_lehman_default_usd')),
    'pnl_2011_euro_sovereign_crisis_pct': ('r', lambda x: pct_col('pnl_2011_euro_sovereign_crisis_usd')),
    'pnl_recession_pct': ('r', lambda x: pct_col('pnl_recession_usd')),
    'pnl_rally_pct': ('r', lambda x: pct_col('pnl_rally_usd')),
    'pnl_sell_off_pct': ('r', lambda x: pct_col('pnl_sell_off_usd')),
    'pnl_bull_market_pct': ('r', lambda x: pct_col('pnl_bull_market_usd')),
    'pnl_bear_market_pct': ('r', lambda x: pct_col('pnl_bear_market_usd')),
    'pnl_correction_pct': ('r', lambda x: pct_col('pnl_correction_usd')),
    'pnl_fed_adverse_pct': ('r', lambda x: pct_col('pnl_fed_adverse_usd')),
    'pnl_fed_severely_adverse_pct': ('r', lambda x: pct_col('pnl_fed_severely_adverse_usd')),
    'pnl_custom_scenario_total_pct': ('r', lambda x: pct_col('pnl_custom_scenario_total_usd')),
}


def _get_where_clause(filters):
    where_clauses = []
    for k, v in filters.items():
        where_clauses.append(get_where_clause(k, v, use_databricks=True))
    where_clause = join_where_clauses(' AND ', where_clauses)
    if len(where_clause) > 0:
        where_clause = ' AND ' + where_clause
    return where_clause


def _get_col_str(cols, keep_contribution_column):
    pnl_col_str = []
    risk_col_str = []
    for c in cols:
        c = c.replace('cum_', '')
        f = RISKMETRIC_COL_AGG_MAP[c][1](c)
        if (not keep_contribution_column) and ('*fund_wght' in f):
            f = norm_col_fund_wght(f)
        c_query = f + ' AS ' + c
        if RISKMETRIC_COL_AGG_MAP[c][0] == 'p':
            if c_query not in pnl_col_str:
                pnl_col_str.append(c_query)
        else:
            if c_query not in risk_col_str:
                risk_col_str.append(c_query)
    return pnl_col_str, risk_col_str


def _get_sel_clause(gby):
    if len(gby) == 0: return ''
    return "," + ",".join(gby)


def _get_alias(col_str):
    return col_str.rsplit(' AS ', 1)[1]


def _get_result_sel_clause(gby, pnl_col_str, risk_col_str):
    join_cols = ['coalesce(p.asof_date, r.asof_date) AS asof_date']
    join_cols += [f'coalesce(p.{c}, r.{c}) AS {c}' for c in gby]
    pnl_cols = [f'p.{_get_alias(c)} AS {_get_alias(c)}' for c in pnl_col_str]
    risk_cols = [f'r.{_get_alias(c)} AS {_get_alias(c)}' for c in risk_col_str]
    return ',\n'.join(join_cols + pnl_cols + risk_cols)


def _get_join_on(gby):
    if len(gby) == 0: return ''
    return ' AND ' + ' AND '.join([f"coalesce(p.{x}, '0') = coalesce(r.{x}, '0')" for x in gby])


def _get_sort_clause(gby):
    if len(gby) == 0: return ''
    return "," + ",".join(gby)
