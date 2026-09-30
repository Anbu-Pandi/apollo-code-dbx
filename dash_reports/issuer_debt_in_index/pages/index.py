def get_debt_index_page():
    view_config = {}
    view_config['values'] = ['Price', 'Yield To Worst Conv', 'Yield To Maturity Conv',
                             'Oas', 'Oas 1y Min', 'Oas 1y Max', 'Oas 1y %-ile',
                             'Effective Duration', 'Spread Duration', 'Amt Issued Usd', 'Amt Outstanding Usd',
                             'Issuer Amt Outstanding Usd',
                             'Volume 1M', 'Gross Volume 1M', 'Etf Change 1M', 'Beta',
                             'Bond Point Dtd Return', 'Bond Point Beta Dtd Return', 'Bond Point Alpha Dtd Return',
                             'Bond Point Mtd Return', 'Bond Point Beta Mtd Return', 'Bond Point Alpha Mtd Return',
                             'Bond Point 1M Return', 'Bond Point Beta 1M Return', 'Bond Point Alpha 1M Return',
                             'Bond Point Qtd Return', 'Bond Point Beta Qtd Return', 'Bond Point Alpha Qtd Return',
                             'Bond Point Ytd Return', 'Bond Point Beta Ytd Return', 'Bond Point Alpha Ytd Return',
                             'Oas 1 Day Chg', 'Oas 1 Week Chg', 'Oas 1 Mo Chg', 'Oas 3 Mo Chg', 'Oas 12 Mo Chg',
                             'Oas Mtd Change', 'Oas Qtd Chg', 'Oas Ytd Chg']
    view_config['rows'] = ['Ticker', 'Coupon', 'Maturity', 'Instrument Id', 'Bbg Id', 'Maturity Years', 'Currency',
                           'Narrow Industry', 'Instrument Type', 'Seniority', 'Rating', 'Current Coupon', 'Index', ]
    view_config['fixed_cols'] = 3

    return view_config


def get_sec_unsec_bonds_page():
    view_config = {}
    view_config['rows'] = ['Ticker', 'Coupon', 'Current Coupon', 'Maturity', 'Maturity Years', 'Currency',
                           'Narrow Industry', 'Instrument Type', 'Seniority', 'Rating', 'Index', 'Instrument Id',
                           'Secured Unsecured']
    view_config['values'] = ['Price', 'Yield To Worst Conv Usd', 'Oas', 'Oas Usd', 'Pecs Rr=0.4 Usd', 'Curve',
                             'Spread Diff Bond V Curve', 'Spread Diff Bond V Other Curve',
                             'Secured Curve', 'Unsecured Curve']
    view_config['filter_cols'] = []
    view_config['filter_vals'] = []
    view_config['fixed_cols'] = 3

    return view_config


def get_sec_unsec_issuers_page():
    view_config = {}
    view_config['rows'] = ['Ticker', 'Narrow Industry', ]
    view_config['cols'] = ['Secured Unsecured', 'Maturity Bucket']
    view_config['values'] = ['Maturity Bucket Spread']
    view_config['filter_cols'] = ['Secured Unsecured Group']
    view_config['filter_vals'] = ['True']

    return view_config
