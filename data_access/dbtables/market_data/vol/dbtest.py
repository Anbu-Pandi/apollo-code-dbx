from data_access.dbtables.market_data.queries import irswaption_vol_curve_gs_queries

if __name__ == '__main__':
    import pandas as pd
    from common.utils.logutil import setuplogger
    from apo_config.config import config

    setuplogger()
    config.init('localdev')

    df = pd.read_clipboard()
    # irswaps.update_irswaps_by_swap_name(df)
    irswaption_vol_curve_gs_queries.update_irswaption_vol_curve_gs_queries_by_swaption(df, overwrite=True)
