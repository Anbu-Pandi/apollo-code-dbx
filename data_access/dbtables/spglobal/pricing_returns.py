import logging

from data_access.dbtables.spglobal.company_data import *
from common.utils import sqlutil, dtutil

from data_access.dbtables.spglobal import APP


def get_pricing(company_id, formula_metric, from_date, to_date, periodicity):
    company_id_csv = '-1'
    company_id_sql_list = tosqllist([])
    if (company_id is not None) or (isinstance(company_id, list)) or (not pd.isnull(company_id)):
        company_id_csv = get_csv_values(company_id)
        company_id_sql_list = company_id_sql_list = tosqllist(company_id)

    logging.info(f'fetching pricing data')

    query = f'''with total_return_pricing as 
                (
                    select
                    cpe.pricingDate,
                    cpe.priceClose,
                    cpe.priceClose * coalesce(cpedaf.divAdjFactor,
                    1) as adjPrice,
                    cs.companyId,
                    cs.securityId
                    from
                    ciqSecurity cs
                    join ciqTradingItem cti on
                    cs.securityId = cti.securityId
                    join ciqPriceEquity cpe on
                    cti.tradingItemId = cpe.tradingItemId
                    left join ciqPriceEquityDivAdjFactor cpedaf on
                    cpedaf.tradingItemId = cpe.tradingItemId
                    and cpe.pricingDate >= cpedaf.fromDate
                    --Match the divAdjFactor
                    and (cpe.pricingDate <= cpedaf.toDate
                    or cpedaf.toDate is null)
                    --to the pricingDate
                    where
                    cs.companyId in {company_id_sql_list}
                    and cti.primaryFlag = 1
                    and cs.primaryFlag = 1
                    and cpe.pricingDate >= '2012-01-01'
                ),
                pricing_and_TEV as 
                    (select      cpe.pricingDate,
                                 cpe.priceClose,
                                 cpe.priceClose * coalesce(cpedaf.divAdjFactor, 1) as adjPrice,
                                 LAG(cpe.priceClose * coalesce(cpedaf.divAdjFactor, 1), 252) OVER (partition by cpe.tradingItemId order by cpe.pricingDate) as adjPrice1Y,
                                 LAG(cpe.pricingDate, 252) OVER (partition by cpe.tradingItemId order by cpe.pricingDate) as pricingDate1Y,
                                 cmc.marketCap,
                                 cmc.sharesOutstanding,
                                 cmc.TEV,
                                 cs.companyId,
                                 cs.securityId
                            from ciqSecurity cs
                            join ciqTradingItem cti
                              on cs.securityId        = cti.securityId
                            join ciqPriceEquity cpe
                              on cti.tradingItemId    = cpe.tradingItemId
                            join ciqMarketCap cmc
                              on cmc.companyId        = cs.companyId
                             and cmc.pricingDate      = cpe.pricingDate
                            left join ciqPriceEquityDivAdjFactor cpedaf
                              on cpedaf.tradingItemId = cpe.tradingItemId
                             and cpe.pricingDate      >= cpedaf.fromDate
                             --Match the divAdjFactor
                             and (   cpe.pricingDate  <= cpedaf.toDate
                                or   cpedaf.toDate is null)
                           --to the pricingDate
                           where cs.companyId in {company_id_sql_list}
                             and cti.primaryFlag = 1
                             and cs.primaryFlag  = 1
                             and cpe.pricingDate >= '2012-01-01'
                    ),
                fins_and_industry  as 
                    (        select f.*,
                             i.industry_level_1,
                             i.industry_level_2,
                             i.industry_level_3,
                             i.industry_level_4,
                             company_name as company_name2
                        from workspace.f_get_company_financials_csv(
                                 '{company_id_csv}',
                                 '{get_csv_values(formula_metric)}',
                                 '{from_date}',
                                 '{to_date}',
                                 '{periodicity}') f
                        join workspace.f_get_industry_csv('{company_id_csv}') i
                        on f.company_id = i.companyid
                    ),
                fins_industry_pricing as 
                    (        SELECT fai.*,
                             pat.pricingDate,
                             pat.pricingDate1Y,
                             pat.marketCap,
                             pat.sharesOutstanding,
                             pat.TEV,
                             pat.adjPrice,
                             pat.adjPrice1Y,
                             (pat.adjPrice / pat.adjPrice1Y - 1) as totalReturn1Y,
                             ROW_NUMBER() OVER (PARTITION BY fai.period_end_date,
                                                             fai.company_id,
                                                             fai.dataitem_id
                                                    ORDER BY pat.pricingDate DESC) AS RowNum
                        from fins_and_industry fai
                        join pricing_and_TEV pat
                          on pat.companyId   = fai.company_id
                         and pat.pricingDate <= fai.period_end_date
                    )
        select *
        from fins_industry_pricing fip
        where RowNum = 1
        order by fip.company_id, fip.period_end_date DESC
    '''

    df = sqlutil.readdfsql(query, app=APP)
    df['qtr'] = df['calendar_year'].astype(str) + ' Q' + df['calendar_quarter'].astype(str)
    return df
