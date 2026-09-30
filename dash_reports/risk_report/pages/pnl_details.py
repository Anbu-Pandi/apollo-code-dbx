from dash_reports.report_common.data.data_model import datastore
from dash_reports.report_common.pages.pivot_page_helpers import create_pivot_controls


def create_layout(app, fund):
    raw_df = datastore.get_pnl()

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

    return create_pivot_controls(False, 'PnL Details', filterdf, fund=fund,
                                 rows=['Fund Entity Code', 'FO Strategy Broad Category'],
                                 values=['Beta PnL', 'Alpha PnL', 'Rate PnL', 'Total PnL Ex Rate',
                                         'P&L DTD Total USD', ],
                                 aggfunc={'Rate PnL': ['sum'], 'Beta PnL': ['sum'], 'Alpha PnL': ['sum'],
                                          'P&L DTD Total USD': ['sum'], 'Total PnL Ex Rate': ['sum']})
