import logging
import os

import jaydebeapi

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend


def get_jdbc_connection(server, databasename, connectiontype=None, username=None, password=None):
    urlparts = [server, 'databaseName={}'.format(databasename)]
    connection_type = connectiontype or ''
    if connection_type.lower() == 'trusted_connection':
        authargs = None
        urlparts += ['integratedSecurity=true']
        jdbcdriverpath = f'{os.environ["JDBC_DRIVERS"]}/sqljdbc42.jar' \
            if ('JDBC_DRIVERS' in os.environ) else None
    elif connection_type.lower() == 'active_directory_integrated':
        authargs = None
        urlparts += ['authentication=ActiveDirectoryIntegrated']
        jdbcdriverpath = f'{os.environ["JDBC_DRIVERS"]}/mssql-jdbc-9.2.1.jre8.jar' \
            if ('JDBC_DRIVERS' in os.environ) else None
    elif connection_type.lower() == '':
        authargs = [username, password]
        jdbcdriverpath = f'{os.environ["JDBC_DRIVERS"]}/sqljdbc42.jar' \
            if ('JDBC_DRIVERS' in os.environ) else None
    else:
        raise Exception(f'Please check connection type: {connection_type} for jdbc driver')

    urlparts += ['/CHARSET=UTF8']
    url = 'jdbc:sqlserver://' + ';'.join(urlparts)

    logging.info(
        'Opening JDBC connection server=%s database=%s authentication=%s',
        server,
        databasename,
        connectiontype or 'sql',
    )
    return jaydebeapi.connect("com.microsoft.sqlserver.jdbc.SQLServerDriver",
                              url,
                              authargs,
                              jdbcdriverpath)


def getdrivername(driver):
    driver = driver or 'odbc'
    driverdict = {'sql': 'SQL Server Native Client 11.0',
                  'odbc': 'ODBC Driver 17 for SQL Server'}
    if driver in driverdict:
        return driverdict[driver]


def _raise_conn_type_error(connection_type, driver):
    raise Exception(f'Please check authentication type: {connection_type} for {driver} driver')


def get_conndict(driver, server, database_name,
                 add_driver=True, auto_commit=True,
                 authentication_type=None, user=None, password=None, use_type=None):
    conndict = {}
    if use_type == 'connection_string':
        if add_driver:
            conndict['Driver'] = f"{getdrivername(driver)}"
        conndict['Server'] = server
        conndict['Database'] = database_name
        if auto_commit:
            conndict['AutoCommit'] = 'True'
        authentication_type = authentication_type or ''
        if authentication_type.lower() == 'trusted_connection':
            conndict['Trusted_Connection'] = 'Yes'
        elif authentication_type.lower() == 'active_directory_integrated':
            conndict['Authentication'] = 'ActiveDirectoryIntegrated'
        elif authentication_type.lower() == '':
            conndict['User Id'] = user
            conndict['Password'] = password
        else:
            _raise_conn_type_error(authentication_type, getdrivername(driver))
    else:
        if add_driver:
            conndict['driver'] = f"{getdrivername(driver)}"
        conndict['server'] = server
        conndict['database'] = database_name
        if auto_commit:
            conndict['autocommit'] = True
        authentication_type = authentication_type or ''
        if authentication_type.lower() == 'trusted_connection':
            conndict['trusted_connection'] = 'yes'
        elif authentication_type.lower() == 'active_directory_integrated':
            conndict['authentication'] = 'activedirectoryintegrated'
        elif authentication_type.lower() == '':
            conndict['uid'] = user
            conndict['pwd'] = password
        else:
            _raise_conn_type_error(authentication_type, getdrivername(driver))

    return conndict


def _prepare_snowflake_private_key(pem_str, passphrase=None):
    pem_bytes = pem_str.encode("utf-8")

    private_key = serialization.load_pem_private_key(
        pem_bytes,
        password=passphrase.encode() if passphrase else None,
        backend=default_backend(),
    )

    pkcs8_der = private_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    return pkcs8_der


def get_snowflake_dict(account, database, warehouse,
                       auto_commit=True,
                       application=None,
                       user=None, password=None, private_key=None):
    conndict = {}
    conndict['account'] = account
    conndict['database'] = database
    conndict['warehouse'] = warehouse
    if (private_key is None) and (application is not None):
        conndict['user'] = user
        conndict['authenticator'] = application
        conndict['password'] = password
        conndict['application'] = application
    elif user is not None and private_key is not None:
        conndict['user'] = user
        conndict['private_key'] = _prepare_snowflake_private_key(private_key)
    elif user is not None and password is not None:
        conndict['user'] = user
        conndict['password'] = password
    else:
        _raise_conn_type_error(application, 'snowflake')

    return conndict


def get_databricks_dict(server, http_path, access_token, catalog):
    conndict = {}
    conndict['server_hostname'] = server
    conndict['http_path'] = http_path
    conndict['access_token'] = access_token
    conndict['catalog'] = catalog
    return conndict


def get_postgres_dict(dbname, user, password, host, port=5432, sslmode=None):
    conndict = {
        'dbname': dbname,
        'user': user,
        'password': password,
        'host': host,
        'port': port
    }

    if sslmode:
        conndict['sslmode'] = sslmode

    return conndict
