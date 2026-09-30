import logging
from dash import html, dcc
from dash_reports.analyst_questionnaire import pages
from common.utils import dtutil


def create_tabs(id_bb_company, path):
    period_type = 'LTM'
    to_date = dtutil.today()
    from_date = dtutil.dateadd(dtutil.boyear(to_date, False), '-5y')

    tabs = {'debt': 'Debt Issuance Outlook', 'equity': 'Equity Issuance Outlook', 'acquisition': 'Acquirer or Acquired',
            'ratings': 'Ratings Momentum', 'mgmt': 'Management Team Perception', 'ownership': 'Ownership Details'}

    tabs_html = {}
    if id_bb_company > 0:
        tabs_html['debt'] = pages.debt_issuance.result_debt_issuance_layout(id_bb_company, from_date, to_date,
                                                                            period_type,
                                                                            path)
        tabs_html['equity'] = pages.equity_issuance.result_equity_issuance_layout(id_bb_company, from_date, to_date,
                                                                                  period_type, path)
        tabs_html['acquisition'] = pages.acquirer_or_acquired.result_acquisition_layout(id_bb_company, from_date,
                                                                                        to_date,
                                                                                        period_type,
                                                                                        path)
        tabs_html['ratings'] = pages.ratings_momentum.result_ratings_momentum_layout(id_bb_company, from_date, to_date,
                                                                                     period_type,
                                                                                     path)
        rating_type = 'STDLONG'
        tabs_html['mgmt'] = pages.mgmnt_team_perception.result_management_perspective_layout(id_bb_company, from_date,
                                                                                             to_date,
                                                                                             period_type, path,
                                                                                             rating_type)
        tabs_html['ownership'] = pages.ownership_details.result_ownership_details_layout(id_bb_company, from_date,
                                                                                         to_date,
                                                                                         period_type,
                                                                                         path)

    tab_kids = []
    for k, _ in tabs_html.items():
        tab_html = tabs_html[k]
        tab_label = tabs[k]
        tab_kids.append(dcc.Tab(label=tab_label, children=tab_html))

    return dcc.Tabs(tab_kids)


def create_layout(id_bb_company, path):
    id_bb_company = int(id_bb_company)

    logging.info(f"generating company info layout: {id_bb_company}")
    co_info_layout = create_tabs(id_bb_company, path)
    return html.Div(children=[co_info_layout], className='fund_filter')
