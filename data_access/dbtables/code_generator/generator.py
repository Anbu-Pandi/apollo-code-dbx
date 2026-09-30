import getpass
import logging
import os
from datetime import datetime

from common.utils.dfutil import tolist


def _convert_list_to_dict(lst):
    if isinstance(lst, dict):
        return lst
    return {x: x for x in tolist(lst)}


def generate(table, ids, outfolder, date_col=None, app=None):
    app = app or 'APP'
    templatefile = os.path.join(os.path.split(__file__)[0], '../code_generator/code_template.py')
    outfile = os.path.abspath(os.path.join(outfolder, f"{table.split('.')[1]}.py"))
    with open(templatefile, 'r') as f:
        table_string = f.read().replace('__table__', table).replace('APP as', f'{app} as')
        table_string = table_string.replace('_dbo.', '_')
        table_string = table_string.replace('__file__', __file__.replace('\\', '/'))
        table_string = table_string.replace('__date__', str(datetime.now()))
        table_string = table_string.replace('__user__', getpass.getuser())

        splitter = 'GET_SQL___id__ ='
        header_string = table_string.split(splitter)[0]
        id_string = splitter + table_string.split(splitter)[1]

        ids = _convert_list_to_dict(ids)
        id_string_list = []
        i = 0
        for id_name, id_columns in ids.items():
            id_columns = tolist(id_columns)
            idstr = id_string.replace("__id__ = '{}'", ' AND '.join([x + " = '{}'" for x in id_columns]))
            idstr = idstr.replace('(__id__, ', "(" + ", ".join(id_columns) + (", " if len(id_columns) > 0 else ""))
            idstr = idstr.replace("'__id__'", "[" + ", ".join(["'" + x + "'" for x in id_columns]) + "]")
            idstr = idstr.replace('__id__', id_name)
            idstr = idstr.replace('WHERE \nAND', 'WHERE')
            if i == 0:
                idstr = idstr.replace("'unique_bool'", 'True')
                i += 1
            else:
                idstr = idstr.replace("'unique_bool'", 'False')

            if date_col is None:
                idstr = idstr.replace(', fromdt, todt', '')
                idstr = idstr.replace(', fromdt=None, todt=None', '')
                idstr = idstr.replace("fromdt = fromdt or '0000-01-01'", '')
                idstr = idstr.replace("todt = todt or '9999-12-31'", '')
                idstr = idstr.replace("    \n", '')
                idstr = idstr.replace("\nAND __datecol__ BETWEEN '{}' AND '{}'", '')
                idstr = idstr.replace(" + ['__datecol__']", '')
                idstr = idstr.replace(", __datecol__", '')
            else:
                idstr = idstr.replace("__datecol__", date_col)

            id_string_list.append(idstr)

    replaced_table_string = '\n'.join([header_string] + id_string_list)
    logging.info(f'writing file for table:{table} {outfile}')
    with open(outfile, 'w') as f:
        f.write(replaced_table_string)


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()

    # outfolder = '../market_data/curves'
    # table = 'dbo.curves'
    # ids = {'curve_id': ['curve_id'], 'currency': ['currency'], 'curve_name': 'curve_name'}
    # date_col = None
    # generate(table, ids, outfolder, date_col=date_col, app='JPM_MARKETDATA_APP')
    #
    # outfolder = '../market_data/curves'
    # table = 'dbo.curve_data'
    # ids = {'curve_id': ['curve_id', 'tenor', 'fwd_tenor', 'source'], 'curve': ['curve_id', 'source']}
    # date_col = 'asof_date'
    # generate(table, ids, outfolder, date_col=date_col, app='JPM_MARKETDATA_APP')
    #
    # outfolder = '../market_data/queries'
    # table = 'dbo.curve_jpm_queries'
    # ids = ['curve_name', 'jpm_dq_query']
    # date_col = None
    # generate(table, ids, outfolder, date_col=date_col, app='JPM_MARKETDATA_APP')
    #
    # outfolder = '../market_data/vol'
    # table = 'dbo.irswaps'
    # ids = {'swap_name': 'swap_name', 'swap': ['currency', 'underlying_rate_tenor', 'swap_tenor', 'swap_type']}
    # date_col = None
    # generate(table, ids, outfolder, date_col=date_col, app='GS_MARKETDATA_APP')
    #
    # outfolder = '../market_data/vol'
    # table = 'dbo.irswaption_vol_curve_data'
    # ids = {'swaption': ['swap_name', 'option_tenor', 'rel_strike', 'source'], 'swap': ['swap_name', 'source']}
    # date_col = 'asof_date'
    # generate(table, ids, outfolder, date_col=date_col, app='GS_MARKETDATA_APP')
    #
    # outfolder = '../market_data/queries'
    # table = 'dbo.irswaption_vol_curve_gs_queries'
    # ids = {'swaption': 'curve_name', 'swap': 'swap_name', 'dataset': 'gs_dataset', 'asset_id': 'gs_asset_id'}
    # date_col = None
    # generate(table, ids, outfolder, date_col=date_col, app='GS_MARKETDATA_APP')
    #
    # outfolder = '../market_data/vol'
    # table = 'dbo.equity_index'
    # ids = {'equity_index': 'equity_index_id'}
    # date_col = None
    # generate(table, ids, outfolder, date_col=date_col, app='GS_MARKETDATA_APP')
    #
    # outfolder = '../market_data/vol'
    # table = 'dbo.equity_index_vol_data'
    # ids = {'equity_index_date': ['equity_index_id', 'option_tenor_date', 'relative_strike', 'strike_reference',
    #                              'expiry_type', 'source'],
    #        'equity_index_tenor': ['equity_index_id', 'option_tenor', 'relative_strike', 'strike_reference',
    #                               'expiry_type', 'source'],
    #        'equity_index': ['equity_index_id', 'expiry_type', 'strike_reference', 'source']}
    # date_col = 'asof_date'
    # generate(table, ids, outfolder, date_col=date_col, app='GS_MARKETDATA_APP')
    #
    # outfolder = '../market_data/queries'
    # table = 'dbo.equity_index_vol_gs_queries'
    # ids = {'equity_index': ['gs_dataset', 'equity_index_id'],
    #        'dataset': 'gs_dataset',
    #        'asset_id': 'gs_asset_id'}
    # date_col = None
    # generate(table, ids, outfolder, date_col=date_col, app='GS_MARKETDATA_APP')
    #
    # outfolder = '../market_data/vol'
    # table = 'dbo.equity_index_varswap_data'
    # ids = {'equity_index_date': ['equity_index_id', 'swap_tenor_date', 'expiry_type', 'source'],
    #        'equity_index_tenor': ['equity_index_id', 'swap_tenor', 'expiry_type', 'source'],
    #        'equity_index': ['equity_index_id', 'expiry_type', 'source']}
    # date_col = 'asof_date'
    # generate(table, ids, outfolder, date_col=date_col, app='GS_MARKETDATA_APP')
    #
    # outfolder = '../market_data/queries'
    # table = 'dbo.fx_gs_queries'
    # ids = {'fx': ['gs_dataset', 'ccy_pair'],
    #        'dataset': 'gs_dataset',
    #        'asset_id': 'gs_asset_id'}
    # date_col = None
    # generate(table, ids, outfolder, date_col=date_col, app='GS_MARKETDATA_APP')
    #
    # outfolder = '../market_data/fwd'
    # table = 'dbo.fx_forward_data'
    # ids = {'fx_date': ['ccy_pair', 'swap_tenor_date', 'source'],
    #        'fx': ['ccy_pair', 'source']}
    # date_col = 'asof_date'
    # generate(table, ids, outfolder, date_col=date_col, app='GS_MARKETDATA_APP')
    #
    # outfolder = '../market_data/vol'
    # table = 'dbo.fx_vol_data'
    # ids = {'fx_date': ['ccy_pair', 'option_tenor_date', 'source'],
    #        'fx': ['ccy_pair', 'source']}
    # date_col = 'asof_date'
    # generate(table, ids, outfolder, date_col=date_col, app='GS_MARKETDATA_APP')

    # outfolder = '../security_data'
    # table = 'dbo.bond_security_master'
    # ids = {'bbgid_latest': ['id_bb_global', 'is_latest'],
    #        'bbgid': ['id_bb_global'],
    #        'latest': ['is_latest'],
    #        'date': []}
    # date_col = 'knowledge_date'
    # generate(table, ids, outfolder, date_col=date_col, app='BOND_REFERENCEDATA_APP')
    #
    # outfolder = '../security_data'
    # table = 'dbo.bond_issuer_master'
    # ids = {'bbgid_latest': ['id_bb_company', 'is_latest'],
    #        'bbgid': ['id_bb_company'],
    #        'latest': ['is_latest'],
    #        'date': []}
    # date_col = 'knowledge_date'
    # generate(table, ids, outfolder, date_col=date_col, app='BOND_REFERENCEDATA_APP')


    outfolder = '../index_data'
    table = 'dbo.basket_constituents'
    ids = {'id': ['gs_asset_id', 'underlying_asset_id'],
           'ticker': ['ticker'],
           'gs_asset_id': ['gs_asset_id'],
           'underlying_asset_id': ['ticker', 'underlying_asset_id']}
    date_col = 'asof_date'
    generate(table, ids, outfolder, date_col=date_col, app='GS_MARKETDATA_APP')


    # outfolder = '../market_data/queries'
    # table = 'dbo.basket_constituents_gs_queries'
    # ids = {'ticker': ['gs_dataset', 'ticker'],
    #        'dataset': 'gs_dataset',
    #        'asset_id': 'gs_asset_id'}
    # date_col = None
    # generate(table, ids, outfolder, date_col=date_col, app='GS_MARKETDATA_APP')
