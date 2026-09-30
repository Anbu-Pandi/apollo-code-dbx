from dash_reports.risk_data_center.data.metrics import *
from data_access.dbtables.risk_pnl_data.pnl_risk_enhanced import get_risk_data, get_spac_betas
from data_access.beta_attribution.security_ts_hist import get_benchmark_beta_cdxhy

date_col = 'asof_date'

label_to_func_map = {'Net & Gross Exposure': cal_net_gross_exposure, 'Modified Duration': cal_mod_dur,
                     'Spread Duration': cal_spread_dur, 'Theta Yield': cal_theta_yield,
                     'Stress Scenarios': cal_stress_scenario, 'Carry Yield': cal_carry_yield,
                     'Unlevered YTW': cal_unlev_ytw, 'Beta Contribution': get_beta_ts}

grpby_to_agg_func_map = {'fund_entity_code': 'sum', 'fo_strategy': 'sum',
                         'fo_strategy_broad_category': 'sum', 'strategy': 'sum',
                         'issuer_ult': 'avg'}

label_to_chart_config = {'Net & Gross Exposure': {'x_axis': 'asof_date',
                                                  'y_axis': {'primary_axis_col': ['Long % NAV',
                                                                                  'Short % NAV', 'Net % NAV',
                                                                                  'Gross % NAV'],
                                                             'secondary_axis_col': ['Net MVE', 'Gross MVE']}},
                         'Modified Duration': {'x_axis': 'asof_date',
                                               'y_axis': {'primary_axis_col': ['mod_dur'],
                                                          'secondary_axis_col': None}},
                         'Spread Duration': {'x_axis': 'asof_date',
                                             'y_axis': {'primary_axis_col': ['spd_dur'],
                                                        'secondary_axis_col': None}},
                         'Theta Yield': {'x_axis': 'asof_date',
                                         'y_axis': {'primary_axis_col': ['annual_theta_yield'],
                                                    'secondary_axis_col': None}},
                         'Stress Scenarios': {'x_axis': 'asof_date',
                                              'y_axis': {'primary_axis_col': ['Bull 10.0%', 'Correction (10.0%)',
                                                                              'Recession (35.0%)', 'Fed Severe (50%)'],
                                                         'secondary_axis_col': None}},
                         'Carry Yield': {'x_axis': 'asof_date',
                                         'y_axis': {'primary_axis_col': ['annual_carry_yield'],
                                                    'secondary_axis_col': None}},
                         'Unlevered YTW': {'x_axis': 'asof_date',
                                           'y_axis': {'primary_axis_col': ['unlev_ytw'],
                                                      'secondary_axis_col': None}},
                         'Beta Contribution': {'x_axis': 'asof_date',
                                               'y_axis': {'primary_axis_col': ['HY', 'LL', 'SPX', 'Total Beta to HY'],
                                                          'secondary_axis_col': ['IR']}},
                         'Benchmark Beta to CDXHY': {'x_axis': 'asof_date',
                                                     'y_axis': {
                                                         'primary_axis_col': ['SPY US EQUITY', 'iboxx_usloan_trs'],
                                                         'secondary_axis_col': ['tsy_10y']}}
                         }


def get_risk_ts(start_date, end_date, fund, risk_metric, grp_str='fund_entity_code'):
    grp_by = []
    if isinstance(grp_str, list):
        grp_by = grp_by.append(date_col)
    else:
        grp_by = [date_col, grp_str]

    df = get_risk_data(start_date, end_date, fund)

    if df.empty:
        return 'Data not available for selected dates', None

    func_name = label_to_func_map[risk_metric]
    data = func_name(df, grp_by)

    if grp_str == 'fund_entity_code':
        fig = create_line_chart(data, grp_str, label_to_chart_config[risk_metric]['x_axis'],
                                label_to_chart_config[risk_metric]['y_axis']['primary_axis_col'],
                                label_to_chart_config[risk_metric]['y_axis']['secondary_axis_col'])
    else:
        fig = None

    return data, fig


def get_yaxis_range(df, cols):
    y_axis_min = df[cols].min()
    if isinstance(y_axis_min, pd.Series):
        y_axis_min = y_axis_min.min()

    y_axis_max = df[cols].max()
    if isinstance(y_axis_max, pd.Series):
        y_axis_max = y_axis_max.max()

    return y_axis_min, y_axis_max


def create_line_chart(df, grp_by, x_axis_col, y_axis_col, secondary_y_axis_col=None, title=None):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    groups = df.groupby(by=grp_by)

    fig = make_subplots(specs=[[{"secondary_y": True}]])
    for group, grp_df in groups:
        if secondary_y_axis_col is not None:
            for col in secondary_y_axis_col:
                fig.add_trace(
                    go.Scatter(x=grp_df[x_axis_col], y=grp_df[col], name=group + '_' + col, visible=True,
                               showlegend=True),
                    secondary_y=True)

        for col in y_axis_col:
            fig.add_trace(go.Scatter(x=grp_df[x_axis_col], y=grp_df[col], name=group + '_' + col), secondary_y=False)

            if secondary_y_axis_col is None:
                fig.add_trace(
                    go.Scatter(x=grp_df[x_axis_col], y=grp_df[col], name=group + '_' + col, visible=False,
                               showlegend=False),
                    secondary_y=True)

    y_axis_min, y_axis_max = get_yaxis_range(df, y_axis_col)

    if secondary_y_axis_col is None:
        secondary_y_axis_min = y_axis_min
        secondary_y_axis_max = y_axis_max
    else:
        secondary_y_axis_min, secondary_y_axis_max = get_yaxis_range(df, secondary_y_axis_col)

    fig.update_layout({'plot_bgcolor': 'rgba(0, 0, 0, 0)'},
                      width=1100,
                      height=500,
                      yaxis=dict(range=[y_axis_min, y_axis_max], showgrid=True),
                      xaxis=dict(tickformat="%e %b %Y"),
                      yaxis2=dict(range=[secondary_y_axis_min, secondary_y_axis_max], showgrid=True),
                      showlegend=True
                      )
    return fig


def get_bnchmrk_beta_cdxhy(start_date, end_date, risk_metric):
    data = get_benchmark_beta_cdxhy(start_date, end_date)
    return data


if __name__ == '__main__':
    from apo_config.config import config

    config.init('prod')

    start_date = '2021-01-05'
    end_date = '2021-08-08'
    df = get_risk_data(start_date, end_date, 'CSTRAT')

    # carry_df = cal_carry_yield(df, grp_by=['asof_date', 'fund_entity_code'])
    # unlev_ytw = cal_unlev_ytw(df, grp_by=['asof_date', 'fund_entity_code'])
    # net_gross_exp = cal_net_gross_exposure(df, grp_by=['asof_date', 'fund_entity_code'])
    # mod_dur = cal_mod_dur(df, grp_by=['asof_date', 'fund_entity_code'])
    # spd_dur = cal_spread_dur(df, grp_by=['asof_date', 'fund_entity_code'])
    # theta_yield = cal_theta_yield(df, grp_by=['asof_date', 'fund_entity_code'])
    # stress = cal_stress_scenario(df, grp_by=['asof_date', 'fund_entity_code'])

    risk_metric = 'Benchmark Beta to CDXHY'
    grpby_data = 'fund_entity_code'

    mod_dur = get_risk_ts(start_date, end_date, 'CSTRAT', risk_metric, grpby_data)
    data, fig = get_bnchmrk_beta_cdxhy(start_date, end_date, risk_metric)

    import plotly.io as pio

    pio.renderers.default = "browser"

    pass
