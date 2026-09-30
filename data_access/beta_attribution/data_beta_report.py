from data_access.beta_attribution import APP
from data_access.beta_attribution import readdfsql_schema


def get_benchmark_beta(benchmark=('iboxx_usloan_trs', 'SPY US EQUITY', 'tsy_10y'),
                       attribute='BETA_WITH_ALPHA_12M_CDXHY'):
    """
    @summary: This function gets beta for given benchmark.

    @params benchmark: list of benchmark codes for which data has to be fetched
    @type: tuple

    @return: dataframe
    """
    query = """SELECT [sec_id] as 'Benchmark' , asof_date as [ASOFDATE], [value] as 'Beta to CDX HY'
                FROM [dbo].[security_ts_hist]
                where (sec_id in {}) 
                and (attribute= '{}') and asof_date > '2014-09-04'""".format(benchmark, attribute)

    beta_df = readdfsql_schema(query, app=APP)

    return beta_df


if __name__ == '__main__':
    a = get_benchmark_beta()
