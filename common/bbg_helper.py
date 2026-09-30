'''
Created on Apr 11, 2018

@author: yma
'''
import pandas as pd
from common import pybbg
import tia.bbg.datamgr as dm
import blpapi
import datetime
    
'''
Facto to BBG ID
'''
def getFactorMapping():
    return {
        'cdx_ig': 'IBOXUMAE Curncy',
        'cdx_hy': 'IBOXHYSE Curncy',
        'itrx_main': 'ITRXEBE Curncy',
        'itrx_xo': 'ITRXEXE Curncy',
        'cdx_em': 'IBOXUMSE Curncy',
        'vix':      'VIX Index',
        '3m_tbill':         'G0O1 Index',
        'cembi_all':    'JCBBCOMP Index',
        '10Y_treasury_index':   'F08210YR Index',
        'merrill_2y':  'MLT1US02 Index',
        'merrill_5y':  'MLT1US05 Index',
        'merrill_10y':  'MLT1US10 Index',
        'merrill_30y':  'MLT1US30 Index',
        'barcalys_7_10': 'BMF7TRUU Index'
        }

'''
Monthly bbg call example:
=BDH("IBOXUMAE msg1 Curncy","PX_LAST","01/01/2017","","per=cm")
'''
def getFactorHistory(factorList, startDate):
    factorMapping = getFactorMapping()
    bbgLoader = pybbg.Pybbg()
    isinSecList = [ factorMapping[f] for f in factorList]
    
    dailyDf = bbgLoader.bdh(isinSecList, ['PX_LAST'], 
                      start_date = startDate, 
                      end_date = datetime.datetime.today() + datetime.timedelta(days=-1),
                    periodselection = 'DAILY'
                      )    
    
    monthlyDf = dailyDf.resample('M').last()
    curIndex = monthlyDf.index
    curIndex = [ c.strftime('%Y-%m') for c in curIndex]
    monthlyDf.index = curIndex

    reverseFactorMapping = {v: k for k, v in list(factorMapping.items())}
    colNames = [ reverseFactorMapping[r] for r in dailyDf.columns]
    dailyDf.columns = colNames
    monthlyDf.columns = colNames
    
    return (dailyDf, monthlyDf)


class BBGUtil():
    def __init__(self):
        pass

    def fill_sec_table(self, seclist, attrlist):
        mgr = dm.BbgDataManager()
        # a = fill_sec_table('L9308UAQ3 CORP', 'ID_BB_GLOBAL', mgr)
        resultsDict = dict()
        for sec in seclist:
            try:
                vals = mgr[sec].get_attributes(attrlist)
                resultsDict[sec] = vals
            except:
                print(("Error in getting {} for Security - {}".format(attrlist, sec)))
                continue
            # attr_dict = dict(zip(attrlist, vals))
            # op_series = pd.Series(attr_dict, name=sec)
        # result = pd.DataFrame(results, columns=['Sec', attrlist])
        return resultsDict


if __name__ == '__main__':
    obj = BBGUtil()
    a = ['71913BAE2 CORP', 'L9308UAQ3 CORP']

    aa = obj.fill_sec_table(a, 'ID_BB_GLOBAL')
    print (aa)
    print ('Check')







