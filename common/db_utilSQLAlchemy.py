import urllib.request, urllib.parse, urllib.error
import pandas as pd
from sqlalchemy import create_engine, event

def dbConnSQLAlchemy():
    """
    Creates an engine connection with database using SQLAlchemy
    :return: instance of SQLAlchemy engine
    """
    dbParams = urllib.parse.quote_plus("Driver={SQL Server Native Client 11.0};"
                               "Server=MarketAnalyticsData-sql;"
                               "Database=MarketAnalyticsData;"
                               "Trusted_Connection=yes; autocommit=True;")
    engine = create_engine("mssql+pyodbc:///?odbc_connect=%s" % dbParams)
    return engine

def insertIntoTbl(df, tblName):
    """
    For inserting large sets of data into database

    :param df: DataFrame in same format as table Schema
    :param tblName: name of the table for insertion
    """
    engine = dbConnSQLAlchemy()
    @event.listens_for(engine, "before_cursor_execute")
    def receive_before_cursor_execute(conn, cursor, statement, params, context, executemany=True):
        if executemany:
            cursor.fast_executemany = True

    df.to_sql(tblName, engine, index=False, if_exists="append", schema="dbo")

def fetchDatafromDB(query):
    engine = dbConnSQLAlchemy()
    conn = engine.connect()
    result = conn.execute(query)
    df = pd.DataFrame(result)
    df.columns = list(result.keys())
    return df
