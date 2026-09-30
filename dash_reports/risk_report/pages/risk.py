from dash_reports.report_common.data.data_model import datastore
from dash_reports.report_common.pages import pivot_page_helpers
from dash_reports.risk_report.data.risk_overview import generate_risk_overview


def create_layout(app, fund, hidepivotcontrols=False):
    risk_df, stress_scenario_df = datastore.read_or_create_cache_for_df(__file__, lambda: generate_risk_overview())

    def filterdf(fund):
        if isinstance(fund, str):
            fund = [fund]
        else:
            fund = fund

        if 'ALL' in fund:
            temp_risk = risk_df
        else:
            temp_risk = risk_df.loc[risk_df['Fund Entity Code'].isin(fund)]

        return temp_risk

    return pivot_page_helpers.create_pivot_controls(hidepivotcontrols, 'Risk Overview', filterdf, fund=fund)


def create_stress_layout(app, fund):
    risk_df, stress_scenario_df = datastore.read_or_create_cache_for_df(__file__, lambda: generate_risk_overview())

    def filterdf(fund):
        if isinstance(fund, str):
            fund = [fund]
        else:
            fund = fund

        if 'ALL' in fund:
            temp_risk = stress_scenario_df
        else:
            temp_risk = stress_scenario_df.loc[stress_scenario_df['Fund Entity Code'].isin(fund)]

        return temp_risk

    return pivot_page_helpers.create_pivot_controls(False, 'Risk Stress Overview', filterdf, fund=fund)
