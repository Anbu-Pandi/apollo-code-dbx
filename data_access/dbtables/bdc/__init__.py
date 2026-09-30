from data_access.dbtables.factset import APP as FACTSET_APP
from data_access.dbtables import BDC_APP, SNOWFLAKE_SPGLOBAL_APP


SPGLOBAL_APP = SNOWFLAKE_SPGLOBAL_APP

__all__ = ["BDC_APP", "FACTSET_APP", "SPGLOBAL_APP"]
