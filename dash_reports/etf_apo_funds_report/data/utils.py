import pandas as pd
import numpy as np
import logging
import plotly.express as px
from plotly.subplots import make_subplots
import plotly.graph_objects as go
from data_access.dbtables import ETF_APP
from data_access.dbtables.risk_pnl_data.pnl_risk_enhanced import get_risk_data
from common.utils.sqlutil import readdfsql
from data_access.dbtables import MARKIT_BOND_PRICING_APP, ICE_INDEX_APP
from common.utils.sqlutil import sql_replace_escape_char

rating_order = {'AAA': 1, 'AA+': 2, 'AA': 3, 'AA-': 4, 'A+': 5, 'A': 6, 'A-': 7, 'BBB+': 8, 'BBB': 9, 'BBB-': 10,
                'BB+': 11, 'BB': 12, 'BB-': 13, 'B+': 14, 'B': 15, 'B-': 16, 'CCC+': 17, 'CCC': 18, 'CCC-': 19,
                'CC': 20, 'C': 21, 'D': 22, 'NR': 23, 'NA': 24
                }


def tosqllist(s, isstr=True):
    s = tolist(s) or []
    if len(s) > 0:
        if isstr:
            s = [f"'{x}'" for x in s]
        else:
            s = [f"{x}" for x in s]
        s = "(" + ",".join(s) + ")"
    else:
        s = "(NULL)"
    return s


def tolist(s):
    if s is None:
        return None
    if isinstance(s, list):
        return s
    return [str(s)]


def rename_column(x, preText):
    """This function prepends text, x is list of columns"""
    colDict = {}
    for x in x:
        colDict[x] = preText + '_' + x
    return colDict


def combine_data(df1, df2, left_df_join_key, right_df_join_key):
    df_common_1 = merge_Df(df1, df2, left_df_join_key['identifier_1'], right_df_join_key['identifier_1'])
    df_common_2 = merge_Df(df1, df2, left_df_join_key['identifier_2'], right_df_join_key['identifier_2'])

    common_Df = create_master_Df(df_common_2, df_common_1, left_df_join_key['identifier_1'],
                                 right_df_join_key['identifier_1'])

    df_uncommon_1 = df1.loc[
        ~(df1.loc[:, left_df_join_key['identifier_1']].isin(common_Df.loc[:, left_df_join_key['identifier_1']]))]
    df_uncommon_2 = df1.loc[
        ~(df1.loc[:, left_df_join_key['identifier_1']].isin(common_Df.loc[:, left_df_join_key['identifier_1']]))]

    uncommmon_Df = create_master_Df(df_uncommon_1, df_uncommon_2, left_df_join_key['identifier_1'],
                                    left_df_join_key['identifier_1'])

    combined_Df = common_Df.append(uncommmon_Df, sort=True)

    return combined_Df


def merge_Df(df1, df2, left_on_join, right_on_join, joinType='inner'):
    """This function combines two dataframe, by removing blank values from left_on_join field.
        This will ensure no blank values are matched in df1 and df2
        params:
        df1 = dataframe()
        df2 = dataframe()
        left_on_join = string, df1 column on which join to perform.
        right_on_join = string, df2 column on which join to perform
        """
    merge_Df = df1[~(df1.loc[:, left_on_join].isnull())].merge(df2, left_on=[left_on_join],
                                                               right_on=[right_on_join], how=joinType)
    return merge_Df


def create_master_Df(df1, df2, df1_field, df2_field):
    """This function combines two dataframe, by removing common data from df1.
    params:
    df1 = dataframe()
    df2 = dataframe()
    df1_field = string, columns values to look into df2 dataframe.
    df2_field = string, column values that contains common values with df1
    """
    df1 = df1.loc[
        ~(df1.loc[:, df1_field].isin(df2.loc[:, df2_field]))]
    df_Master = pd.concat([df1, df2])
    return df_Master


def chunks(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def get_snp_meta_data(identifiers):
    generator = chunks(identifiers, 1000)
    snp_meta_data_df = pd.DataFrame()
    for i in generator:
        query = f"""
        SET NOCOUNT ON
        If Object_id('tempdb.dbo.#identifiers') IS NOT NULL
        drop table #identifiers
    
    SELECT si.securityId,cc.companyname,cc.companyid,b.securityname, si.identifiervalue,si.identifiertypeid
             INTO #identifiers
              from ciqFoundationSecurity b 
              join ciqsecurityidentifier si on si.SECURITYID = b.SECURITYID
              and si.activeflag = 1
              and si.identifiertypeid in (8334, 20082, 8333, 8335)
              join ciqcompany cc on cc.companyid = b.companyid
              JOIN ciqCrossRefIdentifierType c ON si.identifierTypeId = c.identifierTypeId
              WHERE 1=1  
              and identifierValue IN {tosqllist(i)}
           AND c.identifierTypeName in ( 'ISIN', 'LoanX Number', 'CUSIP 9', 'SEDOL' )

            ;
        
    
    ;WITH CTE_identifiers_Union
    As
    (
             SELECT 
                    a.identifierValue, 
                    c.identifierTypeName, 
                    b.CompanyId,
                    b.CompanyName, 
                    b.securityId, 
                    b.SecurityName
                FROM ciqSecurityidentifier a
                    JOIN #identifiers b ON a.securityId = b.securityId
                    JOIN ciqCrossRefIdentifierType c ON c.identifierTypeId = a.identifierTypeId
    
                    UNION 
    
                    SELECT
                    a.identifierValue,
                    b.identifierTypeName,
                    d.CompanyId,
                    e.CompanyName,
                    c.securityId,
                    d.SecurityName
                FROM ciqTradingItemIdentifier a
                    JOIN ciqCrossRefIdentifierType b ON a.identifierTypeId = b.identifierTypeId
                    JOIN ciqFoundationTradingItem c ON c.tradingItemId = a.tradingItemId
                    JOIN ciqFoundationSecurity d ON c.securityId = d.securityId
                    JOIN #identifiers e ON e.securityId = c.securityId
    
    ),
    industryByCompanyId AS (
            SELECT cc.companyId, cst.subTypeValue as industry
            FROM dbo.ciqCompanyIndustryTree ccit 
                JOIN dbo.ciqCompany cc ON ccit.companyId = cc.companyId 
                JOIN #identifiers on cc.companyId = #identifiers.companyId
                LEFT JOIN dbo.ciqSubType cst ON cst.subTypeId = ccit.subTypeId
            WHERE
                ccit.primaryFlag = 1
                and cst.childLevel = 4
        ),
    identifiersPivot AS(
            SELECT *
            FROM
                (
                    SELECT *
                    FROM CTE_identifiers_Union
                ) t
            pivot(
                min(identifierValue)
            FOR
            identifierTypeName
            IN(
                [ISIN],
                [LoanX Number],
                [CUSIP 9],
                [Bloomberg Unique ID],
                [Bloomberg Share Class BBGID],
                [Ticker + Exchange],
                [Bloomberg Global ID(BBGID) + Exchange],
                [Bloomberg Ticker + Exchange],
                [Bloomberg Security ID + Exchange],
                [Bloomberg Advanced Ticker + Exchange]
                )
            ) pivot_table 
            ),
            ratings AS
            (
             SELECT
                a.CUSIP,
                a.ISIN,
                c.DebtTypeCode,
                c.description,
                a.ratingTypeCode,
                a.ratingSymbol,
                a.ratingDate, ROW_NUMBER() OVER(partition by a.ISIN order by a.ratingDate ) RW
            FROM spRatingData a
                JOIN spInstrumentLevelData b ON a.instrumentSymbolValue = b.instrumentSymbolValue
                JOIN spRatingDebtType c ON b.datavalue = c.DebtTypeCode
                JOIN identifiersPivot d ON d.ISIN = a.ISIN
                )
         SELECT distinct CompanyName as company_name, 
                            SecurityName as security_name, 
                            a.ISIN as isin, 
                            a.[CUSIP 9] as cusip, 
                            a.[LoanX Number] as loanx_id,
                            ratingSymbol as rating, 
                            industry
        FROM identifiersPivot a
        JOIN ratings C on C.isin = a.isin 
        LEFT JOIN industryByCompanyId d ON a.companyId = d.CompanyId
        where rw = 1 
    """

        temp_df = readdfsql(query, app='spglobal')
        snp_meta_data_df = snp_meta_data_df.append(temp_df)
        del temp_df

    snp_meta_data_df = snp_meta_data_df.rename(columns=rename_column(snp_meta_data_df.columns, 'SnP'))
    return snp_meta_data_df


def get_etf_data(start_date, end_date, benchmark):
    start_dt, end_dt = _compare_dates(start_date, end_date)

    query = f"Select j1.asof_date," \
            f"j1.composite_ticker," \
            f"j1.constituent_ticker," \
            f"j1.constituent_name," \
            f"j1.cusip," \
            f"j1.isin," \
            f"j1.weight," \
            f"j1.market_value," \
            f"j1.shares_held " \
            f"from dbo.etf_constituents j1 " \
            f"join (select t1.asof_date, max(t1.load_date) load_date from dbo.etf_constituents t1 " \
            f"where t1.composite_ticker in {tosqllist(benchmark)} group by t1.asof_date) j2 " \
            f"on j1.asof_date = j2.asof_date and j1.load_date = j2.load_date " \
            f"where j1.asof_date between '{start_dt}' and '{end_dt}' and j1.composite_ticker in {tosqllist(benchmark)}"

    df = readdfsql(query, app=ETF_APP)

    df['dirty_price'] = df['market_value'].div(df['shares_held'])
    df['%_NAV'] = abs(df['weight']) * 100
    df = df.rename(columns=rename_column(df.columns, 'ETF'))

    df['is_etf'] = 1

    return df


def get_markit_bonds_data(asofdate):
    query = f"Select [asof_date]," \
            f"[isin]," \
            f"[cusip]," \
            f"[ticker]," \
            f"[short_name_of_issuer]," \
            f"[currency]," \
            f"[classification]," \
            f"[region]," \
            f"[tier]," \
            f"[mid_price] " \
            f"from [dbo].[markit_pricing_data] " \
            f"where [Date] = '{asofdate}'"

    df = readdfsql(sql_replace_escape_char(query), app=MARKIT_BOND_PRICING_APP)
    df = df.rename(columns=rename_column(df.columns, 'MKP'))

    return df


def get_ice_metadata(asofdate):
    query = f"Select distinct [asof_date]," \
            f"[cusip]," \
            f"[isin_number]," \
            f"[rating]," \
            f"[industry_lvl4_desc] " \
            f" from [dbo].[ice_index_all]" \
            f" where [asof_date] = '{asofdate}'" \
            f" and lower(frequency) = 'daily'"

    df = readdfsql(query, app=ICE_INDEX_APP)
    df = df.rename(columns=rename_column(df.columns, 'ICE'))

    return df


def get_ice_data(start_date, end_date, benchmark):
    start_dt, end_dt = _compare_dates(start_date, end_date)

    query = f"select asof_date, " \
            f"ice_index_ticker as index_ticker, " \
            f"cusip, " \
            f"isin_number, " \
            f"rating, " \
            f"industry_lvl4_desc, " \
            f"percent_mkt_value, " \
            f"full_market_value_loc as full_market_value," \
            f"price " \
            f"from dbo.ice_index_all " \
            f"where asof_date between '{start_dt}' and '{end_dt}' " \
            f"and ice_index_ticker in {tosqllist(benchmark)} " \
            f"and lower(frequency) = 'daily'"

    df = readdfsql(query, app=ICE_INDEX_APP)
    df = df.rename(columns=rename_column(df.columns, 'ICE'))

    df['is_ice'] = 1

    return df


def _compare_dates(start_date, end_date):
    if start_date > end_date:
        start_dt = end_date
        end_dt = start_date
    else:
        start_dt = start_date
        end_dt = end_date

    return start_dt, end_dt


def get_apo_risk_data(start_date, end_date, fund='CSTRAT'):
    df = get_risk_data(start_date, end_date, fund)
    df = df.pivot_table(
        index=['asof_date', 'fund_entity_code', 'security', 'issuer_ult', 'cusip', 'isin', 'security_type',
               'risk_direction'],
        values=['quantity', 'price_local', 'nav_bom_usd', 'mv_usd', 'mv_equivalent_usd'],
        aggfunc={'quantity': 'sum', 'price_local': 'mean', 'nav_bom_usd': 'mean', 'mv_usd': 'sum',
                 'mv_equivalent_usd': 'sum'}).reset_index()
    if df.empty:
        logging.info(f'No risk data Fetched for start date: {start_date} and end date: {end_date} and fund: {fund}')

    df = df[~df['security_type'].isin(['Cash'])]

    df['%_NAV_net'] = df['mv_equivalent_usd'].div(df['nav_bom_usd']) * 100
    df['%_NAV_gross'] = abs(df['mv_equivalent_usd']).div(df['nav_bom_usd']) * 100

    df.loc[:, 'mv_usd'] = df['mv_usd'] / 1000000
    df.loc[:, 'quantity'] = df['quantity'] / 1000000

    df = df.rename(columns=rename_column(df.columns, 'APO'))

    df['is_apo'] = 1
    df['is_same_direction'] = df['APO_risk_direction'].apply(lambda x: 1 if x == 'Long' else 0)

    return df


def _check_for_nan(x):
    if np.nan in x:
        x.remove(np.nan)

    if None in x:
        x.remove(None)
    return x


def join_other_dataset(left_df, right_df, left_join_keys, right_join_keys, join_type='inner'):
    left_df_key_1 = left_join_keys['identifier_1']
    left_df_key_2 = left_join_keys['identifier_2']
    right_df_key_1 = right_join_keys['identifier_1']
    right_df_key_2 = right_join_keys['identifier_2']

    '''inner join based on two join keys'''
    df_key1_join = merge_Df(left_df, right_df, left_df_key_1, right_df_key_1, join_type)
    df_key2_join = merge_Df(left_df, right_df, left_df_key_2, right_df_key_2, join_type)

    inner_join_df = create_master_Df(df_key1_join, df_key2_join, left_df_key_1, right_df_key_1)

    left_df_key1_list = _check_for_nan(list(set(inner_join_df[left_df_key_1])))

    left_df_key2_list = _check_for_nan(list(set(inner_join_df[left_df_key_2])))

    '''Uncommon in left df'''
    uncommon_left_df = left_df.loc[
        ~(left_df[left_df_key_1].isin(left_df_key1_list) | left_df[left_df_key_2].isin(left_df_key2_list))]

    joined_df = inner_join_df.append(uncommon_left_df, sort=False)

    joined_df = joined_df.drop(columns=[right_df_key_1, right_df_key_2], axis=1)
    joined_df['SnP_rating'] = joined_df['SnP_rating'].apply(lambda x: 'NA' if pd.isnull(x) else x)
    joined_df['SnP_industry'] = joined_df['SnP_industry'].apply(lambda x: 'NA' if pd.isnull(x) else x)

    return joined_df


def join_benchmark_apo_df(benchmark_df, apo_df, benchmark_identifiers, apo_identifiers, join_type='inner'):
    benchmark_df_key_1 = benchmark_identifiers['identifier_1']
    benchmark_df_key_2 = benchmark_identifiers['identifier_2']
    apo_df_key_1 = apo_identifiers['identifier_1']
    apo_df_key_2 = apo_identifiers['identifier_2']

    '''inner join based on two join keys'''
    df_key1_join = merge_Df(benchmark_df, apo_df, benchmark_df_key_1, apo_df_key_1, join_type)
    df_key2_join = merge_Df(benchmark_df, apo_df, benchmark_df_key_2, apo_df_key_2, join_type)

    inner_join_df = create_master_Df(df_key1_join, df_key2_join, benchmark_df_key_1, apo_df_key_1)

    benchmark_df_key1_list = _check_for_nan(list(set(inner_join_df[benchmark_df_key_1])))
    benchmark_df_key2_list = _check_for_nan(list(set(inner_join_df[benchmark_df_key_2])))

    apo_df_key1_list = _check_for_nan(list(set(inner_join_df[apo_df_key_1])))
    apo_df_key2_list = _check_for_nan(list(set(inner_join_df[apo_df_key_2])))

    '''non common data in both df'''
    uncommon_benchmark_df = benchmark_df.loc[
        ~(benchmark_df[benchmark_df_key_1].isin(benchmark_df_key1_list) | benchmark_df[benchmark_df_key_2].isin(
            benchmark_df_key2_list))]

    uncommon_apo_df = apo_df.loc[
        ~(apo_df[apo_df_key_1].isin(apo_df_key1_list) | apo_df[apo_df_key_2].isin(apo_df_key2_list))]

    joined_df = pd.concat([inner_join_df, uncommon_apo_df, uncommon_benchmark_df])

    '''Create one key column'''
    key_1 = benchmark_df_key_1.split('_')[1]
    key_2 = benchmark_df_key_2.split('_')[1]

    joined_df[key_1] = joined_df.apply(
        lambda x: x[apo_df_key_1] if pd.isna(x[benchmark_df_key_1]) else x[benchmark_df_key_1],
        axis=1)

    joined_df[key_2] = joined_df.apply(
        lambda x: x[apo_df_key_2] if pd.isna(x[benchmark_df_key_2]) else x[benchmark_df_key_2],
        axis=1)

    joined_df = joined_df.drop(columns=[apo_df_key_1, apo_df_key_2, benchmark_df_key_1, benchmark_df_key_2], axis=1)

    return joined_df


def get_ts_chart(df):
    color_code = {'APO % Gross NAV': '#1BAFDC', 'Current Overlap': '#888888', 'ETF % Gross NAV': '#FDAE2A',
                  'APO % Net NAV': '#A6C773', 'ICE % Gross NAV': '#FDAE2A'}

    fig = make_subplots(specs=[[{"secondary_y": True}]])
    if len(df) > 0:
        df.loc[-30:, 'Current Overlap'] = df.loc[df.index.max()]['APO % Gross NAV']
        for col in df.columns:
            if col == 'Current Overlap':
                fig.add_trace(
                    go.Scatter(x=df.index, y=df[col].values, name=col,
                               line=dict(color=color_code[col], width=2, dash='dot')), secondary_y=True)
            else:
                fig.add_trace(
                    go.Scatter(x=df.index, y=df[col].values, name=col, line=dict(color=color_code[col], width=2)))

        fig.update_layout({'plot_bgcolor': 'rgba(0, 0, 0, 0)'},
                          width=1200,
                          height=500,
                          yaxis=dict(range=[df.min().min() - 10, df.max().max() + 10], dtick=5,
                                     showgrid=True),
                          xaxis=dict(tickformat="%e %b %Y"),
                          yaxis2=dict(range=[df.min().min() - 10, df.max().max() + 10], dtick=5,
                                      overlaying='y',
                                      side='right')
                          )

    return fig


def get_histogram(df, x_axis_value, y_axis_value, title='', xaxis_title='', yaxis_title='', agg_func='sum',
                  bar_mode='group'):
    fig = px.histogram(df,
                       x=x_axis_value,
                       y=y_axis_value,
                       title=title,
                       histfunc=agg_func,
                       barmode=bar_mode
                       )

    fig.update_layout(bargap=0.3,
                      bargroupgap=0.1,
                      xaxis_tickangle=-90,
                      xaxis_title_text=xaxis_title,
                      yaxis_title_text=yaxis_title
                      # yaxis=dict(range=[df[y_axis_value].min() - 20, df[y_axis_value].max() + 20], dtick=10,
                      #            showgrid=True)
                      )

    fig.layout.plot_bgcolor = '#fff'
    fig.layout.paper_bgcolor = '#fff'

    return fig
