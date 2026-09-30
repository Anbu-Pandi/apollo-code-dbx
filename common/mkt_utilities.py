'''
Created on May 23, 2019

@author: yma
'''
import pandas as pd
from common.date_utilities import DateTimeUtilities
from scipy.interpolate import interpolate
import numpy as np

class MarketDataUtilities(object):
    @staticmethod
    def getTsySymbolList():
        tsySymboToTenorList = {
                'tsy_1m': '1 month bill',
                'tsy_3m': '3 month bill',
                'tsy_12m': '12 month bill',
                'tsy_2y': '2 year note',
                'tsy_3y': '3 year note',
                'tsy_5y': '5 year note',
                'tsy_7y': '7 year note',
                'tsy_10y': '10 year note',
                'tsy_30y': '30 year bond'
                } 
        
        return tsySymboToTenorList
    
    '''
    Get tsy return curve linear interp on mod dur
    '''
    @staticmethod
    def getTsyReturnCurve(conn, asofdate, freq, scalor):
        tsySymboToTenorList = MarketDataUtilities.getTsySymbolList()
        
        tsyList = list(tsySymboToTenorList.keys())
        
        tsyStr = ["'" + str(cds) + "'" for cds in tsyList]
        tsyStr = ', '.join(tsyStr)
        sql = '''
            SELECT
            BETA_TBL.SEC_ID,
            DUR_TBL.VALUE AS DUR,
            BETA_TBL.VALUE AS RET,
            BETA_TBL.ATTRIBUTE
            FROM 
            security_ts_hist BETA_TBL,
            SECURITY_TS_HIST DUR_TBL
            WHERE 
            BETA_TBL.SEC_ID IN ( {0} )
            AND DUR_TBL.SEC_ID IN (  {0} )
            AND BETA_TBL.ATTRIBUTE = 'TOTAL_RET'
            AND DUR_TBL.ATTRIBUTE = 'MODDUR'
            AND BETA_TBL.ASOFDATE = '{1}'            
            AND DUR_TBL.ASOFDATE = '{1}' 
            AND BETA_TBL.SOURCE = 'JPM'              
            AND DUR_TBL.SOURCE = 'JPM'              
            AND BETA_TBL.SEC_ID = DUR_TBL.SEC_ID
            AND BETA_TBL.FREQUENCY = '{2}'
            AND DUR_TBL.FREQUENCY = '{2}'            
            ORDER BY BETA_TBL.SEC_ID        
                '''.format(tsyStr, DateTimeUtilities.dateToStr(asofdate, '%Y-%m-%d'),
                           freq)
            
        df = pd.read_sql(sql, conn)
        
        if len(df) != 0:  
            tsyToRet = dict(list(zip(df['SEC_ID'].values, df['RET'].values)))
            tsyToDur = dict(list(zip(df['SEC_ID'].values, df['DUR'].values)))
        else:
            tsyToRet = dict(list(zip(tsyList, np.zeros(len(tsyList)))))
            tsyToDur = dict(list(zip(tsyList, np.zeros(len(tsyList)))))            
            
        durList = []
        retList = []
        for symbol in tsySymboToTenorList:
            if symbol in tsyToDur and symbol in tsyToRet:
                durList.append(tsyToDur[symbol])
                retList.append(tsyToRet[symbol] * scalor)
        
        fill_value = (retList[0] * scalor, retList[-1] * scalor)
        tsyDurReturnCurve = interpolate.interp1d(durList, retList, bounds_error = False, fill_value = fill_value)
        
        return tsyDurReturnCurve
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
    