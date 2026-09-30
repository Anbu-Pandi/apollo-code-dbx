STRESS_SCENARIO_RENAME = {'pnl_bull_market_usd': 'Bull 10.0%', 'pnl_correction_usd': 'Correction (10.0%)',
                          'pnl_recession_usd': 'Recession (35.0%)',
                          'pnl_fed_severely_adverse_usd': 'Fed Severe (50%)'}

RISK_OVERVIEW = {
    'grp_by': ['fund_entity_code', 'fo_strategy_broad_category'],

    'exposure': {
        'rqrd_cols': ['fund_entity_code', 'fo_strategy_broad_category', 'strategy_desc', 'issuer_ult', 'is_liability',
                      'mv_equivalent_usd', 'risk_direction', 'fund_wght'],
        'rules': {'issuer_ult': ['United States Treasury Note Bond', 'United States Treasury Bill'],
                  'strategy_desc': ['Hedge - Investor'], 'is_liability': ['N']}},

    'unlev_ytw': {
        'rqrd_cols': ['fund_entity_code', 'fo_strategy_broad_category', 'strategy_desc', 'fund_wght',
                      'yield_to_worst'],
        'rules': {'strategy_desc': ['Hedge - Investor']}},

    'theta_usd': {
        'rqrd_cols': ['theta_usd', 'nav_bom_usd', 'fund_entity_code', 'fo_strategy_broad_category',
                      'strategy_desc'],
        'rules': {'strategy_desc': ['Hedge - Investor']}},

    'carry_usd': {
        'rqrd_cols': ['nav_bom_usd', 'carry_usd', 'fund_entity_code', 'fo_strategy_broad_category',
                      'strategy_desc'],
        'rules': {'strategy_desc': ['Hedge - Investor']}},

    'sprd_dur': {
        'rqrd_cols': ['fund_entity_code', 'fo_strategy_broad_category', 'strategy_desc', 'fund_wght',
                      'spread_duration'],
        'rules': {'strategy_desc': ['Hedge - Investor']}},

    'mod_dur': {
        'rqrd_cols': ['fund_entity_code', 'fo_strategy_broad_category', 'strategy_desc', 'fund_wght',
                      'modified_duration'],
        'rules': {'strategy_desc': ['Hedge - Investor']}},

    # 'factor_beta': {'rqrd_cols': ['Reference Index', 'Beta Contribution', 'IR Beta Contrib',
    #                               'fo_strategy_broad_category', 'strategy_desc', 'is_liability',
    #                               'fund_entity_code'],
    #                 'rules': {'strategy_desc': ['Hedge - Investor'], 'is_liability': ['N']}},

    'stress_scenario': {
        'rqrd_cols': ['pnl_bull_market_usd', 'pnl_correction_usd', 'pnl_recession_usd',
                      'pnl_fed_severely_adverse_usd', 'fund_entity_code', 'nav_bom_usd',
                      'fo_strategy_broad_category', 'strategy_desc', 'is_liability'],
        'rules': {'strategy_desc': ['Hedge - Investor'], 'is_liability': ['N']}},

    # 'pnl_roll_strat': {
    #     'rqrd_cols': ['fund_entity_code', 'fo_strategy_broad_category', '1 Week Rolling PnL', 'MTD PnL',
    #                   'QTD PnL', 'YTD PnL']}
}