import numpy as np

INPUT_PATH_FOLDER = r'\\COMMONSHARE\Common$\Hedge Fund\MicroStrategy Exports\PnL Attribution Daily'

"""ouput analysis location; flag should be set to true """
SAVE_OUTPUT = False
OUTPUT_PATH = r'H:\MiniProjects\DashReports\Testing\Oct192020'

"""Add new funds here to include in the dashboard"""
FUND_CODE_LIST = ['ACCORD4', 'AIF', 'ANCF', 'ANOVER2', 'ATCS', 'ATLAS', 'CADBMA', 'CENTRE', 'CF',
                  'CSTRAT', 'FRANK2LS', 'IVYMAI', 'IVYSTRAT', 'K2LIQUID', 'LINCFI', 'MOULTRIE', 'MPISMA',
                  'SPNTACT', 'TRF', 'TRFEUROPE', 'KENT', 'OASISFND', 'PPFCSTR', 'MERCERMA', 'ACE', 'AOP',
                  'ADSBDC', 'SPAC1']

FLAGSHIP_FUNDS = ['CSTRAT', 'ANCF', 'ATLAS', 'CENTRE', 'CF', 'MOULTRIE', 'SPNTACT', 'TRF']

FUNDS_FOR_SANKEY = ['CSTRAT', 'TRF', 'CF', 'ANCF']

BETA_BY_DIFF_FUNDS = ['CSTRAT', 'ANCF', 'TRF', 'CF']

FUND_DISPLAY_ORDER = ['CSTRAT', 'ANCF', 'PPFCSTR', 'TRENHANCE', 'CF', 'PPFOCP', 'ANOVER2', 'ACE',
                      'ACCORD4', 'AOP', 'ADSBDC', 'SPAC1', 'OASISFND', 'ATLAS', 'FRANK2LS', 'K2LIQUID',
                      'K2ALTUCT', 'K2ALTSTR', 'Chiron', 'ATCS', 'TRF',
                      'TRFEUROPE', 'BEACON', 'KENT', 'LINCFI', 'MPISMA', 'CADBMA', 'SLBTRFMA', 'MERCERMA', 'AIF',
                      'IVYMAI', 'IVYSTRAT', 'STLT10', 'STLT11', 'MOULTRIE', 'CENTRE', 'SPNTACT']

"""other mappings used in generating analysis"""
BETA_CHART_COLOR = {'HY': '#A6C773', 'LL': '#1BAFDC', 'SPX': '#FDAE2A', 'IR': '#888888', 'Total Beta to HY': '#111111',
                    'Current Beta': '#888888'}

REFERENCE_INDEX = {'CDX_HY': 'HY', 'LOAN_TRS': 'LL', 'SPY Equity': 'EQ'}

BENCHMARK_MAP = {'SPY US EQUITY': 'SPY', 'iboxx_usloan_trs': 'LL TRS', 'tsy_10y': '10Y TRSY'}

STRESS_SCENARIO_RENAME = {'P&L Bull Market Total USD': 'Bull 10.0%', 'P&L Correction Total USD': 'Correction (10.0%)',
                          'P&L Recession Total USD': 'Recession (35.0%)',
                          'P&L Fed Severely Adverse Total USD': 'Fed Severe (50%)'}

SECTOR_MAP = {'Energy': 'Energy', 'Commodities': 'Commod', 'Industrials': 'Ind', 'Materials': 'Materials',
              'Financials': 'Fins', 'Consumer Discretionary': 'Cons Disc', 'Communication Services': 'Comm Serv',
              'Information Technology': 'IT', 'Real Estate': 'RE', 'Consumer Staples': 'Cons Staple',
              'Health Care': 'HC', 'Utilities': 'Utils', 'Index': 'Index', 'Sovereign': 'Sov',
              'Special Purpose Entity': 'SPE', 'Financing': 'Financing', 'Closed End Fund': 'C/E Fund',
              'Asset Backed Securities': 'ABS', 'FX': 'FX', 'None': 'None'}

SECTOR_CYCLE_MAP = {'': ['Total'], 'Cyclical': ['Energy', 'Commod', 'Ind', 'Materials', 'Fins'],
                    'Growth': ['Cons Disc', 'Comm Serv', 'IT', 'RE'], 'Defensive': ['Cons Staple', 'HC', 'Utils'],
                    'Other': ['Index', 'Sov', 'SPE', 'Financing', 'C/E Fund', 'ABS', 'FX', 'None']}

RATINGS_MAP = {'Aaa': 'AAA', 'Cash': 'AAA', 'Aa1': 'AA', 'Aa2': 'AA', 'Aa3': 'AA', 'A1': 'A', 'A2': 'A', 'A3': 'A',
               'Baa1': 'BBB', 'Baa2': 'BBB', 'Baa3': 'BBB', 'Ba1': 'BB', 'Ba2': 'BB', 'Ba3': 'BB', 'B1': 'B', 'B2': 'B',
               'B3': 'B', 'Caa1': 'CCC', 'Caa2': 'CCC', 'Caa3': 'CCC', 'Ca': 'CC', 'C': 'C', 'D': 'D'}

IG_HY_MAP = {'': ['Total'], 'IG': ['AAA', 'AA', 'A', 'BBB'], 'HY': ['BB', 'B', 'CCC', 'CC', 'C', 'D', 'OTH']}

"""Analysis Rules"""
PNL_ATTRIB_RULES = {'beta_pnl_cols': ['Beta PnL', 'Beta Return', 'FO Strategy Broad Category',
                                      'Fund Entity Code', 'Reference Index'],

                    'alpha_pnl_cols': ['Alpha PnL', 'Alpha Return', 'FO Strategy Broad Category',
                                       'Fund Entity Code', 'Rate PnL', 'Rate Return'],

                    'total_pnl_cols': ['FO Strategy Broad Category', 'P&L DTD Total x FX USD',
                                       'Fund Entity Code', 'Total Return']}

RISK_OVERVIEW = {
    'grp_by': ['Fund Entity Code', 'FO Strategy Broad Category'],

    'exposure': {
        'rqrd_cols': ['Fund Entity Code', 'FO Strategy Broad Category', 'Strategy Desc', 'Issuer Ult', 'Is Liability',
                      'MV Equivalent USD', 'Risk Direction', 'Fund Wght'],
        'rules': {'Issuer Ult': ['United States Treasury Note Bond', 'United States Treasury Bill'],
                  'Strategy Desc': ['Hedge - Investor'], 'Is Liability': ['N']}},

    'unlev_ytw': {
        'rqrd_cols': ['Fund Entity Code', 'FO Strategy Broad Category', 'Strategy Desc', 'Fund Wght',
                      'Yield to Worst'],
        'rules': {'Strategy Desc': ['Hedge - Investor']}},

    'theta_usd': {
        'rqrd_cols': ['Theta USD', 'NAV BOM USD', 'Fund Entity Code', 'FO Strategy Broad Category',
                      'Strategy Desc'],
        'rules': {'Strategy Desc': ['Hedge - Investor']}},

    'carry_usd': {
        'rqrd_cols': ['NAV BOM USD', 'Carry USD', 'Fund Entity Code', 'FO Strategy Broad Category',
                      'Strategy Desc'],
        'rules': {'Strategy Desc': ['Hedge - Investor']}},

    'sprd_dur': {
        'rqrd_cols': ['Fund Entity Code', 'FO Strategy Broad Category', 'Strategy Desc', 'Fund Wght',
                      'Spread Duration'],
        'rules': {'Strategy Desc': ['Hedge - Investor']}},

    'mod_dur': {
        'rqrd_cols': ['Fund Entity Code', 'FO Strategy Broad Category', 'Strategy Desc', 'Fund Wght',
                      'Modified Duration'],
        'rules': {'Strategy Desc': ['Hedge - Investor']}},

    'factor_beta': {'rqrd_cols': ['Reference Index', 'Beta Contribution', 'IR Beta Contrib',
                                  'FO Strategy Broad Category', 'Strategy Desc', 'Is Liability',
                                  'Fund Entity Code'],
                    'rules': {'Strategy Desc': ['Hedge - Investor'], 'Is Liability': ['N']}},

    'stress_scenario': {
        'rqrd_cols': ['P&L Bull Market Total USD', 'P&L Correction Total USD', 'P&L Recession Total USD',
                      'P&L Fed Severely Adverse Total USD', 'Fund Entity Code', 'NAV BOM USD',
                      'FO Strategy Broad Category', 'Strategy Desc', 'Is Liability'],
        'rules': {'Strategy Desc': ['Hedge - Investor'], 'Is Liability': ['N']}},

    'pnl_roll_strat': {
        'rqrd_cols': ['Fund Entity Code', 'FO Strategy Broad Category', '1 Week Rolling PnL', 'MTD PnL',
                      'QTD PnL', 'YTD PnL']}}

SECTOR_ATTRIB_RULES = {'risk_enhanced_file': {
    'cols': ['Fund Entity Code', 'Fund Wght', 'Gross MV Equiv USD',
             'Issuer Ult GICS Industry Sector', 'MV Equivalent USD'],

    'rules': {'Issuer Ult': ['United States Treasury Note Bond', 'United States Treasury Bill'],
              'Strategy Desc': ['Hedge - Investor'], 'Is Liability': ['N']}},

    'rolling_data': {
        'cols': ['Fund Entity Code', 'Issuer Ult GICS Industry Sector', '1 Week Rolling PnL', 'MTD PnL', 'QTD PnL',
                 'YTD PnL']},

    'unlev_ytw_cols': ['Fund Entity Code', 'Issuer Ult GICS Industry Sector', 'Fund Wght', 'Yield to Worst'],

    'group_by': ['Fund Entity Code', 'Issuer Ult GICS Industry Sector']}

RATING_ATTRIB_RULES = {'risk_enhanced_file': {
    'cols': ['Fund Entity Code', 'Fund Wght', 'Gross MV Equiv USD',
             'Rating', 'MV Equivalent USD'],

    'rules': {'Issuer Ult': ['United States Treasury Note Bond', 'United States Treasury Bill'],
              'Strategy Desc': ['Hedge - Investor'], 'Is Liability': ['N']}},

    'rolling_data': {
        'cols': ['Fund Entity Code', 'Rating', '1 Week Rolling PnL', 'MTD PnL', 'QTD PnL',
                 'YTD PnL']},

    'unlev_ytw_cols': ['Fund Entity Code', 'Rating', 'Fund Wght', 'Yield to Worst'],

    'group_by': ['Fund Entity Code', 'Rating']}

BETA_DIFF_RULES = {'rules': {'Security Type': ['Currency Swap', 'FX Forward', 'FX Option', 'Preferred Equity',
                                               'Collateralized Mortgage Obligation']},

                   'funds': BETA_BY_DIFF_FUNDS,

                   'other_funds': {'pivot_fields': {'idx': ['Fund Entity Code', 'Latest Issuer Ult'],
                                                    'vals': ['Beta Contribution_PREV_DATE', 'Impact Fund Wght',
                                                             'Beta Contribution_CUR_DATE', 'Impact Beta',
                                                             'Beta Contribution_DIF',
                                                             'MV Equivalent USD_PREV_DATE',
                                                             'MV Equivalent USD_CUR_DATE',
                                                             'MV Equivalent USD_DIF', 'Quantity_PREV_DATE',
                                                             'Quantity_CUR_DATE',
                                                             'Quantity_DIF', 'Price Local_PREV_DATE',
                                                             'Price Local_CUR_DATE'],
                                                    'aggregation': {'Beta Contribution_PREV_DATE': np.sum,
                                                                    'Beta Contribution_CUR_DATE': np.sum,
                                                                    'Impact Fund Wght': np.sum,
                                                                    'Impact Beta': np.mean,
                                                                    'Beta Contribution_DIF': np.sum,
                                                                    'MV Equivalent USD_PREV_DATE': np.sum,
                                                                    'MV Equivalent USD_CUR_DATE': np.sum,
                                                                    'MV Equivalent USD_DIF': np.sum,
                                                                    'Quantity_PREV_DATE': np.sum,
                                                                    'Quantity_CUR_DATE': np.sum,
                                                                    'Quantity_DIF': np.sum,
                                                                    'Price Local_PREV_DATE': np.mean,
                                                                    'Price Local_CUR_DATE': np.mean
                                                                    }}},

                   'TRF': {'pivot_fields': {
                       'idx': ['Fund Entity Code', 'FO Strategy Broad Category', 'Latest Issuer Ult', 'Security'],
                       'vals': ['Beta Contribution_PREV_DATE', 'Impact Fund Wght',
                                'Beta Contribution_CUR_DATE', 'Impact Beta',
                                'Beta Contribution_DIF',
                                'MV Equivalent USD_PREV_DATE', 'MV Equivalent USD_CUR_DATE',
                                'MV Equivalent USD_DIF', 'Quantity_PREV_DATE',
                                'Quantity_CUR_DATE',
                                'Quantity_DIF', 'Price Local_PREV_DATE',
                                'Price Local_CUR_DATE'],
                       'aggregation': {'Beta Contribution_PREV_DATE': np.sum,
                                       'Beta Contribution_CUR_DATE': np.sum,
                                       'Impact Fund Wght': np.sum,
                                       'Impact Beta': np.mean,
                                       'Beta Contribution_DIF': np.sum,
                                       'MV Equivalent USD_PREV_DATE': np.sum,
                                       'MV Equivalent USD_CUR_DATE': np.sum,
                                       'MV Equivalent USD_DIF': np.sum,
                                       'Quantity_PREV_DATE': np.sum,
                                       'Quantity_CUR_DATE': np.sum, 'Quantity_DIF': np.sum,
                                       'Price Local_PREV_DATE': np.mean,
                                       'Price Local_CUR_DATE': np.mean
                                       }}},

                   'col_rename': {'Price Local_PREV_DATE': 'Px_Prev_Date',
                                  'Beta Contribution_PREV_DATE': 'Prev BetaContr',
                                  'MV Equivalent USD_PREV_DATE': 'Prev.$MVE', 'Quantity_PREV_DATE': 'Prev.Qty',
                                  'Price Local_CUR_DATE': 'Px C. Date', 'Beta Contribution_CUR_DATE': 'C. BetaContr',
                                  'MV Equivalent USD_CUR_DATE': 'C.$MVE', 'Quantity_CUR_DATE': 'C.Qty',
                                  'Beta Contribution_DIF': 'Net Beta Chg', 'MV Equivalent USD_DIF': '$MVE Chg',
                                  'Quantity_DIF': 'Qty_Diff', 'Impact Fund Wght': 'NAV_%_impact',
                                  'Impact Beta': 'BetaCoeff_impact'},

                   }

PIVOT_AGGFUNCS = ['sum', 'mean', 'count', 'min', 'max', 'std', 'prod']

MAX_ROWS_DISPLAY = 10_000
MAX_COLS_DISPLAY = 45
