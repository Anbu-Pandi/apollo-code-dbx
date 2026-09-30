from dash_reports.report_common.data.data_model import datastore
from dash_reports.report_common.pages.pivot_page_helpers import create_pivot_controls


def create_layout(app, fund):
    raw_df = datastore.get_cur_risk()

    def filterdf(fund):
        if isinstance(fund, str):
            fund = [fund]
        else:
            fund = fund

        if 'ALL' in fund:
            temp_risk = raw_df
        else:
            temp_risk = raw_df.loc[raw_df['Fund Entity Code'].isin(fund)]

        return temp_risk

    return create_pivot_controls(False, 'Risk Details', filterdf, fund=fund,
                                 rows=['Fund Entity Code', 'FO Strategy Broad Category'],
                                 columns=['As Of Date'],
                                 values=['Fund Wght', 'Quantity', 'MV USD', 'MV Local', 'MV Equivalent USD',
                                         'Beta Contribution'],
                                 aggfunc={'Fund Wght': ['sum'], 'Quantity': ['sum'], 'MV USD': ['sum'],
                                          'MV Local': ['sum'], 'MV Equivalent USD': ['sum'],
                                          'Beta Contribution': ['sum']})
