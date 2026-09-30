import dash_html_components as html
from matplotlib.colors import LinearSegmentedColormap
import pandas as pd

DISPLAY_COLS = ['effective_date_time', 'apo_dv01', 'apo_spd_dur', 'apo_oas', 'apo_yield', 'qty_model_est',
                'apo_dirty_price', 'dirty_mv', 'mkt_tcost', 'apo_tcost', 'trade_count', 'apo_warf_rating',
                'portfolio_trade_index'

                ]


# 'apo_clean_price', 'clean_mv', 'dirty_mv_unrated', 'qty_capped','ticker_count', 'true_weight', 'spd_dur_contrib',
# 'oas_contrib', 'yield_contrib','warf_contrib_rating', 'warf_contrib_rated_rating', 'apo_warf_rated_rating',
# 'trade_count_unrated'

def get_stylized_df(df, title=''):
    hex_colors = ['#63BE7B', '#FFEB84', '#F8696B']
    excel_gyr_cmap = LinearSegmentedColormap.from_list('excel_gyr', hex_colors)
    df_styled = df.style.background_gradient(cmap=excel_gyr_cmap,
                                             subset=pd.IndexSlice[df.index.get_level_values(0).drop('Total'),
                                             df.columns.get_level_values(0).drop('Total')],
                                             axis=None
                                             ).format("{:,.2f}").set_table_styles([{'selector': 'th',
                                                                                    'props': [('color', 'black')
                                                                                              ]
                                                                                    }]
                                                                                  ).set_properties(
        **{'border': '0.5px solid black', 'text-align': 'center',
           'padding': '3px', 'color': 'black'}).set_caption(title)
    return df_styled


def get_heatmap_div(df, title):
    styled_df = get_stylized_df(df)

    return html.Div([html.Br(),
                     html.Div(html.H3(title, className="subtitle padded")),
                     html.Br(),
                     html.Div(html.Iframe(srcDoc=styled_df.to_html(),
                                          width='100%',
                                          height='300px',
                                          style={'border': 'none'}
                                          )),
                     html.Br()
                     ]
                    , style={
            'display': 'inline-block',
            'width': '48%',
            'padding-right': '10px'}
                    )


def get_section_title(section_title):
    return html.Div([section_title], className="sub_header")
