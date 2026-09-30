import logging

import dash_html_components as html
from dash_reports.report_common.pages.pivot_page_helpers import create_pivot_controls
from dash_reports.report_common.utils.components import make_dash_table
from dash_reports.report_common.utils import dfhelpers

from ...report_common.data.data_model import datastore
from ..data.bonds import ICE_INDICES
from ..data.returns_beta_calc.index_ts import BOND_REF_INDEX, LOAN_REF_INDEX
from .index import get_debt_index_page, get_sec_unsec_bonds_page, get_sec_unsec_issuers_page
from dash_reports.report_common.utils import dfhelpers


def create_result_layout(page, dt, issuer, industry, instrument_id):
    logging.info(f"debt view getting results for page: {page} dt: {dt}")

    if page == 'movers':
        view_config = get_debt_index_page()
    elif page == 'secured-unsecured-bonds':
        view_config = get_sec_unsec_bonds_page()
    elif page == 'secured-unsecured-issuers':
        view_config = get_sec_unsec_issuers_page()

    show_value_columns = view_config.get('values', None)
    show_col_columns = view_config.get('cols', None)
    show_row_columns = view_config.get('rows', None)
    fixed_cols = view_config.get('fixed_cols', None)
    filter_cols = view_config.get('filter_cols', None)
    filter_vals = view_config.get('filter_vals', None)

    show_value_agg_func = {x: ['mean'] for x in show_value_columns}

    df = datastore.get_issuer_debt_df(dt).copy()
    if issuer:
        df = df[df['ticker'].isin(dfhelpers.listify(issuer))]
    if industry:
        df = df[df['narrow_industry'].isin(dfhelpers.listify(industry))]
    if instrument_id:
        df = df[df['instrument_id'].isin(dfhelpers.listify(instrument_id))]
    df.columns = df.columns.map(dfhelpers.ui_column_name)
    df = df.rename(columns={'Oas Pctile': 'Oas 1y %-ile',
                            'Oas Min': 'Oas 1y Min',
                            'Oas Max': 'Oas 1y Max',
                            'Secured Curve Spread': 'Secured Curve',
                            'Unsecured Curve Spread': 'Unsecured Curve',
                            'Curve Spread': 'Curve',
                            'Other Curve Spread': 'Other Curve',
                            })

    index_df = df[[x for x in df.columns if x.startswith('Index ')]].drop_duplicates().dropna()
    num_cols = [x for x in index_df.columns if x != 'Index Ticker']
    index_df[num_cols] = index_df[num_cols] * 100
    index_df = index_df.replace({BOND_REF_INDEX: 'CDX HY', LOAN_REF_INDEX: 'SP LL'})
    index_html = html.Div([
        html.H6('Index Returns', className='subtitle'),
        make_dash_table(index_df)], className='fund_filter', style=dict(width='35%'))

    index_list = ",".join(ICE_INDICES + ['SPBDAL'])
    pivot_html = create_pivot_controls(False,
                                       f"Index ({index_list}) Movers for: {dt}",
                                       lambda: df,
                                       rows=show_row_columns,
                                       columns=show_col_columns,
                                       values=[x for x in show_value_columns if x in df.columns],
                                       aggfunc={k: v for k, v in show_value_agg_func.items() if k in df.columns},
                                       aggfunc_default='mean',
                                       page_size=10,
                                       fixed_columns=fixed_cols,
                                       filtercolumns=filter_cols,
                                       filtervalues=filter_vals,
                                       before_pivot_div=index_html)

    return html.Div([pivot_html], style=dict(width='100%'))
