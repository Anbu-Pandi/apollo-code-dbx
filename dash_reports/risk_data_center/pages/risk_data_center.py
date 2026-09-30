from dash_reports.report_common.pages.link_and_headers import get_page_title
from data_access.beta_attribution.ice_index import get_timeseries
from data_access.dbtables.index_data.etf_constituents import get_timeseries_constituents
from dash_reports.risk_data_center.data.risk_data import get_risk_ts, get_risk_data, \
    get_bnchmrk_beta_cdxhy, get_spac_betas
from dash_reports.risk_data_center.pages.utils import *
from dash import Input, Output, State, ALL, MATCH, callback


def get_data_center_headers():
    layout = html.Div([get_page_title("Data Center"),
                       html.Br(),
                       get_data_center_links(),
                       html.Br(),
                       get_date_section(),
                       html.Br(),
                       html.Div(id='output-container'),
                       ])
    return layout


def get_data_for_download(start_date, end_date, fund, risk_metric):
    if risk_metric == 'Risk Enhanced':
        df = get_risk_data(start_date, end_date, fund)
        file_name = f"risk_{start_date}_{end_date}_{fund}.csv"
    elif risk_metric == 'ICE Index Holdings':
        df = get_timeseries(start_date, end_date, fund)
        file_name = f"ICE_{start_date}_{end_date}_{fund}.csv"
    elif risk_metric == 'ETF Index Holdings':
        df = get_timeseries_constituents(start_date, end_date, fund)
        file_name = f"ETF_{start_date}_{end_date}_{fund}.csv"

    return df, file_name


def get_result_layout(start_date, end_date, fund, risk_metric, grpby_data):
    start_dt = pd.to_datetime(start_date).date()
    end_dt = pd.to_datetime(end_date).date()

    if risk_metric in data_for_download_list:
        df, file_name = get_data_for_download(start_date, end_date, fund, risk_metric)
        return '', dcc.send_data_frame(df.to_csv, file_name)
    elif risk_metric in risk_metric_list:
        data, fig = get_risk_ts(start_dt, end_dt, fund, risk_metric, grpby_data)
        return get_dash_table(data, fig=fig, title=risk_metric), ''
    elif risk_metric == 'Benchmark Beta to CDXHY':
        data = get_bnchmrk_beta_cdxhy(start_date, end_date, get_bnchmrk_beta_cdxhy)
        return get_dash_table(data, title=risk_metric), ''
    elif risk_metric == 'SPAC Beta':
        df = get_spac_betas(start_date, end_date)
        file_name = f'spac_betas_{start_date}_{end_date}.csv'
        return '', dcc.send_data_frame(df.to_csv, file_name)


@callback(
    Output('metric-result-container', 'children'),
    Output("download-dataframe-csv", "data"),
    Input("risk_data_center_submit_button", "n_clicks"),
    State('start_date_control', 'date'),
    State('end_date_control', 'date'),
    State('fund', 'value'),
    State('risk_metrics', 'value'),
    State('grpby', 'value'),
    prevent_initial_call=True
)
def on_risk_data_portal_get_data(n_clicks, start_date, end_date, fund=None, risk_metric=None, grpby_data=None):
    return get_result_layout(start_date, end_date, fund, risk_metric, grpby_data)
