import calendar
from datetime import datetime

from dateutil.relativedelta import relativedelta, FR
from pandas.tseries.offsets import BDay

from common.date_utilities import DateTimeUtilities
from common.db_utilities import *
from common.utils import dtutil
from common.utils.sqlutil import sql_replace_escape_char
from data_access.beta_attribution import APP, readdfsql_schema
from data_access.beta_attribution.ice_index import TABLE_NAME as ice
from data_access.beta_attribution.security_ts_hist import TABLE_NAME as security_ts_hist
from data_access.beta_attribution.sp_loan_index import TABLE_NAME as snp_loan, APP as snp_app
from jp_data_api.jpm_data_query_helper import JPMQueryBuilder
from data_access.dbtables import ICE_INDEX_APP



def getLastFriday(asofdate):
    lastFriday = asofdate + relativedelta(weekday=FR(-1))

    return lastFriday


class index_data:
    def __init__(self, start_date, end_date):
        self.start_date = start_date
        self.end_date = end_date
        self.jpm_ret_date = self.get_jpm_ret_date(self.end_date)
        self.jpm_prev_month_end_date = dtutil.eomonth(dtutil.dateadd(self.jpm_ret_date, '-1m'))
        self.last_friday_date = self.end_date if self.end_date.day == \
                                                 calendar.monthrange(self.end_date.year, self.end_date.month)[
                                                     1] else getLastFriday(self.end_date)

        self.last_friday_date_str = self.last_friday_date.strftime("%Y-%m-%d")

        self.last_last_friday = DateTimeUtilities.getLastFriday(getLastFriday(self.last_friday_date))
        self.last_last_friday_str = self.last_last_friday.strftime("%Y-%m-%d")

    def get_mtd_data(self, benchmark):
        """
        @summary: This function gets data for index attribution.

        @params asofdate: Date for which data has to be fetched
        @type: datetime

        @params benchmark: list of benchmark codes for which data has to be fetched
        @type: str

        @return: dataframe
        """

        query = f'''
        SELECT  industry_lvl3_desc as [Industry Lvl 3 Desc], 
                CASE 
                    WHEN rating IN ('AA1', 'AA2', 'AA3') THEN 'AA' 
                    WHEN rating IN ('A1', 'A2', 'A3') THEN 'A' 
                    WHEN rating IN ('BBB1', 'BBB2', 'BBB3') THEN 'BBB' 
                    WHEN rating IN ('BB1','BB2', 'BB3') THEN 'BB' 
                    WHEN rating IN ('B1', 'B2', 'B3') THEN 'B' 
                    WHEN rating IN ('BB1', 'BB2', 'BB3') THEN 'BB' 
                    WHEN rating IN ('C', 'CC', 'CCC1', 'CCC2', 'CCC3') THEN 'CCC OR BELOW' 
                ELSE 
                    rating END AS Rating_L1, 
                nullif(SUM([percent_mkt_value_prev_mend] * [trr_pct_mtd_loc]) / SUM([percent_mkt_value_prev_mend]), 0) AS MTD_Return_Factor, 
                SUM([percent_mkt_value_prev_mend] * [trr_pct_mtd_loc]) / 100 AS MTD_Return_Contr, SUM([percent_mkt_value]) AS FactorWeight,
                nullif(SUM([percent_mkt_value] * oas) / SUM([percent_mkt_value]), 0) AS curr_Spd_Factor, COUNT(*) AS BOND_COUNT
        FROM    {ice}
        WHERE   ([ice_index_ticker] = '{benchmark}') AND (asof_date = '{self.last_friday_date_str}') AND (rating <> 'CASH') and lower(frequency) = 'daily'       
        GROUP BY industry_lvl3_desc, 
                CASE 
                    WHEN rating IN ('AA1', 'AA2', 'AA3') THEN 'AA' 
                    WHEN rating IN ('A1', 'A2', 'A3') THEN 'A' 
                    WHEN rating IN ('BBB1', 'BBB2', 'BBB3') THEN 'BBB' 
                    WHEN rating IN ('BB1', 'BB2', 'BB3') THEN 'BB'
                    WHEN rating IN ('B1', 'B2', 'B3') THEN 'B' 
                    WHEN rating IN ('BB1', 'BB2', 'BB3') THEN 'BB' 
                    WHEN rating IN ('C', 'CC', 'CCC1', 'CCC2', 'CCC3') THEN 'CCC OR BELOW' 
                ELSE 
                    rating END
        HAVING SUM([percent_mkt_value]) != 0
        ORDER BY [industry_lvl3_desc]
        '''

        mtd_rtrns = readdfsql_schema(sql_replace_escape_char(query), app=ICE_INDEX_APP)
        mtd_rtrns['Key'] = mtd_rtrns['Industry Lvl 3 Desc'] + '_' + mtd_rtrns['Rating_L1']

        query2 = f'''
        SELECT  ticker as Ticker, industry_lvl3_desc as [Industry Lvl 3 Desc],
                nullif(SUM([percent_mkt_value_prev_mend] * [trr_pct_mtd_loc]) / sum([percent_mkt_value_prev_mend]), 0) AS MTD_Return_Factor, 
                nullif(SUM([percent_mkt_value_prev_mend] * [trr_pct_mtd_loc]) / 100, 0) AS MTD_Return_Contr,
                nullif(SUM([percent_mkt_value_prev_mend] * (Price - Price / (1 + [trr_pct_mtd_loc] * 0.01))) / SUM([percent_mkt_value_prev_mend]), 0) AS MTD_Return_bondPTS_Factor, 
                nullif(SUM([percent_mkt_value] * oas) / SUM([percent_mkt_value]), 0) AS curr_Spd_Factor, SUM([percent_mkt_value]) AS FactorWeight,
                COUNT(*) AS BOND_COUNT
        FROM    {ice}
        WHERE   ([ice_index_ticker] = '{benchmark}') AND (asof_date = '{self.last_friday_date_str}') AND (rating <> 'CASH') and lower(frequency) = 'daily'
        GROUP BY ticker, industry_lvl3_desc
        HAVING SUM([percent_mkt_value]) != 0
        ORDER BY MTD_Return_Factor
        '''

        mtd_rtrns_ticker = readdfsql_schema(sql_replace_escape_char(query2), app=ICE_INDEX_APP)

        return mtd_rtrns, mtd_rtrns_ticker

    def get_excess_mtd_data(self, benchmark):
        """
        @summary: This function gets excess returns data for index attribution.

        @params asofdate: Date for which data has to be fetched
        @type: datetime

        @params benchmark: list of benchmark codes for which data has to be fetched
        @type: str

        @return: dataframe
        """

        query = f'''
        SELECT  industry_lvl3_desc as [Industry Lvl 3 Desc], 
                CASE 
                    WHEN rating IN ('AA1', 'AA2', 'AA3') THEN 'AA'
                    WHEN rating IN ('A1', 'A2', 'A3') THEN 'A' 
                    WHEN rating IN ('BBB1', 'BBB2', 'BBB3') THEN 'BBB' 
                    WHEN rating IN ('BB1', 'BB2', 'BB3') THEN 'BB' 
                    WHEN rating IN ('B1', 'B2', 'B3') THEN 'B' 
                    WHEN rating IN ('BB1', 'BB2', 'BB3') THEN 'BB' 
                    WHEN rating IN ('C', 'CC', 'CCC1', 'CCC2', 'CCC3') THEN 'CCC OR BELOW' 
                ELSE 
                    rating END AS Rating_L1, 
                nullif(SUM([percent_mkt_value_prev_mend] * [excess_return_pct_mtd]) / SUM([percent_mkt_value_prev_mend]), 0) AS MTD_ExcessReturn_Factor, 
                SUM([percent_mkt_value_prev_mend] * [excess_return_pct_mtd]) / 100 AS MTD_ExcessReturn_Contr, SUM([percent_mkt_value]) AS FactorWeight,
                nullif(SUM([percent_mkt_value] * oas) / SUM([percent_mkt_value]), 0) AS curr_Spd_Factor, 
                COUNT(*) AS BOND_COUNT
        FROM  {ice}
        WHERE ([ice_index_ticker] = '{benchmark}') AND (asof_date = '{self.last_friday_date_str}') AND (rating <> 'CASH') and lower(frequency)='daily'
        GROUP BY industry_lvl3_desc, 
                CASE 
                    WHEN rating IN ('AA1', 'AA2', 'AA3') THEN 'AA' 
                    WHEN rating IN ('A1', 'A2', 'A3') THEN 'A' 
                    WHEN rating IN ('BBB1', 'BBB2', 'BBB3') THEN 'BBB' 
                    WHEN rating IN ('BB1', 'BB2', 'BB3') THEN 'BB' 
                    WHEN rating IN ('B1', 'B2', 'B3') THEN 'B' 
                    WHEN rating IN ('BB1', 'BB2', 'BB3') THEN 'BB' 
                    WHEN rating IN ('C', 'CC', 'CCC1', 'CCC2', 'CCC3') THEN 'CCC OR BELOW' 
                ELSE 
                    rating END
        HAVING SUM([percent_mkt_value]) != 0
        ORDER BY [industry_lvl3_desc]
        '''

        df = readdfsql_schema(sql_replace_escape_char(query), app=ICE_INDEX_APP)
        df['Key'] = df['Industry Lvl 3 Desc'] + '_' + df['Rating_L1']
        df = df.set_index('Key')

        CDX_HY_df = self.get_CDXHY_level(self.jpm_ret_date, self.jpm_ret_date)

        mtd = CDX_HY_df.loc[CDX_HY_df.index.max()]['Return']

        corr_df, beta_df = self.get_beta_corr_data(benchmark)
        beta_df = beta_df.loc[:, ['Key', 'BETA']]
        beta_df = beta_df.set_index('Key')

        excess_return_df = pd.concat([df, beta_df], sort=False, axis=1)
        excess_return_df['Alpha'] = excess_return_df['MTD_ExcessReturn_Factor'] - mtd * excess_return_df['BETA']
        excess_return_df = excess_return_df.reset_index()

        return excess_return_df, CDX_HY_df

    def get_beta_corr_data(self, benchmark):
        """
        @summary: This function gets excess returns data for index attribution.

        @params asofdate: Date for which data has to be fetched
        @type: datetime

        @params benchmark: list of benchmark codes for which data has to be fetched
        @type: str

        @return: dataframe
        """

        query = f'''
        SELECT  IID.industry_lvl3_desc as [Industry Lvl 3 Desc], 
                CASE 
                    WHEN rating IN ('AA1', 'AA2', 'AA3') THEN 'AA' 
                    WHEN rating IN ('A1', 'A2', 'A3') THEN 'A' 
                    WHEN rating IN ('BBB1', 'BBB2', 'BBB3') THEN 'BBB' 
                    WHEN rating IN ('BB1', 'BB2', 'BB3') THEN 'BB' 
                    WHEN rating IN ('B1', 'B2', 'B3') THEN 'B' 
                    WHEN rating IN ('BB1', 'BB2', 'BB3') THEN 'BB' 
                    WHEN rating IN ('C', 'CC', 'CCC1', 'CCC2', 'CCC3') THEN 'CCC OR BELOW' 
                ELSE 
                    rating END AS Rating_L1, 
                nullif(SUM(IID.[percent_mkt_value_prev_mend] * STH.value) / SUM(IID.[percent_mkt_value_prev_mend]), 0) AS CORR, 
                SUM(IID.[percent_mkt_value_prev_mend] * STH.value) / 100 AS CORR_Contr, 
                SUM(IID.[percent_mkt_value]) AS FactorWeight, COUNT(*) AS BOND_COUNT
        FROM    {ice} AS IID INNER JOIN {security_ts_hist} AS STH 
                ON IID.cusip = STH.sec_id
        WHERE   (IID.[ice_index_ticker] = '{benchmark}') AND 
                (IID.asof_date = '{self.last_last_friday_str}') AND 
                (IID.rating <> 'CASH') AND (lower(IID.frequency='daily')) AND 
                (STH.asof_date = '{self.last_last_friday_str}') AND 
                (STH.source = 'ICE') AND (STH.attribute = 'Correlation_WITH_ALPHA_12M_CDXHY')
        GROUP BY IID.[industry_lvl3_desc], 
                CASE 
                    WHEN rating IN ('AA1', 'AA2', 'AA3') THEN 'AA' 
                    WHEN rating IN ('A1', 'A2', 'A3') THEN 'A' 
                    WHEN rating IN ('BBB1', 'BBB2', 'BBB3') THEN 'BBB' 
                    WHEN rating IN ('BB1', 'BB2', 'BB3') THEN 'BB' 
                    WHEN rating IN ('B1', 'B2', 'B3') THEN 'B' 
                    WHEN rating IN ('BB1', 'BB2', 'BB3') THEN 'BB' 
                    WHEN rating IN ('C', 'CC', 'CCC1', 'CCC2', 'CCC3') THEN 'CCC OR BELOW' 
                ELSE 
                    rating END
        ORDER BY Rating_L1, IID.[industry_lvl3_desc]
        '''

        corr_df = readdfsql_schema(sql_replace_escape_char(query), app=ICE_INDEX_APP)
        corr_df['Key'] = corr_df['Industry Lvl 3 Desc'] + '_' + corr_df['Rating_L1']

        query2 = f'''
        SELECT  IID.industry_lvl3_desc as [Industry Lvl 3 Desc], 
                CASE 
                    WHEN rating IN ('BB1', 'BB2', 'BB3') THEN 'BB' 
                    WHEN rating IN ('B1', 'B2', 'B3') THEN 'B' 
                    WHEN rating IN ('BB1', 'BB2', 'BB3') THEN 'BB' 
                    WHEN rating IN ('C', 'CC', 'CCC1', 'CCC2', 'CCC3') THEN 'CCC OR BELOW' 
                ELSE 
                    rating END AS Rating_L1, 
                nullif(SUM(IID.[percent_mkt_value_prev_mend] * STH.value) / SUM(IID.[percent_mkt_value_prev_mend]), 0) AS BETA, 
                SUM(IID.[percent_mkt_value_prev_mend] * STH.value) / 100 AS BETA_Contr, SUM(IID.[percent_mkt_value]) AS FactorWeight,
                COUNT(*) AS BOND_COUNT
        FROM    {ice} AS IID INNER JOIN {security_ts_hist} AS STH 
                ON IID.cusip = STH.sec_id
        WHERE   (IID.[ice_index_ticker] = '{benchmark}') AND 
                (IID.asof_date = '{self.last_last_friday_str}') AND 
                (IID.rating <> 'CASH') AND (lower(IID.frequency='daily')) AND  
                (STH.asof_date = '{self.last_last_friday_str}') AND 
                (STH.source = 'ICE') AND (STH.attribute = 'BETA_WITH_ALPHA_12M_CDXHY')
        GROUP BY IID.[industry_lvl3_desc], 
                CASE 
                    WHEN rating IN ('BB1', 'BB2', 'BB3') THEN 'BB' 
                    WHEN rating IN ('B1', 'B2', 'B3') THEN 'B' 
                    WHEN rating IN ('BB1', 'BB2', 'BB3') THEN 'BB' 
                    WHEN rating IN ('C', 'CC', 'CCC1', 'CCC2', 'CCC3') THEN 'CCC OR BELOW'
                ELSE 
                    rating END
        ORDER BY Rating_L1, IID.[industry_lvl3_desc]
        '''

        beta_df = readdfsql_schema(sql_replace_escape_char(query2), app=ICE_INDEX_APP)
        beta_df['Key'] = beta_df['Industry Lvl 3 Desc'] + '_' + beta_df['Rating_L1']

        return corr_df, beta_df

    def get_snp_mtd_data(self):
        """
        @summary: This function gets excess returns data for index attribution.

        @params asofdate: Date for which data has to be fetched
        @type: datetime

        @params benchmark: list of benchmark codes for which data has to be fetched
        @type: str

        @return: dataframe
        """
        start_date = self.start_date.replace(day=1)
        start_date_str = start_date.strftime("%Y-%m-%d")

        query = f'''
        SELECT  COUNT(DISTINCT asof_date) AS DATE_COUNT, lcd_broad_industry as LCD_BROAD_INDUSTRY, 
                CASE 
                    WHEN current_rating IN ('B+', 'B', 'B-') THEN 'B' 
                    WHEN current_rating IN ('BB+', 'BB', 'BB-') THEN 'BB' 
                    WHEN current_rating IN ('BBB+', 'BBB', 'BBB-') THEN 'BBB' 
                    WHEN current_rating IN ('C', 'CC', 'CCC+', 'CCC', 'CCC-', 'D', 'NR') THEN 'CCC OR BELOW' 
                ELSE 
                    current_rating END AS RATING,
                SUM(total_return) AS MTD_RET_CONTR,
                SUM(weights) / COUNT(DISTINCT asof_date) AS BUCKET_WEIGHT,
                COUNT(*) / COUNT(DISTINCT asof_date) AS LOAN_COUNT
        FROM    {snp_loan}
        WHERE   asof_date >= '{start_date_str}' AND asof_date <= '{self.last_friday_date_str}'
        GROUP BY lcd_broad_industry,
                CASE 
                    WHEN current_rating IN ('B+', 'B', 'B-') THEN 'B' 
                    WHEN current_rating IN ('BB+', 'BB', 'BB-') THEN 'BB' 
                    WHEN current_rating IN ('BBB+', 'BBB', 'BBB-') THEN 'BBB' 
                    WHEN current_rating IN ('C', 'CC', 'CCC+', 'CCC', 'CCC-', 'D', 'NR') THEN 'CCC OR BELOW'
                ELSE
                    current_rating END
        '''

        industry_return = readdfsql_schema(query, app=snp_app)
        industry_return['Key'] = industry_return['LCD_BROAD_INDUSTRY'] + '_' + industry_return['RATING']

        query2 = f'''
        SELECT COUNT(DISTINCT asof_date) AS DATE_COUNT,
                MIN(asof_date) AS START_DATE, 
                MAX(asof_date) AS END_DATE, 
                LCD_BROAD_INDUSTRY, 
                rating as RATING,	
                SUM(BUCKET_RET) AS RET_IN_BUCKET, 
                AVG(LOAN_COUNT) AS LOAN_COUNT, 
                avg(BUCKET_WGHT) as WGHT
        FROM    (    
                    SELECT asof_date, lcd_broad_industry as LCD_BROAD_INDUSTRY, 
                            CASE 
                                WHEN current_rating IN ('B+', 'B', 'B-') THEN 'B' 
                                WHEN current_rating IN ('BB+', 'BB', 'BB-') THEN 'BB' 
                                WHEN current_rating IN ('BBB+', 'BBB', 'BBB-') THEN 'BBB' 
                                WHEN current_rating IN ('C', 'CC', 'CCC+', 'CCC', 'CCC-', 'D', 'NR') THEN 'CCC OR BELOW' 
                                ELSE current_rating 
                            END AS RATING,
                            SUM(total_return) / SUM(weights) AS BUCKET_RET,
                            COUNT(*) AS LOAN_COUNT,
                            sum(weights) as BUCKET_WGHT
                    FROM    {snp_loan}
                    GROUP BY asof_date, lcd_broad_industry, 
                            CASE WHEN current_rating IN ('B+', 'B', 'B-') THEN 'B' 
                                WHEN current_rating IN ('BB+', 'BB', 'BB-') THEN 'BB' 
                                WHEN current_rating IN ('BBB+', 'BBB', 'BBB-') THEN 'BBB' 
                                WHEN current_rating IN ('C', 'CC', 'CCC+', 'CCC', 'CCC-', 'D', 'NR') THEN 'CCC OR BELOW' 
                                ELSE current_rating 
                            END
                ) as HK_SP_LOAN_RET_BY_SECTOR_RATING
        WHERE        (asof_date >='{start_date_str}') AND (asof_date <= '{self.last_friday_date_str}')
        GROUP BY LCD_BROAD_INDUSTRY, rating
        ORDER BY LCD_BROAD_INDUSTRY, rating
        '''

        rating_return = readdfsql_schema(query2, app=snp_app)
        rating_return['Key'] = rating_return['LCD_BROAD_INDUSTRY'] + '_' + rating_return['RATING']

        return industry_return, rating_return

    def get_CDXHY_level(self, start_date, end_date):
        expr = {'HY CDX': 'DB(CDS,TRAC-X,NAHY100UNF05ONRUN,JPM_RETURN)'}

        jpmQuery = JPMQueryBuilder(expr, start_date, end_date)
        df_cur = jpmQuery.getResultDf()

        jpmQuery_prev = JPMQueryBuilder(expr, self.jpm_prev_month_end_date, self.jpm_prev_month_end_date)
        df_prev = jpmQuery_prev.getResultDf()

        df = pd.concat([df_prev, df_cur])
        df['Return'] = df.pct_change().dropna().mul(100)

        return df

    def get_jpm_ret_date(self, end_date):

        if end_date.day == calendar.monthrange(end_date.year, end_date.month)[1]:
            jpm_ret_date = end_date
        else:
            jpm_ret_date = end_date - BDay(1)

        jpm_ret_date = dtutil.adjustdate(jpm_ret_date)

        return jpm_ret_date


if __name__ == '__main__':
    from common.utils.envsetup import setuplogger

    setuplogger()
    from apo_config.config import config

    config.init('prod')
    obj = index_data(datetime(2021, 6, 1), datetime(2021, 6, 6))
    betacorrdf = obj.get_beta_corr_data('H0A0')
    data = obj.get_CDXHY_level(datetime(2021, 6, 4), datetime(2021, 6, 4))
