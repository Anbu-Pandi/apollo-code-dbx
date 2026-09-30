import logging
import dash_reports.macro_data.pages.hy as hy
import dash_reports.macro_data.pages.ig as ig
import dash_reports.macro_data.pages.em as em
import dash_reports.macro_data.pages.xover as xover
import dash_reports.macro_data.pages.europe as europe
import dash_reports.macro_data.pages.finsen as finsen
import dash_reports.macro_data.pages.finsub as finsub
import dash_reports.macro_data.pages.japan as japan


def create_index_tab(asof_date, df, otr_hist_df, cdx_ex_default_df, index, title):
    if index == 'HY':
        layout = hy.create_hy_layout(asof_date, df, otr_hist_df, cdx_ex_default_df, index, title)
    elif index == 'IG':
        layout = ig.create_ig_layout(asof_date, df, otr_hist_df, cdx_ex_default_df, index, title)
    elif index == 'EM':
        layout = em.create_em_layout(asof_date, df, otr_hist_df, cdx_ex_default_df, index, title)
    elif index == 'XO':
        layout = xover.create_xover_layout(asof_date, df, otr_hist_df, cdx_ex_default_df, index, title)
    elif index == 'MN':
        layout = europe.create_europe_layout(asof_date, df, otr_hist_df, cdx_ex_default_df, index, title)
    elif index == 'FN':
        layout = finsen.create_finsen_layout(asof_date, df, otr_hist_df, cdx_ex_default_df, index, title)
    elif index == 'FS':
        layout = finsub.create_finsub_layout(asof_date, df, otr_hist_df, cdx_ex_default_df, index, title)
    elif index == 'JP':
        layout = japan.create_japan_layout(asof_date, df, otr_hist_df, cdx_ex_default_df, index, title)
    else:
        layout = []

    return layout
