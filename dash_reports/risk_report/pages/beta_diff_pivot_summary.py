from dash_reports.report_common.data.data_model import datastore
from dash_reports.report_common.pages.pivot_page_helpers import create_pivot_controls

"""Read Raw Data"""


def update_col(x):
    return x.replace('DIF', 'Change') \
        .replace('CUR', 'Current') \
        .replace('PREV', 'Previous') \
        .replace('DATE', 'Date') \
        .replace('Wght', 'Weight') \
        .replace('_', ' ')


def create_layout(app, fund):
    raw_df = datastore.get_beta_diff()

    def filterdf(fund):
        if isinstance(fund, str):
            fund = [fund]
        else:
            fund = fund

        if 'ALL' in fund:
            temp_risk = raw_df
        else:
            temp_risk = raw_df.loc[raw_df['Fund Entity Code'].isin(fund)]

        temp_risk.rename(columns={k: update_col(k) for k in temp_risk.columns}, inplace=True)
        return temp_risk

    return create_pivot_controls(False, 'Beta Diff Pivot Summary', filterdf, fund=fund,
                                 rows=['Fund Entity Code'],
                                 values=['Beta Contribution Change',
                                         'Impact Fund Weight', 'Impact Beta',
                                         'Fund Weight Change', 'Fund Weight Previous Date',
                                         'Fund Weight Current Date', ],
                                 aggfunc={'Beta Contribution Change': ['sum'],
                                          'Impact Fund Weight': ['sum'], 'Impact Beta': ['sum'],
                                          'Fund Weight Change': ['sum'], 'Fund Weight Previous Date': ['sum'],
                                          'Fund Weight Current Date': ['sum'], })
