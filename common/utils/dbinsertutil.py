"""
https://gadymargalit.wordpress.com/2014/07/19/python-and-mssql-performance/
And here are the results:
Running 100 inserts
To save to DB myway 0:00:00.166000
To save to DB execute 0:00:07.265000
To save to DB executemany 0:00:14.173000

Running 2000 inserts (Azure DB)
To save to DB myway 0:00:01.194000
To save to DB execute 0:02:24.632000
To save to DB executemany 0:04:48.415000

Running 2000 inserts (LAN DB)
To save to DB myway 0:00:00.524000
To save to DB execute 0:00:17.183000
To save to DB executemany 0:00:31.523000
So how did I do it?
I created my own lib called MSSQL.py
"""
# MSQL functions Version 0.5

# MSQL functions Version 0.5
import datetime

import pandas as pd
import pyodbc


# CONNECTION_STRING = "DRIVER={SQL Server};SERVER=localhost;DATABASE=master"

def CreateCursor(CONNECTION_STRING):
    return pyodbc.connect(CONNECTION_STRING).cursor()


"""
# samples
cursor.execute("select top 10 * from stocks")
rows = cursor.fetchall()
for row in rows:
    print (row.TickerSymbol, row.Company)
cursor.close()
"""


def encapsulate(value, is_databricks_app=False):
    # Encapsulate the input for SQL use (add ' etc)
    if isinstance(value, list):
        r = []
        for i in value:
            if isinstance(i, str):
                r.append(encapsulate(i, is_databricks_app=is_databricks_app))
            else:
                r.append(str(encapsulate(i, is_databricks_app=is_databricks_app)))
        return ','.join(r)
    elif isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    elif isinstance(value, str):
        return "N'" + value.replace("'", "''") + "'"
    elif isinstance(value, datetime.date) and False:
        return "'" + value.isoformat() + "'"
    elif isinstance(value, (datetime.datetime, datetime.date)):
        val = value.strftime('%Y-%m-%d %H:%M:%S')
        return f"CAST('{val}' as TIMESTAMP)" if is_databricks_app else f"CONVERT(datetime2,'{val}')"
    elif pd.isnull(value):
        return "Null"
    else:
        return str(value)
        # cursor,user.fbid,user.name,user.age,user.gender,user.device,user.platform,DeviceTypesDic, platformTypesDic


def enlist(rows, max_rows_per_batch=1000, is_databricks_app=False):
    # Takes a list of items and make them in format for SQL insert
    # limit of 1000 lines
    clLists = []
    cl = []
    LineCounter = 0
    for i in rows:
        if LineCounter >= max_rows_per_batch:
            clLists.append("select " + " union all select ".join(cl))
            cl = []
            LineCounter = 0
        cl.append(encapsulate(i, is_databricks_app=is_databricks_app))
        LineCounter += 1
    clLists.append("select " + " union all select ".join(cl))
    return clLists


def CreateDicForTable(table, cursor):
    cursor.execute("select * from %s" % table)
    rows = cursor.fetchall()
    dic = {}
    for row in rows:
        dic[row[1]] = row[0]
    return dic


def RefeshDicForTable(table, TableDic, NewItemsList, cursor):
    # Gets table dictionary and new items rows, add the new items to the DB and update the Dict with new keys
    cursor.execute("select isnull(max(%sID),0) as MaxID from %s" % (table[:-1], table))
    maxID = cursor.fetchone()[0]
    for NewItems in enlist(NewItemsList):
        cursor.execute("Insert into %s (Name) Values " % table + NewItems)
    cursor.commit()
    cursor.execute("select * from %s where %sID &gt; %d" % (table, table[:-1], maxID))
    rows = cursor.fetchall()
    dic = TableDic
    for row in rows:
        dic[row[1]] = row[0]
    return dic


def Dic2List(ListofDicts):
    if len(ListofDicts) > 0:
        fields = ListofDicts[0].keys()
        ll = []
        for dic in ListofDicts:
            dictlist = []
            for key, value in dic.iteritems():
                dictlist.append(value)
            ll.append(dictlist)
    else:
        fields = []
        ll = []
    return (fields, ll)


def enfields(fields):
    tempfields = []
    for field in fields:
        tempfields.append('[%s]' % field)
    return "(" + ",".join(tempfields) + ")"


def insertListDic(ListofDicts, table_name, fieldsOverRide, cursor):
    # must get only one table
    (fields, datas) = Dic2List(ListofDicts)
    if fieldsOverRide != "" and fieldsOverRide is not None:
        fields = fieldsOverRide
    for data in enlist(datas):
        #    print ("Insert into %s %s Values %s" % (table_name, enfields(fields), data))
        cursor.execute("Insert into %s %s Values %s" % (table_name, enfields(fields), data))
    cursor.commit()


def CreateDicForTypesTable(table, cursor):
    cursor.execute("select * from %s" % table)
    rows = cursor.fetchall()
    dic = {}
    for row in rows:
        dic[row[1]] = row[0]
    return dic


def updateTypeDic(TypeDic, table_name, CodeName, dataset, cursor):
    if len(dataset) > 0:
        dataset -= set(TypeDic)
        if len(dataset) > 0:
            cursor.execute("select isnull(max(%s),0) as MaxID from %s" % (CodeName, table_name))
            maxID = cursor.fetchone()[0]
            for data in enlist(dataset):
                cursor.execute("Insert into %s (Name) Values %s" % (table_name, data))
                cursor.commit()
            cursor.execute("select * from %s where %s &gt; %d" % (table_name, CodeName, maxID))
            rows = cursor.fetchall()
            for row in rows:
                TypeDic[row[1]] = row[0]
    return TypeDic


def writetoDB(datalist, table_name, fieldsnames, cursor):
    for data in enlist(datalist):
        cursor.execute("Insert into %s %s Values %s" % (table_name, enfields(fieldsnames), data), multi=True)
    cursor.commit()
