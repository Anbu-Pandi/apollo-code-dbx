from data_access.dbtables.market_data.queries import curve_jpm_queries

if __name__ == '__main__':
    import pandas as pd
    from common.utils.logutil import setuplogger
    from apo_config.config import config

    setuplogger()
    config.init('localdev')

    df = pd.DataFrame({'curve_name': 'usd 3m libor', 'currency': 'USD', 'tenor': '3M', 'daycount': 'ACT/360',
                       'compounding_frequency': '2', 'is_default': 1}, index=[0])
    df = pd.read_clipboard()
    # curves.update_curves_by_curve_name(df)
    curve_jpm_queries.update_curve_jpm_queries_by_curve_name(df)
