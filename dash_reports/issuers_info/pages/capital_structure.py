import logging
import pandas as pd
import numpy as np

from common.utils import dtutil

import dash_cytoscape as cyto
from dash import html, dcc

from dash_reports.report_common.utils import components, dfhelpers
from dash_reports.report_common.pages.link_and_headers import get_page_title
from dash_reports.report_common.elastic_search.api import default_es

from dash_reports.issuers_info.data.positions import get_positions_agg_by_instrument_id
from dash_reports.issuers_info.data.capital_structure import get_company_tree_view

from dash_reports.issuers_info.data.caching import ES_BBG_ISSUERS_INDEX, ES_BBG_BONDS_INDEX

CAP_STRUCT_TABLE_ID = 'issuer-table-id'
RELS_COS_STORE_ID = 'hidden-store-id'

DD_COMPANY_NAME_ID = 'restricted_company_dd_id'
SUBMIT_BUTTON_ID = 'restricted_submit_id'
SHOW_SECURITY_ISSUERS_CHECKLIST_ID = 'restricted_issuer_checklist_id'
RESULTS_SECTION_ID = 'restricted_section_id'
DD_SECTION_ID = 'restricted_dd_section_id'

OPT_WITH_SECURITIES = 'WITH_SECURITIES'


def get_issuer_info_header():
    return html.Div([get_page_title("Issuer Info"),
                     html.Br(), ])


def get_company_name_dropdown(selected_value=None, options=None, show_security_issuers=True):
    options = options or []
    if selected_value and options:
        df = default_es.search_exact(ES_BBG_ISSUERS_INDEX, 'id_bb_company', selected_value)
        options = [{'label': x['id_bb_global_company_name'], 'value': int(x['id_bb_company'])}
                   for _, x in df.iterrows() if pd.notnull(x['id_bb_company'])]

    company_dd_header = html.Div(id='restricted_company_name_dd_container',
                                 className='fund_filter',
                                 children=[html.Div(style=dict(display='flex'),
                                                    children=[html.Div([html.Label("{:<25}".format("Company Name:"),
                                                                                   className='subtitle')],
                                                                       style=dict(width='20%')),
                                                              html.Div([dcc.Dropdown(value=selected_value,
                                                                                     multi=False,
                                                                                     id=DD_COMPANY_NAME_ID,
                                                                                     options=options), ],
                                                                       style=dict(width='80%'))
                                                              ]),
                                           html.Div(children=[dcc.Checklist(options=[
                                               {'label': 'Issuers with Securities Only', 'value': OPT_WITH_SECURITIES}],
                                               value=[OPT_WITH_SECURITIES] if show_security_issuers else [],
                                               id=SHOW_SECURITY_ISSUERS_CHECKLIST_ID)]),
                                           html.Br(),
                                           html.Div(children=[html.Button('Go', id=SUBMIT_BUTTON_ID,
                                                                          className='submit-button')])
                                           ])

    return company_dd_header


def _get_findf_col_name(col):
    return (col, '')


def _get_findf_chg_cols(findf):
    return [x for x in findf.columns if x[1] in ['yoy', 'qoq']]


def arrange_fin_df(findf):
    findf['dataitem_value'] = pd.to_numeric(findf['dataitem_value'], errors='coerce')
    findf = pd.pivot_table(findf, values='dataitem_value',
                           index='dataitem_name',
                           columns=['period_type', 'qtr']).reset_index()
    sep = ' '
    findf = findf.sort_index(ascending=True, axis=1)
    first_cols = [_get_findf_col_name('dataitem_name')]
    findf = findf.loc[:, first_cols + [x for x in findf.columns if x not in first_cols]]
    findf[_get_findf_col_name(sep * 1)] = np.nan
    findf[_get_findf_col_name(sep * 2)] = np.nan
    findf[_get_findf_col_name(sep * 3)] = np.nan

    qtr_cols = sorted([x for x in findf.columns if x[0].startswith('Q')])
    ann_cols = sorted([x for x in findf.columns if x[0].startswith('A')])
    ltm_cols = sorted([x for x in findf.columns if x[0].startswith('LTM')])
    ytd_cols = sorted([x for x in findf.columns if x[0].startswith('YTD')])

    qtr_cols_offset = 0
    if len(qtr_cols) >= 2:
        last_qtr = qtr_cols[-1]
        secondlast_qtr = qtr_cols[-2]
        qoq_col = (f'{last_qtr[0]}_chg', f'qoq')
        qtr_cols += [qoq_col]
        findf[qoq_col] = (findf[last_qtr] - findf[secondlast_qtr])
        qtr_cols_offset += 1

    if len(qtr_cols) >= 5 + qtr_cols_offset:
        last_qtr = qtr_cols[-1 - qtr_cols_offset]
        lastyear_qtr = qtr_cols[-5 - qtr_cols_offset]
        yoy_col = (f'{last_qtr[0]}_chg', f'yoy')
        qtr_cols += [yoy_col]
        findf[yoy_col] = (findf[last_qtr] - findf[lastyear_qtr])
        qtr_cols_offset += 1

    ytd_cols_offset = 0
    if len(ytd_cols) >= 5:
        this_yr = ytd_cols[-1]
        past_yr = ytd_cols[-5]
        yoy_col = (f'{this_yr[0]}_chg', f'yoy')
        ytd_cols += [yoy_col]
        findf[yoy_col] = (findf[this_yr] - findf[past_yr])
        ytd_cols_offset += 1

    ltm_cols_offset = 0
    if len(ltm_cols) >= 5:
        this_yr = ltm_cols[-1]
        past_yr = ltm_cols[-5]
        yoy_col = (f'{this_yr[0]}_chg', f'yoy')
        ltm_cols += [yoy_col]
        findf[yoy_col] = (findf[this_yr] - findf[past_yr])
        ltm_cols_offset += 1

    additional_ytd_cols = [] if (ytd_cols_offset == 0) else [-1]
    findf = findf[first_cols + qtr_cols[-8 - qtr_cols_offset:] + [_get_findf_col_name(sep * 1)] +
                  ltm_cols[-5 - ltm_cols_offset:] + [_get_findf_col_name(sep * 2)] +
                  ann_cols + [_get_findf_col_name(sep * 3)] +
                  ([ytd_cols[-5 - ytd_cols_offset:][i] for i in ([-1 - ytd_cols_offset] + additional_ytd_cols)] \
                       if len(ytd_cols) > 0 else [])]

    return findf


def get_min_findf(findf):
    mini_items = ['IQ_REVENUE_TOTAL', 'IQ_TOTAL_REV', 'IQ_TOTAL_REVENUE', 'IQ_TOTAL_REV_1YR_ANN_GROWTH',
                  'IQ_EBITDA', 'IQ_EBITDA_MARGIN',
                  'IQ_CAPEX',
                  'IQ_EBITDA_CAPEX',
                  'IQ_CASH_OPER', 'IQ_TOTAL_CASH_OPER', 'IQ_LEVERED_FCF', 'IQ_UNLEVERED_FCF']
    mini_findf = findf[findf['formula_metric'].isin(mini_items)]
    metric_desc_map = mini_findf[['formula_metric', 'dataitem_name']].drop_duplicates() \
        .set_index('formula_metric').to_dict()['dataitem_name']

    mini_findf = arrange_fin_df(mini_findf)
    mini_findf = mini_findf.set_index(_get_findf_col_name('dataitem_name')).T
    if ((metric_desc_map.get('IQ_EBITDA', 'n/a') in mini_findf.columns) and
            (metric_desc_map.get('IQ_CAPEX', 'n/a') in mini_findf.columns)):
        mini_findf['EBITDA - Capex'] = (mini_findf[metric_desc_map.get('IQ_EBITDA', 'n/a')] -
                                        mini_findf[metric_desc_map.get('IQ_CAPEX', 'n/a')])

    mini_items_desc = [metric_desc_map[x] for x in mini_items if x in metric_desc_map] + ['EBITDA - Capex']
    mini_findf = mini_findf.T.reindex(mini_items_desc).reset_index()
    return mini_findf


def create_fin_tab_kids(findf):
    tab_kids = []
    findf = findf.sort_values(['period_end_date', 'dataitem_name'], ascending=[False, True])
    if 'qtr' not in findf.columns:
        findf['qtr'] = findf['calendar_year'].astype(str) + ' Q' + findf['calendar_quarter'].astype(str)
    findf = findf[['qtr', 'period_type', 'formula_metric', 'dataitem_name', 'dataitem_value', 'id_bb_company']]

    mini_findf = get_min_findf(findf)
    findf = arrange_fin_df(findf)
    chg_cols = _get_findf_chg_cols(findf)

    tab_kids.append(html.H6(f"Summary:", className='subtitle'))
    tab_kids.append(create_ui_table(mini_findf, chg_cols))
    tab_kids.append(html.Br())
    tab_kids.append(html.H6(f"Details:", className='subtitle'))
    tab_kids.append(create_ui_table(findf, chg_cols))

    return tab_kids


def create_fin_ui(fin_dict):
    tabs = []
    for k, v in fin_dict.items():
        fin_ui = create_fin_tab_kids(v)
        tabs.append(dcc.Tab(label=k, children=fin_ui))
    div_kids = []
    div_kids.append(html.H6("Financial Statement:", className='subtitle'))
    div_kids.append(dcc.Tabs(tabs))
    return html.Div(children=div_kids, className='fund_filter')


def create_div_kids(graph_tree_df, debt_df, show_corp_tree_df, corp_tree_df, pos_history,
                    positions_df):
    div_kids = [html.Br(),
                html.H6("Corporate Tree:", className='subtitle'),
                html.Div(children=[create_corp_tree_graph(graph_tree_df)]),
                html.Br(),
                html.H6(f"Held Positions in Last {pos_history}: ", className='subtitle'),
                html.Div(children=[create_ui_table(positions_df)]),
                html.Br(),
                html.P(children=[html.H6("Debt ", className='subtitle'),
                                 html.H6("(excludes convertible bonds):", className='subtitle',
                                         style=dict(color='Tomato'))]),
                html.Div(children=[create_ui_table(debt_df.dropna(axis=1, how='all'))]),
                html.Div(children=[
                    html.Br(),
                    html.H6("Corporate Tree:", className='subtitle'),
                    create_ui_table(corp_tree_df),
                ] if show_corp_tree_df else
                []), ]
    return div_kids


def _update_columns_from_corp_tree(corp_tree_df, df):
    if len(corp_tree_df) > 0:
        update_cols = ['level', 'obligor_bbid', 'id_bb_global_obligor_name',
                       'restriction_type', 'restriction_scope', 'restriction_color_code',
                       'restriction_start_date', 'restriction_end_date']
        corp_tree_df.set_index('id_bb_company', inplace=True)
        for col in update_cols:
            if ('id_bb_company' in df.columns) and (col in corp_tree_df.columns):
                df[col] = corp_tree_df.reindex(df['id_bb_company'])[col].values

        corp_tree_df.reset_index(inplace=True)


def _update_columns(corp_tree_df, debt_df, positions_df):
    if (len(debt_df) > 0) and (len(positions_df) > 0):
        debt_df['apo_funds'] = positions_df.groupby('instrument_id')['fund_entity_code'] \
            .apply(lambda x: ','.join(sorted(x.unique()))).reindex(debt_df['instrument_id']).values

        if len(positions_df) > 0:
            (positions_df['id_bb_company'],
             positions_df['id_bb_global_company_name'],
             positions_df['ticker']) = zip(*debt_df.drop_duplicates(subset=['instrument_id']) \
                                           .set_index('instrument_id').reindex(positions_df['instrument_id']) \
                [['id_bb_company', 'id_bb_global_company_name', 'ticker']].values)

        _update_columns_from_corp_tree(corp_tree_df, debt_df)

    if len(positions_df) > 0:
        _update_columns_from_corp_tree(corp_tree_df, positions_df)


def _reorder_columns(df):
    cols_order = ['as_of_date', 'company_corp_ticker', 'ticker',
                  'id_bb_company', 'id_bb_global_company_name', 'obligor_bbid', 'id_bb_global_obligor_name',
                  'business_structure', 'level',
                  'parent_id_bb_company', 'parent_id_bb_global_company_name',
                  'parent_obligor_id', 'parent_obligor_name',
                  'ult_parent_id_bb_company', 'ult_parent_id_bb_global_company_name',
                  'ultimate_obligor_id', 'ultimate_obligor_name',
                  'debt_type', 'instrument_id']
    df = df[[x for x in cols_order if x in df.columns] + [x for x in df.columns if not x in cols_order]]
    return df


def create_cap_struct_layout(id_bb_company, show_security_issuers=True, show_corp_tree_df=False):
    logging.info(f'creating cap structure layout for company_id: {id_bb_company}')

    div_kids = []
    company_tree_view = get_company_tree_view(id_bb_company, only_security_issuers=show_security_issuers)
    if company_tree_view is not None:
        corp_tree_df, graph_tree_df, debt_df = company_tree_view
        corp_tree_df = corp_tree_df[pd.notnull(corp_tree_df['level'])]
        rels_cos = corp_tree_df['id_bb_company'].tolist()
        rels_tickers = corp_tree_df['company_corp_ticker'].unique().tolist()

        if len(debt_df) > 0:
            sel_rows = (pd.to_numeric(debt_df['amt_outstanding'].astype(str).str.replace(',', ''), errors='coerce') \
                        .fillna(0) > 0)
            debt_df = debt_df[sel_rows]

        pos_history = '1y'
        pos_to_dt = dtutil.today(use_bday=True)
        pos_from_dt = dtutil.dateadd(pos_to_dt, f"-{pos_history}")

        pos_dfs = []
        for i, debt_type in enumerate(['Bond', 'Loan', 'CDS']):
            rels_sec = debt_df[debt_df['debt_type'] == debt_type]['instrument_id'].unique().tolist() \
                if len(debt_df) > 0 else []
            if (len(rels_sec) > 0) or (i == 0):
                pos_dfs.append(get_positions_agg_by_instrument_id(pos_from_dt, pos_to_dt, [], rels_sec, debt_type))

        positions_df = pd.concat(pos_dfs)

        _update_columns(corp_tree_df, debt_df, positions_df)
        if (len(debt_df) > 0) and ('level' in debt_df.columns):
            sel_rows = (pd.notnull(debt_df['level']))
            debt_df = debt_df[sel_rows]

        if (len(positions_df) > 0) and ('level' in positions_df.columns):
            sel_rows = (pd.notnull(positions_df['level']))
            positions_df = positions_df[sel_rows]

        positions_df = _reorder_columns(positions_df)
        debt_df = _reorder_columns(debt_df)
        corp_tree_df = _reorder_columns(corp_tree_df)

        div_kids = create_div_kids(graph_tree_df, debt_df,
                                   show_corp_tree_df, corp_tree_df,
                                   pos_history, positions_df)

        div_kids.append(dcc.Store(id=RELS_COS_STORE_ID, data=rels_cos))

    return html.Div(className='page',
                    children=[html.Div(id=CAP_STRUCT_TABLE_ID,
                                       children=[html.Div(children=div_kids, className='fund_filter')])])


def create_corp_tree_graph(df):
    if len(df) > 0:
        df = df.drop_duplicates(subset=['id_bb_company'])
        roots = [f'#{int(x)}' for x in df[df['level'] == 1]['id_bb_company'].unique().tolist()]
        roots = ','.join(roots)
        root_name = df.iloc[0]['id_bb_global_company_name']
        nodes = [{'data': {'id': int(x['id_bb_company']),
                           'label': f"{x['id_bb_global_company_name']}:{x['additional_info']}"}}
                 for _, x in df.iterrows()]
        edges = [{'data': {'source': int(x['id_bb_company']), 'target': int(x['parent_id_bb_company'])}}
                 for _, x in df.iterrows() if pd.notnull(x['parent_id_bb_company'])]
        logging.info(f'creating cytoscape with roots: {roots}, {root_name} nodes: {len(nodes)} + edges:{len(edges)}')
        nw_graph = cyto.Cytoscape(elements=nodes + edges,
                                  minZoom=0.7,
                                  maxZoom=1,
                                  layout={'name': 'breadthfirst', 'roots': roots, },
                                  style={'width': '1700px', 'height': '400px'}
                                  )
        return nw_graph
    return html.Div(html.Br())


def create_ui_table(df, light_headers=[]):
    def _ui_col(x):
        if isinstance(x, tuple):
            return tuple([dfhelpers.ui_column_name(xi) for xi in x])
        return dfhelpers.ui_column_name(x)

    df.columns = [_ui_col(x) for x in df.columns]
    light_headers = [_ui_col(x) for x in light_headers]
    return components.make_dash_table(df, page_size=10, dynamic_num_format=True, light_headers=light_headers)


def create_static_layout(bbg_id=None):
    dd_control = html.Div(id=DD_SECTION_ID, children=[get_company_name_dropdown(bbg_id), ], className='fund_filter')
    return dd_control


def create_dynamic_layout():
    search_results = html.Div(id=RESULTS_SECTION_ID, children=[])
    return html.Div(children=[search_results])


def create_layout(bbg_id=None):
    return create_static_layout(bbg_id), create_dynamic_layout()
