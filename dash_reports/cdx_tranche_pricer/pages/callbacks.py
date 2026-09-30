import os
import logging
import traceback

import dash_core_components as dcc
import dash_html_components as html
from dash import Input, Output, State, callback, dcc

@callback(Output('issuer_df_store_id', 'data'),
          Output('tranche_uploaded_df_id', 'children'),
          Input('tranche_upload_id', 'contents'),
          State('tranche_upload_id', 'filename'),
          prevent_initial_call=True, )
def on_upload_cdx_file(contents, file_name):
    try:
        from dash_reports.cdx_tranche_pricer.data.utils import parse_contents
        from dash_reports.report_common.utils.components import make_dash_table

        logging.info(f'Uploading file: {file_name}')
        df = parse_contents(contents, file_name)
        logging.info(f'Successfully parsed {len(df)} rows from {file_name}')
        out_store = {'df': df.to_json(orient='records'), 'filename': file_name}
        out_div = html.Div([
            html.Br(),
            html.H6(f'Uploaded {len(df)} names from: {file_name}.:', className='subtitle'),
            make_dash_table(df)
        ], className='fund_filter')
        return out_store, out_div
    except Exception as e:
        logging.error(f'Error uploading file {file_name}: {str(e)}')
        logging.error(traceback.format_exc())
        error_div = html.Div([
            html.Br(),
            html.H6(f'Error uploading file:', style={'color': 'red'}, className='subtitle'),
            html.P(f'{str(e)}', style={'color': 'red'})
        ], className='fund_filter')
        return {}, error_div


def _get_template_file():
    template_file = os.path.join(os.path.split(__file__)[0], '../data/files/issuer_info.csv')
    return template_file


@callback(Output('tranche_download_id', 'data'),
          Input('tranche_download_button_id', 'n_clicks'),
          prevent_initial_call=True, )
def on_download_template(nclicks):
    try:
        template_file = _get_template_file()
        logging.info(f'Downloading template file: {template_file}')
        return dcc.send_file(template_file)
    except Exception as e:
        logging.error(f'Error downloading template: {str(e)}')
        logging.error(traceback.format_exc())
        raise


@callback(Output('tranche_pricer_results', 'children'),
          Input('tranche_calc_button_id', 'n_clicks'),
          State('issuer_df_store_id', 'data'),
          State('tranche_date_id', 'date'),
          State('k1', 'value'),
          State('k2', 'value'),
          State('corr1', 'value'),
          State('corr2', 'value'),
          State('running_coupon', 'value'),
          State('maturity_date', 'value'),
          State('ticker', 'value'),
          prevent_initial_call=True, )
def on_calc_tranche(nclicks, issuer_store, dt, k1, k2, corr1, corr2, running_coupon, maturity_date, ticker):
    try:
        import pandas as pd
        from dash_reports.cdx_tranche_pricer.data.calcs import calc_tranche
        from dash_reports.cdx_tranche_pricer.data.ticker_data import get_ticker_constituents
        from dash_reports.report_common.utils import dfhelpers
        from dash_reports.report_common.utils.components import make_dash_table

        logging.info(f'Calculate button clicked - dt={dt}, k1={k1}, k2={k2}, ticker={ticker}')
        dfs = []
        file_names = []

        if issuer_store:
            df = issuer_store.get('df')
            if issuer_store.get('filename'):
                file_names.append(issuer_store.get('filename'))
            if df is not None:
                df = pd.read_json(df, orient='records')
                dfs.append(df)
                logging.info(f'Loaded {len(df)} rows from uploaded file')

        if ticker:
            logging.info(f'Fetching ticker constituents for: {ticker}')
            template_df = pd.read_csv(_get_template_file())
            ticker_df = get_ticker_constituents(dt, ticker.split(','))
            ticker_df = ticker_df[[x for x in ticker_df.columns if x in template_df.columns.tolist()]]
            dfs.append(ticker_df)
            file_names.append(ticker)
            logging.info(f'Loaded {len(ticker_df)} rows from ticker {ticker}')

        file_name = ', '.join(file_names)
        if len(dfs) == 0:
            logging.warning('No data provided - neither file nor ticker')
            return html.H6(f'Please upload a file or provide a ticker', style={'color': 'red'}, className='subtitle')

        df = pd.concat(dfs)
        logging.info(f'Starting tranche calculation with {len(df)} total rows')

        out = calc_tranche(dt, df, float(k1) / 100, float(k2) / 100, float(running_coupon) / 1e4,
                           maturity_date, float(corr1) / 100, float(corr2) / 100)
        outdf = pd.DataFrame(out)
        outdf.columns = outdf.columns.map(dfhelpers.ui_column_name)
        
        logging.info(f'Calculation completed successfully - {len(outdf)} results')

        calc_div = html.Div([
            html.Br(),
            html.H6(f'Calculation Results:', className='subtitle'),
            make_dash_table(outdf),
            html.Br()
        ], className='fund_filter')

        df_div = html.Div([
            html.Br(),
            html.H6(f'Calculation Inputs: {len(df)} names from: {file_name}.:', className='subtitle'),
            make_dash_table(df)
        ], className='fund_filter')

        return html.Div([calc_div, df_div])
    
    except Exception as e:
        logging.error(f'Error in tranche calculation: {str(e)}')
        logging.error(traceback.format_exc())
        return html.Div([
            html.Br(),
            html.H6(f'Error during calculation:', style={'color': 'red'}, className='subtitle'),
            html.P(f'{str(e)}', style={'color': 'red'}),
            html.P('Check logs for details', style={'color': 'orange'})
        ], className='fund_filter')
