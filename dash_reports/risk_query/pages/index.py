from common.utils import dtutil
from dash import html, dcc

from dash_reports.report_common.pages.link_and_headers import get_page_title
from dash_reports.report_common.utils.components import create_link
from dash_reports.risk_query.pages import query_page


def get_headers(app=None):
    if app is not None:
        app.title = 'Risk Query'
    layout = html.Div([get_page_title("Risk Query"),
                       html.Br(),
                       ])
    return layout


def get_risk_query_menu(page):
    menu = html.Div([
        create_link('risk-query', 'fund-risk', 'Fund', selected_page=page),
        create_link('risk-query', 'issuer-risk', 'Issuer', selected_page=page),
        create_link('risk-query', 'fund-ytd-pnl', 'Fund YTD PnL', selected_page=page),
        create_link('risk-query', 'issuer-ytd-pnl', 'Issuer YTD PnL', selected_page=page),
        create_link('risk-query', 'sector-risk', 'Sector', selected_page=page),
        create_link('risk-query', 'rating-risk', 'Rating', selected_page=page),
        create_link('risk-query', 'liquidity', 'Liquidity', selected_page=page),
        create_link('risk-query', 'query', 'Query', selected_page=page),
    ], className="row all-tabs", )
    return menu


def create_risk_query_layout(app, querypage, user_settings, groups=None):
    app.title = 'Risk Query'
    dt_1b = dtutil.dateadd(dtutil.today(), '-1b')
    dt_1w = dtutil.dateadd(dtutil.today(), '-1w-1b')
    dt_3m = dtutil.dateadd(dtutil.today(), '-3m-1b')
    dt_ytd = dtutil.boyear(dtutil.today())
    static_div_kids = [get_risk_query_menu(querypage)]

    dt1 = None
    dt2 = None
    rm = None
    gby = None
    filtercolumns = None
    filtervalues = None

    risk_cols = ['beta_contribution', 'mv_5y_equivalent_usd', 'mv_equivalent_usd', 'mv_usd',
                 'yield_to_worst', 'credit_spread',
                 'ir_delta', 'credit_delta', 'equity_delta',
                 'rate_duration', 'spread_duration', 'modified_duration',
                 'daily_theta_usd', 'daily_carry_usd', 'mean_days_to_liquidate', 'cum_daily_pnl', 'beta_contrib_mult']
    pnl_cols = ['cum_daily_pnl', 'cum_daily_ex_fx_pnl', 'cum_daily_rate_pnl', 'cum_daily_fx_pnl', 'cum_daily_beta_pnl',
                'cum_daily_alpha_pnl']
    if querypage == 'fund-risk':
        rm = risk_cols
        gby = ['fund_entity_code']
        filtercolumns = ['fund_entity_code']
        filtervalues = ['CSTRAT']
        dt1 = dt_1w
        dt2 = dt_1b
    elif querypage == 'issuer-risk':
        rm = risk_cols
        gby = ['issuer_ult']
        filtercolumns = ['fund_entity_code']
        filtervalues = ['CSTRAT']
        dt1 = dt_1w
        dt2 = dt_1b
    elif querypage == 'fund-ytd-pnl':
        rm = pnl_cols
        gby = ['fund_entity_code']
        filtercolumns = ['fund_entity_code', 'security']
        filtervalues = ['CSTRAT', '!=FX Offset']
        dt1 = dt_ytd
        dt2 = dt_1b
    elif querypage == 'issuer-ytd-pnl':
        rm = pnl_cols
        gby = ['issuer_ult']
        filtercolumns = ['fund_entity_code', 'security']
        filtervalues = ['CSTRAT', '!=FX Offset']
        dt1 = dt_ytd
        dt2 = dt_1b
    elif querypage == 'sector-risk':
        rm = risk_cols
        gby = ['issuer_ult_gics_sub_industry']
        filtercolumns = ['fund_entity_code']
        filtervalues = ['CSTRAT']
        dt1 = dt_3m
        dt2 = dt_1b
    elif querypage == 'rating-risk':
        rm = risk_cols
        gby = ['final_rating']
        filtercolumns = ['fund_entity_code']
        filtervalues = ['CSTRAT']
        dt1 = dt_3m
        dt2 = dt_1b
    elif querypage == 'liquidity':
        rm = ['liquidity_score', 'mean_days_to_liquidate']
        gby = ['fund_entity_code']
        filtercolumns = ['fund_entity_code']
        filtervalues = ['CSTRAT']
        dt1 = dt_ytd
        dt2 = dt_1b
    elif querypage == 'query':
        dt1 = dt_3m
        dt2 = dt_1b
    static_div_kids.append(query_page.create_static_layout(dt1, dt2, rm, gby, filtercolumns, filtervalues, user_settings=user_settings, groups=groups))
    static_div_kids.append(dcc.Store(query_page.CHG_STORE_ID, data='new request'))
    return static_div_kids, query_page.create_dynamic_layout()
