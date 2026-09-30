from dash_reports.go_private_form.pages.utils import *
from dash_reports.report_common.utils import dfhelpers
from dash_reports.report_common.utils.components import make_dash_table
import dash_reports.macro_data.data.price_spread_volume as data
import dash_reports.macro_data.pages.charts as charts


def create_charts(df, otr_data, ticker):
    fig_list = []
    idx_figs = charts.generate_price_spread_trnxn_chart(df,
                                                        x_axis_col='maturity_date',
                                                        price_plot_cols=['mkt_px', 'model_px', 'price_skew',
                                                                         'prev_mkt_px', 'prev_price_skew',
                                                                         'prev_model_px'],
                                                        spread_plot_cols=['model_spd_bps', 'conv_spd_bps',
                                                                          'spd_skew_bps', 'prev_spd_skew_bps',
                                                                          'prev_conv_spd_bps'],
                                                        vol_plot_cols=['daily_gross_notional',
                                                                       '5d_avg_daily_gross_notional',
                                                                       'default_probability'],
                                                        idx_name=ticker)
    idx_figs['ts_chart'] = charts.generate_sprd_vol_ts_chart(otr_data, ticker, cols_to_plot=['conv_spd_bps',
                                                                                             'spd_skew_bps',
                                                                                             'daily_gross_notional'
                                                                                             ],
                                                             title=f'{ticker}-5Y Spread & Volume TS')

    for k, v in idx_figs.items():
        fig_list.append(html.Div([dcc.Graph(figure=fig, style={'display': 'inline-block'}) for fig in v]))

    return fig_list


def get_ex_default_div(asof_date, cdx_ex_default_df, index, title):
    ex_def_df = cdx_ex_default_df[cdx_ex_default_df['idx_short_name'] == index]

    implied_spd_chart = None
    implied_spd_dash_table = None

    if not ex_def_df.empty:
        implied_spd_chart = charts.generate_cdx_ex_default_charts(ex_def_df, index)

        implied_spd_df = ex_def_df[ex_def_df['asof_date'] == asof_date]
        implied_spd_df.sort_values('apo_ex_default_ticker', inplace=True)

        implied_spd_df.columns = [dfhelpers.ui_column_name(x) for x in implied_spd_df.columns]
        implied_spd_dash_table = make_dash_table(implied_spd_df,
                                                 cell_styling={'whiteSpace': 'normal',
                                                               'height': 'auto'},
                                                 txt_col_width='150px',
                                                 num_col_width='125px',
                                                 fixed_columns=6
                                                 )

    ex_default_implied_spd_div = html.Div([dcc.Graph(figure=implied_spd_chart, style={'display': 'inline-block'},
                                                     id={'type': 'ex_default_implied_spd_chart', 'index': title}),
                                           html.Br(),
                                           html.Div(implied_spd_dash_table,
                                                    id={'type': 'ex_default_implied_spd_output', 'index': title})],
                                          style={'display': 'none' if implied_spd_chart is None else 'block'})

    return ex_default_implied_spd_div


def create_japan_layout(asof_date, df, otr_hist_data, cdx_ex_default_df, index, title):
    df_index = df[df['idx_short_name'] == index]

    price_spread_charts = create_charts(df_index, otr_hist_data, index)

    df_index = df_index[data.DISPLAY_ORDER]
    df_index.columns = [dfhelpers.ui_column_name(x) for x in df_index.columns]

    ex_default_implied_spd_div = get_ex_default_div(asof_date, cdx_ex_default_df, index, title)

    layout = [html.Br(),
              html.Div([get_section_title(title),
                        html.Br(),
                        html.Div(price_spread_charts),
                        html.Br(),
                        html.Div(make_dash_table(df_index,
                                                 cell_styling={'whiteSpace': 'normal', 'height': 'auto'},
                                                 txt_col_width='150px',
                                                 num_col_width='125px',
                                                 fixed_columns=5
                                                 ),
                                 className='list'),
                        html.Br(),
                        ex_default_implied_spd_div
                        ]
                       ),
              html.Br(),
              ]
    return layout
