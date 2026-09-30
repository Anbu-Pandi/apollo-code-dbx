import logging
from typing import List, Optional, Dict, Any

import pandas as pd
from common.utils import sqlutil
from data_access.dbtables import OLYMPUS_APP
from data_access.spark.api import sparkmanager


class LimaDataAccessLayer:
    def __init__(self, schema_name: str = "dbo"):
        self.schema_name = schema_name

    def get_table_name(self, table_suffix: str) -> str:
        return f"{self.schema_name}.{table_suffix}"

    def create(self, df: pd.DataFrame, table_name: str) -> None:
        try:
            full_table_name = self.get_table_name(table_name)
            logging.info(f"Creating data in table: {full_table_name}")
            sparkmanager.pandastosql(df, full_table_name, app=OLYMPUS_APP)
            logging.info(f"Successfully created data in table: {full_table_name}")
        except Exception as e:
            logging.error(f"Error creating data in {full_table_name}: {str(e)}")
            raise Exception(f"Error creating data in {full_table_name}: {str(e)}")

    def read(self, table_name: str,
             conditions: Optional[Dict[str, Any]] = None,
             columns: Optional[List[str]] = None) -> pd.DataFrame:
        try:
            full_table_name = self.get_table_name(table_name)

            select_cols = "*"
            if columns:
                select_cols = ", ".join(columns)

            where_clause = ""
            if conditions:
                conditions_list = []
                for col, value in conditions.items():
                    if isinstance(value, str):
                        conditions_list.append(f"{col} = '{value}'")
                    elif isinstance(value, (list, tuple)):
                        values_str = ", ".join([f"'{v}'" if isinstance(v, str) else str(v) for v in value])
                        conditions_list.append(f"{col} IN ({values_str})")
                    else:
                        conditions_list.append(f"{col} = {value}")
                where_clause = "WHERE " + " AND ".join(conditions_list)

            query = f"SELECT {select_cols} FROM {full_table_name} {where_clause}"

            logging.info(f"Executing query: {query}")
            result = sqlutil.readdfsql(query, app=OLYMPUS_APP)
            logging.info(f"Successfully read data from {full_table_name}")
            return result

        except Exception as e:
            logging.error(f"Error reading data from {full_table_name}: {str(e)}")
            raise Exception(f"Error reading data from {full_table_name}: {str(e)}")

    def delete(self, table_name: str, conditions: Dict[str, Any]) -> None:
        try:
            full_table_name = self.get_table_name(table_name)

            where_list = []
            for col, value in conditions.items():
                if isinstance(value, str):
                    where_list.append(f"{col} = '{value}'")
                elif isinstance(value, (list, tuple)):
                    values_str = ", ".join([f"'{v}'" if isinstance(v, str) else str(v) for v in value])
                    where_list.append(f"{col} IN ({values_str})")
                else:
                    where_list.append(f"{col} = {value}")
            where_clause = " AND ".join(where_list)

            query = f"DELETE FROM {full_table_name} WHERE {where_clause}"

            logging.info(f"Executing delete query: {query}")
            sqlutil.execsql(query, app=OLYMPUS_APP)
            logging.info(f"Successfully deleted data from {full_table_name}")

        except Exception as e:
            logging.error(f"Error deleting data from {full_table_name}: {str(e)}")
            raise Exception(f"Error deleting data from {full_table_name}: {str(e)}")


if __name__ == "__main__":
    dal = LimaDataAccessLayer()
    df = dal.read("dl_metric_summary", {"fund_entity_code": ["CSTRAT"]})
