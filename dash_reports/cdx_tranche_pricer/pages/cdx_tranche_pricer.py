import dash_core_components as dcc
import dash_html_components as html
from dash import dcc

from common.utils.dtutil import today, dateadd
from dash_reports.report_common.pages.link_and_headers import get_page_title
from dash_reports.report_common.utils.combos import get_labelled_component
from dash_reports.report_common.utils.components import get_datepicker_control

TRANCHE_PRICER_RESULTS_ID = 'tranche_pricer_results'
TRANCHE_DATE_ID = 'tranche_date_id'
TRANCHE_UPLOAD_ID = 'tranche_upload_id'
ISSUER_DF_STORE_ID = 'issuer_df_store_id'
TRANCHE_DOWNLOAD_ID = 'tranche_download_id'
TRACHE_DOWNLOAD_BUTTON_ID = 'tranche_download_button_id'
TRANCHE_CALC_BUTTON_ID = 'tranche_calc_button_id'
TRANCHE_UPLOADED_DF_ID = 'tranche_uploaded_df_id'


def create_tranche_pricer_layout():
    dt = dateadd(today(), '-1b')
    static_layout = html.Div([
        html.Br(),
        html.Div(children=[get_labelled_component('Value Date',
                                                  get_datepicker_control(dt=dt,
                                                                         id=TRANCHE_DATE_ID,
                                                                         disabled=False))],
                 className='fund_filter'),
        html.Br(),
        html.Div(children=[get_labelled_component('Attachment Point (%)',
                                                   dcc.Input(id='k1', type='number', value=0))],
                 className='fund_filter'),
        html.Br(),
        html.Div(children=[get_labelled_component('Detachment Point (%)',
                                                   dcc.Input(id='k2', type='number', value=100))],
                 className='fund_filter'),
        html.Br(),
        html.Div(children=[get_labelled_component('Attachment Point Correlation (%)',
                                                   dcc.Input(id='corr1', type='number', value=0))],
                 className='fund_filter'),
        html.Br(),
        html.Div(children=[get_labelled_component('Detachment Point Correlation (%)',
                                                   dcc.Input(id='corr2', type='number', value=30))],
                 className='fund_filter'),
        html.Br(),
        html.Div(children=[get_labelled_component('Running Coupon (bps)',
                                                   dcc.Input(id='running_coupon', type='number', value=500))],
                 className='fund_filter'),
        html.Br(),
        html.Div(children=[get_labelled_component('Maturity Date (tenor or yyyy-mm-dd format)',
                                                   dcc.Input(id='maturity_date', type='text', value='5Y'))],
                 className='fund_filter'),
        html.Br(),
        html.Div(dcc.Upload(html.Button('Upload Issuer Constituents File', className='submit-button'),
                            id=TRANCHE_UPLOAD_ID),
                 className='fund_filter'),
        html.Br(),
        html.Div(children=[get_labelled_component('Or Ticker',
                                                   dcc.Input(id='ticker', type='text', value='CDX-NAHY_42_1'))],
                 className='fund_filter'),
        html.Br(),
        html.Div(html.Button('Download Issuer Constituents Template', id=TRACHE_DOWNLOAD_BUTTON_ID,
                             className='submit-button'),
                 className='fund_filter'),
        html.Div(dcc.Download(id=TRANCHE_DOWNLOAD_ID)),
        html.Br(),
        dcc.Store(id=ISSUER_DF_STORE_ID, data={}, storage_type='memory'),
        html.Div(html.Button('Calculate', id=TRANCHE_CALC_BUTTON_ID, className='submit-button'),
                 className='fund_filter'),
        html.Br(),
        html.Div(children=[], id=TRANCHE_UPLOADED_DF_ID)
    ])

    dynamic_layout = html.Div(children=[], className='page', id=TRANCHE_PRICER_RESULTS_ID)
    return static_layout, dynamic_layout


def get_tranche_headers(app=None):
    if app is not None:
        app.title = 'CDX Tranche Pricer'
    layout = html.Div([get_page_title("CDX Tranche Pricer"),
                       html.Br(),
                       ])
    return layout
