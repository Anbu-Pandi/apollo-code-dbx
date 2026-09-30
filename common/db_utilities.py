'''
Created on Sep 25, 2017

@author: yma
'''
import pandas as pd
from common import pybbg

def getRatingMapping(conn):
    sql = '''
            SELECT
            Moody as Moody,
            SP as SP,
            FITCH as Fitch,
            [Index] as Notch,
            [Rating Bucket]
            FROM
            RATING_MAPPING
        '''    
        
    df = pd.read_sql(sql, conn)   
    
    return df


def getPosFromDb(cnxn, portName, notionalCol='NOTIONALASS'):
    # EXCLUDING SWAP BELOW
    sql = '''
    select 
        EBS.ISIN,
        SUM(EPC.{1}) AS NOT_ASSET
    from EM_PF_CONTENTS EPC, EM_BOND_STATIC EBS
    WHERE 
    EBS.CUSIP = EPC.CUSIP AND 
    EBS.TYPE <> 'S' AND
    EPC.NOTIONAL <> 0 AND EPC.PFNAME = '{0}'
    group by EBS.ISIN
    '''.format(portName, notionalCol)
    
    df = pd.read_sql(sql, cnxn)     
    
    return df 

def getPosDetailsForPort(cnxn, portList):
    portStrList = ["'" + str(port) + "'" for port in portList]
    portStr = ','.join(portStrList)

    sql = '''
        SELECT
            EPF.PFNAME,
            EBS.ISIN,
            EBS.CUSIP,
            EES.CORP,
            EBS.CPN,
            EBS.IS_CALL,
            EBS.NXT_CALL_DT,
            EBS.NXT_CALL_PX,            
            EBS.TYPE,
            EBS.MATURITY,
            EBS.AMT_OUTSTANDING,
            EBS.CCY,
            RM_MDY.SP AS RTG_MDY,
            RM_SP.SP AS RTG_SP,
            RM_F.SP AS RTG_F,
            EBR.RW2,
            EBS.SEN,
            EC.REGION,
            EC.CNTRY,
            EC.CNTRYNAME,
            EES.INDUSTRY_SECTOR,
            EES.INDUSTRY_GROUP,
            EES.INDUSTRY,
            EBP.DURW,
            EBS.IS_FLOAT,
            EBP.ZSPD,
            EBP.PX,
            EBP.ACCRP,
            EBP.YP,
            EBP.YMP,
            EBP.YCP
        FROM 
            EM_PF_CONTENTS EPF LEFT OUTER JOIN
            EM_BOND_STATIC EBS ON EPF.CUSIP = EBS.CUSIP LEFT OUTER JOIN
            EM_ENTITY_STATIC EES ON EBS.ID_BB_COMPANY = EES.ID_BB_COMPANY LEFT OUTER JOIN
            EM_BOND_RTG EBR ON EPF.CUSIP = EBR.CUSIP LEFT OUTER JOIN
            EM_BOND_PX EBP ON EPF.CUSIP = EBP.CUSIP LEFT OUTER JOIN
            EM_CNTRY EC ON EES.CNTRY = EC.CNTRY LEFT OUTER JOIN
            rating_mapping RM_MDY ON RM_MDY.[Index] = EBR.RTG_MDY LEFT OUTER JOIN
            rating_mapping RM_SP ON RM_SP.[Index] = EBR.RTG_SP LEFT OUTER JOIN
            rating_mapping RM_F ON RM_F.[Index] = EBR.RTG_F
        WHERE
            EPF.PFNAME IN ( {0})
        ORDER BY 
            EPF.PFNAME,
            EES.CORP,
            EBS.MATURITY
    '''.format(portStr)
    
    df = pd.read_sql(sql, cnxn)     
    
    return df

def getBondDetails(cnxn, isinList):
    isinStrList = ["'" + str(isin) + "'" for isin in isinList]
    isinStr = ','.join(isinStrList)

    sql = '''
        SELECT
            EBS.ISIN,
            EBS.CUSIP,
            EBS.AMT_OUTSTANDING,
            EBS.IS_CALL,
            RM.FITCH,
            EESC.RTG_T,
            EESC.ANALYST,
            EBS.NXT_CALL_DT,
            EBS.NXT_CALL_PX,
            EBP.ZSPD,
            EESC.QSOVG,
            EC.REGION,
            EES.CORP,
            EBR.RW2,
            RMT.SP AS RTG_INTERNAL
        FROM 
            EM_BOND_STATIC EBS LEFT OUTER JOIN 
            EM_ENTITY_STATIC EES ON EBS.ID_BB_COMPANY = EES.ID_BB_COMPANY LEFT OUTER JOIN
            EM_CNTRY EC ON EES.CNTRY = EC.CNTRY LEFT OUTER JOIN
            EM_BOND_RTG EBR ON EBR.CUSIP = EBS.CUSIP LEFT OUTER JOIN
            EM_ENTITY_SCORE EESC ON EBS.SEN = EESC.SEN AND EES.CIQIDFP = EESC.CIQIDFP LEFT OUTER JOIN
            EM_BOND_PX EBP ON EBS.CUSIP = EBP.CUSIP LEFT OUTER JOIN
            rating_mapping RM ON RM.[Index] = EBR.RTG_F LEFT OUTER JOIN
            rating_mapping RMT ON RMT.[Index] = EESC.RTG_T
        WHERE EBS.ISIN IN ({0})
    '''.format(isinStr)
    
    df = pd.read_sql(sql, cnxn)     
    
    return df


def compareRiskPosVSDatabasePos(riskPosDf, dbPosDf, addSecNameFromBBG = False):
    # aggregate over isin
    riskPosDf = riskPosDf.groupby(['ISIN'])[['TRADE_QUANTITY']].sum()
    riskPosDf = riskPosDf.reset_index()
    
    riskPosDf = riskPosDf[riskPosDf['TRADE_QUANTITY']!=0]
    
    
    mergedDf = riskPosDf.merge(dbPosDf, indicator=False, how='outer')
    
    mergedDf.rename(columns={
                                        'TRADE_QUANTITY':'RISK ASSET NOTIONAL',
                                        'NOT_ASSET':'ALLOCATION ASSET NOTIONAL',
                                        }, inplace=True)
    
    
    mergedDf['RISK ASSET NOTIONAL'] = mergedDf['RISK ASSET NOTIONAL'].fillna(0)
    mergedDf['ALLOCATION ASSET NOTIONAL'] = mergedDf['ALLOCATION ASSET NOTIONAL'].fillna(0)
    
    mergedDf['Mismatch'] = mergedDf['RISK ASSET NOTIONAL'] - mergedDf['ALLOCATION ASSET NOTIONAL']

    mergedDf['Mismatch_abs'] = mergedDf['Mismatch'].abs()
#     mergedDf = mergedDf.sort(columns='Mismatch_abs', ascending = False).drop('Mismatch_abs', axis=1)
    mergedDf = mergedDf.sort_values(['Mismatch_abs'], ascending=[False]).drop('Mismatch_abs', axis=1)
    
    if addSecNameFromBBG:
        bbgLoader = pybbg.Pybbg()
        isinSecList = [ str(x) + ' CORP' for x in mergedDf['ISIN'].values]
        isinToSecNameDf = bbgLoader.bdp(isinSecList, ['SECURITY_NAME'])
        isinToSecNameDf = isinToSecNameDf.transpose()
        
        isinToSec = dict(list(zip(isinToSecNameDf.index, isinToSecNameDf['SECURITY_NAME'].values)))
        isinToSec = {isinCorp.split(' ')[0] : isinToSec[isinCorp] for isinCorp in isinToSec}
        isinToSecDf = pd.DataFrame(list(isinToSec.items()), columns = ['ISIN', 'Name'])
        
        mergedDf = mergedDf.merge(isinToSecDf, indicator=False, how='outer')
        
    
    return mergedDf



















