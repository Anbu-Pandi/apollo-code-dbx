"""
🚀 Quick runner for CDS Index Tranche table initialization

Run this to create the markit_cds_index_tranche table in Databricks
"""

from data_access.dbtables.market_data.credit.init_tables import init_cds_index_tranche_table

if __name__ == '__main__':
    init_cds_index_tranche_table(overwrite=True)

