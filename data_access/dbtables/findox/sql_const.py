from common.utils import dfutil

CAPIQ_FINDOX_MAP = {
    'IQ_EBITDA': ['Adjusted EBITDA (mgmt)', 'Pro Forma EBITDA (mgmt)', 'adj. EBITDA'],
    'IQ_CAPEX': 'Capital expenditures',
    'IQ_NET_INTEREST_EXP': ['Net Cash Interest Paid', 'Net interest paid'],
    'IQ_GROSS_DEBT_LEASES_CAP': 'Gross Debt (Leases Capitalized)',
    'IQ_NET_DEBT_LEASES_CAP': 'Net Debt (Leases Capitalized)',
    'IQ_CAPITAL_LEASES_DEBT': 'Total Financial/Capital Leases Debt',
    'IQ_TOTAL_DEBT': ['CUSTOM_TOTAL_DEBT', 'Gross financial debt'],
    'IQ_NET_DEBT': ['CUSTOM_NET_DEBT', 'Net financial debt'],
    'IQ_TOTAL_ASSETS': ['Total Assets', 'Total assets'],
    'IQ_NI': ['Net Income in CF', 'Net profit in CF'],
    'IQ_CASH_EQUIV': ['Cash & Cash Equivalents', 'Cash and cash equivalents'],
    'IQ_TOTAL_REVENUE': ['Revenue', 'Revenues'],
    'IQ_EBITDA_CAPEX_INT': 'CUSTOM_EBITDA_CAPEX',
    'IQ_TOTAL_DEBT_EBITDA': 'CUSTOM_TOTAL_DEBT_EBITDA'
}

CAPIQ_FINDOX_MAP2 = {
    # =====================================================================
    # Revenue / Sales
    # =====================================================================
    'IQ_TOTAL_REV': ['Revenue', 'Revenues', 'Total revenue'],
    'IQ_TOTAL_REV_SHARE': ['Revenue per share'],
    'IQ_COST_REV': ['Cost of goods sold', 'Cost of revenue', 'COGS', 'Cost of sales'],
    'IQ_TOTAL_REVENUE': ['Revenue', 'Revenues'],

    # =====================================================================
    # EBITDA / EBIT / Operating Income
    # =====================================================================
    'IQ_EBITDA': ['Adjusted EBITDA (mgmt)', 'Pro Forma EBITDA (mgmt)', 'adj. EBITDA'],
    'IQ_EBIT': ['EBIT', 'Operating income', 'Earnings before interest and taxes'],

    # =====================================================================
    # Depreciation & Amortization
    # =====================================================================
    'IQ_DA': ['Depreciation and amortization', 'Depreciation & amortization', 'D&A'],
    'IQ_DA_EBITDA': ['Depreciation and amortization', 'Depreciation & amortization', 'D&A'],
    'IQ_DA_CF': ['Depreciation and amortization (CF)', 'Depreciation and amortization', 'D&A'],

    # =====================================================================
    # SG&A / Operating Expenses
    # =====================================================================
    'IQ_SGA': ['SG&A', 'Selling, general and administrative', 'SG&A expenses'],
    'IQ_TOTAL_OTHER_OPER': ['Other operating expenses', 'Other operating costs'],
    'IQ_RD_EXP': ['Research and development', 'R&D expenses', 'Research & development'],

    # =====================================================================
    # Interest
    # =====================================================================
    'IQ_INTEREST_EXP': ['Interest Expense', 'Interest expense',
                        'Total interest expense', 'Interest paid'],
    'IQ_CASH_INTEREST': ['Interest paid', 'Cash interest paid', 'Interest Expense'],
    'IQ_INTEREST_INVEST_INC': ['Interest Income', 'Interest income', 'Interest received'],
    'IQ_NET_INTEREST_EXP': ['Net Cash Interest Paid', 'Net interest paid'],

    # =====================================================================
    # Pre-Tax Income / Tax
    # =====================================================================
    'IQ_EBT': ['Pre-tax income', 'Earnings before taxes', 'Income before taxes'],
    'IQ_INC_TAX': ['Income tax expense', 'Income taxes', 'Tax expense'],
    'IQ_CASH_TAXES': ['Taxes paid', 'Cash taxes paid'],
    'IQ_EFFECT_TAX_RATE': ['Effective tax rate'],
    'IQ_INC_TAX_PAY_CURRENT': ['Income taxes payable', 'Tax payable', 'Taxes payable'],

    # =====================================================================
    # Net Income / EPS
    # =====================================================================
    'IQ_NI_CF': ['Net Income in CF', 'Net profit in CF'],
    'IQ_BASIC_EPS_EXCL': ['Basic EPS', 'Earnings per share (basic)'],
    'IQ_DILUT_EPS_EXCL': ['Diluted EPS', 'Earnings per share (diluted)'],
    'IQ_NI': ['Net Income in CF', 'Net profit in CF'],

    # =====================================================================
    # Other Income Statement Items
    # =====================================================================
    'IQ_DO': ['Discontinued operations', 'Income from discontinued operations',
              'Net Income Excl. Discontinued Operations'],
    'IQ_EXTRA_ACC_ITEMS': ['Extraordinary items'],
    'IQ_TOTAL_UNUSUAL': ['Unusual items', 'Unusual expenses'],
    'IQ_OTHER_NON_OPER_EXP': ['Non-operating expenses', 'Other non-operating expenses'],
    'IQ_CURRENCY_GAIN': ['Foreign currency gains/losses', 'Currency translation adjustment'],
    'IQ_LEGAL_SETTLE': ['Legal settlements', 'Litigation expenses'],
    'IQ_GAIN_ASSETS': ['Gain on sale of assets', 'Gain (loss) on sale of assets'],
    'IQ_MINORITY_INTEREST_IS': ['Minority interest', 'Non-controlling interest'],
    'IQ_MINORITY_INTEREST_TOTAL': ['Minority interest (total)', 'Non-controlling interest (total)'],
    'IQ_IMPAIRMENT_GW': ['Goodwill impairment', 'Impairment of goodwill'],
    'IQ_ASSET_WRITEDOWN': ['Asset writedown', 'Impairment of assets', 'Asset impairment'],

    # =====================================================================
    # Balance Sheet - Assets
    # =====================================================================
    'IQ_TOTAL_CA': ['Total current assets', 'Current assets'],
    'IQ_CASH_ST_INVEST': ['Cash and short-term investments', 'Cash & short-term investments'],
    'IQ_RESTRICTED_CASH': ['Restricted cash (current)', 'Restricted cash'],
    'IQ_INVENTORY': ['Inventory', 'Inventories'],
    'IQ_WIP_INV': ['Work in progress inventory', 'WIP inventory'],
    'IQ_PREPAID_EXP': ['Prepaid expenses', 'Prepaid expenses and other current assets'],
    'IQ_GPPE': ['Gross PP&E', 'Property, plant & equipment (gross)', 'PP&E (gross)'],
    'IQ_NPPE': ['Net PP&E', 'Property, plant & equipment (net)', 'PP&E (net)'],
    'IQ_LT_INVEST': ['Long-term investments', 'Investments'],
    'IQ_TOTAL_ASSETS': ['Total Assets', 'Total assets'],
    'IQ_CASH_EQUIV': ['Cash & Cash Equivalents', 'Cash and cash equivalents'],

    # =====================================================================
    # Balance Sheet - Debt / Leases
    # =====================================================================
    'IQ_TOTAL_DEBT_NON_CURRENT': ['Long-term debt', 'Non-current debt', 'Long-term financial debt'],
    'IQ_TOTAL_DEBT_CURRENT': ['Short-term debt', 'Current debt', 'Current financial debt'],
    'IQ_DEF_CHARGES_LT': ['Deferred charges', 'Deferred charges (long-term)'],
    'IQ_NET_DEBT_LEASES_CAP': 'Net Debt (Leases Capitalized)',
    'IQ_GROSS_DEBT_LEASES_CAP': 'Gross Debt (Leases Capitalized)',
    'IQ_CAPITAL_LEASES_DEBT': 'Total Financial/Capital Leases Debt',

    # =====================================================================
    # Balance Sheet - Liabilities
    # =====================================================================
    'IQ_TOTAL_LIAB': ['Total liabilities', 'Total Liabilities'],
    'IQ_TOTAL_CL': ['Total current liabilities', 'Current liabilities'],
    'IQ_TOTAL_LIAB_EQUITY': ['Total liabilities and equity',
                             'Total liabilities & shareholders equity'],
    'IQ_AP': ['Accounts payable', 'Trade payables'],
    'IQ_AE': ['Accrued expenses', 'Accrued liabilities'],
    'IQ_TOTAL_DEBT': ['CUSTOM_TOTAL_DEBT', 'Gross financial debt'],
    'IQ_NET_DEBT': ['CUSTOM_NET_DEBT', 'Net financial debt'],

    # =====================================================================
    # Balance Sheet - Equity
    # =====================================================================
    'IQ_TOTAL_EQUITY': ['Total equity', 'Total shareholders equity', 'Total Equity'],
    'IQ_TOTAL_COMMON_EQUITY': ['Common equity', 'Total common equity'],
    'IQ_APIC': ['Additional paid-in capital', 'APIC'],
    'IQ_PREF_EQUITY': ['Preferred equity', 'Preferred stock'],
    'IQ_TREASURY': ['Treasury stock', 'Treasury shares'],

    # =====================================================================
    # Cash Flow Statement
    # =====================================================================
    'IQ_CASH_OPER': ['Cash from operations', 'Operating cash flow',
                     'Cash flow from operations'],
    'IQ_CASH_INVEST': ['Cash from investing', 'Investing activities',
                       'Cash flow from investing'],
    'IQ_CASH_FINAN': ['Cash from financing', 'Financing activities',
                      'Cash flow from financing'],
    'IQ_NET_CHANGE': ['Net change in cash', 'Change in cash and cash equivalents'],
    'IQ_TOTAL_DIV_PAID_CF': ['Total dividends paid', 'Dividends paid'],
    'IQ_COMMON_DIV_CF': ['Common dividends paid', 'Dividends paid'],
    'IQ_SPECIAL_DIV_CF': ['Special dividends', 'Special dividends paid'],
    'IQ_PREF_DIV_CF': ['Preferred dividends', 'Preferred dividends paid'],
    'IQ_DIV_SHARE': ['Dividends per share'],
    'IQ_LT_DEBT_ISSUED': ['Long-term debt issued', 'Issuance of long-term debt',
                          'Proceeds from long-term debt'],
    'IQ_LT_DEBT_REPAID': ['Long-term debt repaid', 'Repayment of long-term debt'],
    'IQ_ST_DEBT_ISSUED': ['Short-term debt issued', 'Increase in short-term borrowings'],
    'IQ_ST_DEBT_REPAID': ['Short-term debt repaid', 'Decrease in short-term borrowings'],
    'IQ_SALE_PPE_CF': ['Sale of PP&E', 'Proceeds from sale of PP&E',
                       'Sale of property, plant & equipment'],
    'IQ_CASH_ACQUIRE_CF': ['Cash acquisitions', 'Acquisitions', 'Business acquisitions'],
    'IQ_DIVEST_CF': ['Divestitures', 'Proceeds from divestitures', 'Sale of business'],
    'IQ_COMMON_REP': ['Common stock repurchased', 'Share buyback', 'Stock repurchase'],
    'IQ_COMMON_ISSUED': ['Common stock issued', 'Stock issuance',
                         'Proceeds from stock issuance'],
    'IQ_INVEST_SECURITY_CF': ['Investment in securities', 'Purchase of investment securities'],
    'IQ_CAPEX': 'Capital expenditures',

    # =====================================================================
    # Working Capital
    # =====================================================================
    'IQ_WORKING_CAP': ['Working capital'],
    'IQ_CHANGE_NET_OPER_ASSETS': ['Change in working capital', 'Changes in working capital'],

    # =====================================================================
    # Shares Outstanding
    # =====================================================================
    'IQ_BASIC_WEIGHT': ['Basic shares outstanding', 'Weighted average shares (basic)'],
    'IQ_DILUT_WEIGHT': ['Diluted shares outstanding', 'Weighted average shares (diluted)'],

    # =====================================================================
    # Margins / Ratios
    # =====================================================================
    'IQ_EBITDA_MARGIN': ['EBITDA margin'],
    'IQ_NI_MARGIN': ['Net income margin', 'Net margin'],
    'IQ_TOTAL_REV_1YR_ANN_GROWTH': ['Revenue growth'],
    'IQ_LT_DEBT_EQUITY': ['Long-term debt to equity'],
    'IQ_TOTAL_DEBT_CAPITAL': ['Total debt to capital'],
    'IQ_EBIT_INT': ['EBIT interest coverage'],

    # =====================================================================
    # Valuation Multiples
    # =====================================================================
    'IQ_TEV': ['Enterprise value'],
    'IQ_TEV_EBITDA': ['EV/EBITDA'],
    'IQ_TEV_TOTAL_REV': ['EV/Revenue', 'EV/Sales'],
    'IQ_TEV_EBIT': ['EV/EBIT'],
    'IQ_PBV': ['Price to book value', 'P/B ratio'],
    'IQ_PTBV': ['Price to tangible book value'],

    # =====================================================================
    # Lease-Related
    # =====================================================================
    'IQ_OPER_LEASE_EXP': ['Operating lease expense', 'Lease expense', 'Operating lease payments'],

    # =====================================================================
    # Custom Computed Fields
    # =====================================================================
    'IQ_EBITDA_INT': 'CUSTOM_EBITDA_INT',
    'IQ_EBITDA_LEASE_ADJUSTED': 'CUSTOM_EBITDA_LEASE_ADJUSTED',
    'IQ_CAPEX_PCT_REV': 'CUSTOM_CAPEX_PCT_REVENUE',
    'IQ_EBITDA_CAPEX_INT': 'CUSTOM_EBITDA_CAPEX',
    'IQ_TOTAL_DEBT_EBITDA': 'CUSTOM_TOTAL_DEBT_EBITDA'

}


REVERSE_CAPIQ_FINDOX_MAP = {}
for capiq_key, findox_values in CAPIQ_FINDOX_MAP.items():
    for value in dfutil.tolist(findox_values):
        REVERSE_CAPIQ_FINDOX_MAP[value] = capiq_key

PERIOD_TYPE_MAP = {
    'q': 'qrt',
    'l': 'ltm',
    'y': 'fy',
    'h': 'hy',
    's': 'hy',
}

ZERO_DEFAULT_FIELDS = ['IQ_CAPITAL_LEASES_DEBT']

CUSTOM_FINDOX_FIELDS = {
    'CUSTOM_TOTAL_DEBT': ('<IQ_GROSS_DEBT_LEASES_CAP> - <IQ_CAPITAL_LEASES_DEBT>',
                          'Total Debt (ex-Leases)'),
    'CUSTOM_NET_DEBT': ('<IQ_NET_DEBT_LEASES_CAP> - <IQ_CAPITAL_LEASES_DEBT>',
                        'Net Debt (ex-Leases)'),
    'CUSTOM_TOTAL_DEBT_EBITDA': ('<IQ_TOTAL_DEBT> / <IQ_EBITDA>',
                                 'Debt / EBITDA'),
    'CUSTOM_NET_DEBT_EBITDA': ('<IQ_NET_DEBT> / <IQ_EBITDA>',
                               'Net Debt / EBITDA'),
    'CUSTOM_TOTAL_DEBT_ASSETS': ('<IQ_TOTAL_DEBT> / <IQ_TOTAL_ASSETS>',
                                 'Total Debt / Assets'),
    'CUSTOM_NET_DEBT_ASSETS': ('<IQ_NET_DEBT> / <IQ_TOTAL_ASSETS>',
                               'Net Debt / Assets'),
    'CUSTOM_EBITDA_CAPEX': ('(<IQ_EBITDA> - <IQ_CAPEX>) /  <IQ_NET_INTEREST_EXP>',
                            '(EBITDA - Capex) / Interest Expense'),
}

COMPANY_FINANCIALS_SQL = '''
WITH selected_companies AS (
  SELECT DISTINCT id
  FROM dbo.findox_agg_universe
  WHERE ticker IN {2} OR findox_id IN {2}
), issuerslist AS (
  SELECT fmc.issuer_id, fmc.client_issuer_name, fmc.system_issuer_name,
         concat_ws(';', sort_array(collect_set(concat(fmc.identifier_type_name, ':', fmc.identifier)))) AS identifier
  FROM findox_models_common_ids fmc
  WHERE fmc.issuer_id IN (
    SELECT findox_id FROM dbo.findox_agg_universe
    WHERE ticker IN {2} OR findox_id IN {2}
  )
  GROUP BY fmc.issuer_id, fmc.client_issuer_name, fmc.system_issuer_name
),
univ_rk AS (
    select *,
    row_number() over(partition by id order by financial_data_updated desc) as rw
  from dbo.findox_agg_universe
),
univ AS (
  SELECT * from univ_rk where rw = 1
),
per_dt as (
  select *, row_number() over(partition by id order by asof_date_time desc, load_date desc) as rw
  from findox_agg_periods
  WHERE company_id IN (SELECT id FROM selected_companies)
),
per as (
  select * except(rw)
  from per_dt
  where rw=1
),
sdl_dt as (
  SELECT *, row_number() over(partition by id, company_id, standard_line_id order by asof_date_time desc, load_date desc) as rw
  FROM findox_agg_standard_data_lines sda
  WHERE company_id IN (SELECT id FROM selected_companies)
),
sdl as (
  SELECT * except(rw)
  FROM sdl_dt
  where rw=1
),
sda_dt AS (
  SELECT *, row_number() over(partition by report_id, line_id order by asof_date_time desc, load_date desc) as rw
  FROM findox_agg_standard_data_actual sda
  WHERE company_id IN (SELECT id FROM selected_companies)
),
sda AS (
  SELECT * except(rw)
  FROM sda_dt
  where rw=1
),
sdlm AS (
  SELECT line_label, line_id, period, other_labels, company_id, asof_date, asof_date_time, count(*) ct
  FROM findox_agg_standard_data_lines_map
  WHERE company_id IN (SELECT id FROM selected_companies)
  group by all
)	
select	
	sda.report_id,
	sda.period_id,
	sda.company_id,
	agg.findox_id,
	agg.last_period,
	agg.next_report_date,
	sda.company_ticker,
	sdl.id as statement_id,
	sdl.standard_line_group as statement_group_id,
	sda.line_id,
	sdl.[label],
	sdlm.other_labels,
	sda.data_{0},
	agg.industry_l3,
	il.client_issuer_name,
	il.system_issuer_name,
	il.identifier,
	fp.period,
	fp.secondary_period,
	fp.calendar_period, 
	fp.period_type,
	fp.end_of_reporting_period,
	fp.report_issue_date,
	fp.reporting_currency,
	agg.financial_data_updated,
	row_number() over(partition by sda.report_id, sdl.[label]
      order by agg.financial_data_updated desc, sda.asof_date_time desc, sda.load_date desc,
               sdl.order, sdl.id, sdl.order_line, sda.line_id, sdlm.other_labels) as rk
FROM sda sda
    LEFT JOIN sdl sdl
      ON CAST(sdl.company_id AS INT) = CAST(sda.company_id AS INT)
      AND CAST(sdl.standard_line_id AS INT) = CAST(sda.line_id AS INT)
      AND coalesce(sda.asof_date_time, getdate()) = coalesce(sdl.asof_date_time, getdate())
    LEFT JOIN univ agg ON agg.id = sda.company_id
    LEFT JOIN issuerslist il ON il.issuer_id = agg.findox_id
    LEFT JOIN per fp
      ON CAST(fp.company_id AS INT) = CAST(sdl.company_id AS INT)
      AND coalesce(sda.asof_date_time, getdate()) = coalesce(fp.asof_date_time, getdate())
      AND sda.period_id = fp.id
    LEFT JOIN sdlm
      ON CAST(sdlm.company_id AS INT) = CAST(sda.company_id AS INT)
      AND CAST(sdlm.line_id AS INT) = CAST(sda.line_id AS INT)
      AND coalesce(sdlm.asof_date_time, getdate()) = coalesce(sda.asof_date_time, getdate())
      AND sdlm.period = fp.period
where
	(
        sda.data_ltm is not null
        or sda.data_qrt is not null
        or sda.data_ytd is not null
        or sda.data_hy is not null
        or sda.data_fy is not null
	)
	and (sda.company_ticker IN {2} 
	    OR agg.findox_id IN {2})
	and fp.end_of_reporting_period BETWEEN '{3}' and '{4}'
	{1}
QUALIFY rk = 1
order by
	sda.period_id desc, sdl.id, sda.line_id, sdl.order_line asc
'''
