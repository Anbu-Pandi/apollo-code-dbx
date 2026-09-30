from common.db_utilities import *
from datetime import datetime

class ETFData:
    def __init__(self):
        pass

    def getETFConstituents(self, asofdate, benchmark):
        """
        @summary: This function gets data from ETF_CONSTITUENTS table.

        @params asofdate: Date for which data has to be fetched
        @type: datetime

        @params benchmark: list of benchmark codes for which data has to be fetched
        @type: list

        @return: dataframe
        """
        startTime = pd.datetime.now()
        conn = getAPOSecDBServerSql()
        query = "Select [as_of_date],[Composite_Ticker],[Constituent_Ticker],[Constituent_Name],[Cusip],[Isin],[Weight],[Market_Value],[Shares_Held]" \
                " from [dbo].[ETF_CONSTITUENTS]" \
                " where [as_of_date] = '" + str(asofdate) + "' and Composite_Ticker in " + \
                str(benchmark).replace("[", "(").replace("]", ")")

        df = pd.read_sql(query, conn)

        conn.close()
        print(('Data Fetched from SQL, Time Taken: ', str(pd.datetime.now() - startTime)))
        return df

    def benchmarkTradeBlotter(self, asofdate, prevdate, benchmark):
        """
                @summary: This functions provides trading activity comparison of ETF benchmarks between T vs T-n date.

                @params asofdate: T - date for comparison
                @type: datetime

                @params prevdate: T-n - date against which comparison of T date to be done
                @type: datetime

                @params benchmark: list of benchmark codes for which data has to be fetched
                @type: list

                @return: dataframe

        """

        startTime = pd.datetime.now()
        conn = getAPOSecDBServerSql()

        query = "SELECT PREV.Constituent_Ticker AS PREV_TICKER,PREV.Constituent_Name as PREV_Name, PREV.Isin AS PREV_ISIN, CUR.Constituent_Ticker AS CUR_TICKER, CUR.Composite_Ticker as CUR_Composite_Ticker, PREV.Composite_Ticker as Prev_CUR_Composite_Ticker, " \
                "CUR.Isin AS CUR_ISIN, CUR.Constituent_Name as CUR_Name, CUR.Cusip as CUR_Cusip, PREV.Cusip as Prev_Cusip,PREV.Shares_Held AS PREV_SHARE, CUR.Shares_Held AS CUR_SHARE, " \
                "CUR.Shares_Held - PREV.Shares_Held AS SHARE_CHG " \
                "FROM ETF_Constituents_Aggregate PREV FULL OUTER JOIN  ETF_Constituents_Aggregate CUR " \
                "ON (PREV.Constituent_Ticker = CUR.Constituent_Ticker)" \
                "where PREV.[Composite_Ticker] in " + str(benchmark).replace("[", "(").replace("]", ")") + \
                "AND CUR.[Composite_Ticker] in " + str(benchmark).replace("[", "(").replace("]", ")") + \
                "AND PREV.as_of_date = '" + str(asofdate) + \
                "' AND CUR.as_of_date = '" + str(prevdate) + "'"

        df = pd.read_sql(query, conn)
        conn.close()
        print(('Data Fetched from SQL, Time Taken: ', str(pd.datetime.now() - startTime)))
        return df


class MarkitData:
    def __init__(self):
        pass

    def getAllBondsMarkitPriceData(self, asofdate):
        """
            @summary: This function gets bonds pricing data from markit_pricing_data table.

            @params asofdate: date for which pricing data to be fetched
            @type: datetime

            @return: dataframe
        """
        startTime = pd.datetime.now()
        conn = getAPOSecDBServerSql()

        query = "Select [Date], [ISIN], [CUSIP], [TICKER], [Coupon], [Bid Price], [Mid Price], [Ask Price] " \
                " from [dbo].[markit_pricing_data] where [Date] = '" + str(asofdate) + "'"

        df = pd.read_sql(query, conn)
        conn.close()

        print(('Data Fetched from SQL, Time Taken: ', str(pd.datetime.now() - startTime)))
        return df

    def getBondsMarkitPriceData(self,asofdate):
        """
            @summary: This function gets bonds pricing data from markit_pricing_data table.

            @params asofdate: date for which pricing data to be fetched
            @type: datetime

            @return: dataframe
        """
        startTime = pd.datetime.now()
        conn = getAPOSecDBServerSql()

        query = "Select [Date], [ISIN], [CUSIP] , [Ticker], [Short name of Issuer], [Currency], [Classification], [Region], [Tier], [Mid Price]" \
                " from [dbo].[markit_pricing_data] where [Date] = '" + str(asofdate) + "'"

        df = pd.read_sql(query, conn)
        conn.close()

        print(('Data Fetched from SQL, Time Taken: ', str(pd.datetime.now() - startTime)))
        return df


class ICEData:
    def __init__(self):
        pass

    def getICEData(self, asofdate, benchmark):
        """
        @summary: This function gets ICE benchmark data from ice_index_data_new table for given benchmarks.

        @params asofdate: Date for which data has to be fetched
        @type: datetime

        @params benchmark: list of benchmark codes for which data has to be fetched
        @type: list

        @return: dataframe
        """
        startTime = pd.datetime.now()
        conn = getAPOSecDBServerSql()

        query = "Select [Asofdate],[ICE Index Ticker],[Description], [Cusip],[ISIN number],[Ticker],[Rating],[Industry Lvl 4 Desc],[Face Value_LOC], [% Mkt Value]" \
                " from [dbo].[ice_index_data_new] " \
                "where [AsofDate] = '" + str(asofdate) + "' and [ICE Index Ticker] in " + str(benchmark).replace("[",
                                                                                                                 "(").replace(
            "]", ")")

        df = pd.read_sql(query, conn)
        conn.close()
        print(('Data Fetched from SQL, Time Taken: ', str(pd.datetime.now() - startTime)))
        return df

    def getAllICEData(self, asofdate):
        """
            @summary: This function gets all ICE benchmark data from ice_index_data_new table for given date.

            @params asofdate: Date for which data has to be fetched
            @type: datetime

            @params benchmark: list of benchmark codes for which data has to be fetched
            @type: list

            @return: dataframe
        """
        startTime = pd.datetime.now()
        conn = getAPOSecDBServerSql()

        query = "Select [Asofdate],[Description], [Cusip],[ISIN number],[Ticker],[Rating],[Industry Lvl 4 Desc],[Face Value_LOC], [Subordination Type]" \
                " from [dbo].[ice_index_data_new]" \
                " where [AsofDate] = '" + str(asofdate) + "'"

        df = pd.read_sql(query, conn)
        conn.close()

        print(('Data Fetched from SQL, Time Taken: ', str(pd.datetime.now() - startTime)))
        return df

    def getICEMetaData(self):
        startTime = pd.datetime.now()
        conn = getAPOSecDBServerSql()

        query = '''
                Select distinct(coalesce([ISIN number], [Cusip])) as [ICE_Identifier],
                    [Cusip], [ISIN number], [Description], [Ticker], [Par Wtd Coupon], [Rating],  
                    [Industry Lvl 1 Desc], [Industry Lvl 2 Desc], [Industry Lvl 3 Desc], [Industry Lvl 4 Desc],
                     [Subordination Type]

                from [dbo].[ice_index_data_new] 
                '''

        df = pd.read_sql(query, conn)
        conn.close()

        df = df.loc[~df['Subordination Type'].isnull()]
        ICERatingScale = {'AAA': 1, 'AA1': 2, 'AA2': 3, 'AA3': 4, 'A1': 5, 'A2': 6, 'A3': 7, 'BBB1': 8, 'BBB2': 9,
                          'BBB3': 10,
                          'BB1': 11, 'BB2': 12, 'BB3': 13, 'B1': 14, 'B2': 15, 'B3': 16, 'CCC1': 17, 'CCC2': 18,
                          'CCC3': 19,
                          'CC': 20, 'C': 21, 'D': 22, 'NR': -1, 'CASH': -2}

        df.loc[:, 'RatingScore'] = df['Rating'].apply(
            lambda x: ICERatingScale[x] if x in list(ICERatingScale.keys()) else None)

        finalDF = pd.DataFrame()

        grpbyDF = df.groupby(['Ticker', 'Subordination Type'])

        for key, data in grpbyDF:
            if len(data) > 1:
                data = data.sort_values(['RatingScore'])
                tempData = pd.DataFrame(data.iloc[-1]).T
            else:
                tempData = data

            tempData = tempData.set_index(['Ticker', 'Subordination Type'])

            finalDF = finalDF.append(tempData)
            del data, tempData

        finalDF = finalDF.drop(['RatingScore'], axis=1)

        dataDict = finalDF.to_dict('index')

        print(('Data Fetched from SQL, Time Taken: ', str(pd.datetime.now() - startTime)))
        return dataDict

if __name__ == "__main__":
    markit = ICEData()
    df1 = markit.getICEMetaData()
    print('Checkpoint 1')
