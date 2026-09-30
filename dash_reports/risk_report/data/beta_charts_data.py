import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from dash_reports.report_common.data.data_model import datastore
from dash_reports.report_common.data.config import *
from common.utils import dtutil


def data_beta_components_charts():
    df = datastore.get_risk_series()

    df_rqrd = df[['As Of Date', 'Fund Entity Code', 'Reference Index', 'Beta Contribution', 'IR Beta Contrib']]
    df_rqrd = df_rqrd.loc[df_rqrd['Reference Index'].isin(['CDX_HY', 'LOAN_TRS', 'SPY Equity'])]
    df_rqrd['As Of Date'] = pd.to_datetime(df_rqrd['As Of Date'])
    df_rqrd = df_rqrd.set_index(['As Of Date', 'Fund Entity Code'])

    ref_df = df_rqrd.loc[:, ['Reference Index', 'Beta Contribution']]
    ref_df = ref_df.pivot_table(index=['As Of Date', 'Fund Entity Code'], columns=['Reference Index'],
                                values=['Beta Contribution'], aggfunc={'Beta Contribution': np.sum})
    ref_df.columns = ref_df.columns.droplevel(0)

    ir_df = df_rqrd.pivot_table(index=['As Of Date', 'Fund Entity Code'], values=['IR Beta Contrib'],
                                aggfunc={'IR Beta Contrib': np.sum})

    colsname = {'CDX_HY': 'HY', 'LOAN_TRS': 'LL', 'SPY Equity': 'SPX', 'IR Beta Contrib': 'IR'}

    master_df = pd.concat([ref_df, ir_df], axis=1, sort=False)
    master_df = master_df.rename(columns=colsname)
    master_df = master_df.fillna(0)
    master_df['Total Beta to HY'] = master_df['HY'] + master_df['LL'] + master_df['SPX'] + master_df['IR']
    master_df = master_df.mul(100)
    master_df = master_df.reset_index().set_index('As Of Date')

    return master_df


def generate_beta_charts(fundsList, color_code):
    df = data_beta_components_charts()
    beta_charts = {}
    for fund in fundsList:
        temp_df = df.loc[df['Fund Entity Code'] == fund]
        if len(temp_df) > 0:
            temp_df = temp_df.drop('Fund Entity Code', axis=1)
            filter_dt = pd.to_datetime(dtutil.dateadd(temp_df.index.max(), '-30b'))
            filter_dt_mask = temp_df.index >= filter_dt
            temp_df.loc[filter_dt_mask, 'Current Beta'] = temp_df.loc[temp_df.index.max()]['Total Beta to HY']
            fig = make_subplots(specs=[[{"secondary_y": True}]])
            for col in temp_df.columns:
                if col == 'Current Beta':
                    fig.add_trace(
                        go.Scatter(x=temp_df.index, y=temp_df[col].values, name=col,
                                   line=dict(color=color_code[col], width=2, dash='dot')), secondary_y=True)
                else:
                    fig.add_trace(
                        go.Scatter(x=temp_df.index, y=temp_df[col].values, name=col,
                                   line=dict(color=color_code[col], width=2)))
            # fig = px.line(temp_df, x=temp_df.index, y=['HY', 'LL', 'SPX', 'IR', 'Total Beta to HY'])
            fig.update_layout({'plot_bgcolor': 'rgba(0, 0, 0, 0)'},
                              width=1200,
                              height=500,
                              yaxis=dict(range=[temp_df.min().min() - 20, temp_df.max().max() + 20], dtick=10,
                                         showgrid=True),
                              xaxis=dict(tickformat="%e %b %Y"),
                              yaxis2=dict(range=[temp_df.min().min() - 20, temp_df.max().max() + 20], dtick=10,
                                          overlaying='y',
                                          side='right')
                              )

            beta_charts[fund] = {'key': fund, 'chart': fig}
            del temp_df

    return beta_charts


def data_beta_chart(fundsList):
    df = datastore.get_risk_series()
    df = df.loc[df['Fund Entity Code'].isin(fundsList)]
    df['Total Beta Contr'] = df['Beta Contribution'] + df['IR Beta Contrib']
    df_pivot = df.pivot_table(index=['As Of Date'], columns=['Fund Entity Code'],
                              values=['Total Beta Contr'], aggfunc={'Total Beta Contr': np.sum})

    master_df = df_pivot.fillna(0)
    master_df = master_df.mul(100)

    return master_df


def generate_flagship_beta_chart(fundsList):
    df = data_beta_chart(fundsList)
    df.columns = df.columns.droplevel(0)
    # fig = px.line(df, x=df.index, y=df.columns)
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    for fund in fundsList:
        fig.add_trace(go.Scatter(x=df.index, y=df[fund], name=fund), secondary_y=False)

    fig.add_trace(go.Scatter(x=df.index, y=df[fundsList[0]], name=fundsList[0], visible=False, showlegend=False),
                  secondary_y=True)

    fig.update_layout({'plot_bgcolor': 'rgba(0, 0, 0, 0)'},
                      width=1100,
                      height=500,
                      yaxis=dict(range=[df.min().min() - 20, df.max().max() + 20], dtick=10, showgrid=True),
                      xaxis=dict(tickformat="%e %b %Y"),
                      yaxis2=dict(range=[df.min().min() - 20, df.max().max() + 20], dtick=10, showgrid=True),
                      showlegend=True
                      )
    return fig


if __name__ == '__main__':
    datastore.init()
    a = data_beta_components_charts()
    # a = generate_beta_charts(['CSTRAT', 'TRF'], beta_chart_color)
