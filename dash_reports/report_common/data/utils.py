from dash_reports.report_common.data.config import FUND_DISPLAY_ORDER
import uuid

def get_ordered_fund_list(fundlist):
    fund_in_ordered_list = [f for f in FUND_DISPLAY_ORDER if f in fundlist]
    fund_not_in_ordered_list = sorted([f for f in fundlist if f not in fund_in_ordered_list])
    return fund_in_ordered_list + fund_not_in_ordered_list


def parsefunds(fund, fundlist):
    if fund is None:
        retval = []
    elif isinstance(fund, (str, float, int)):
        fund = str(fund)
        if fund == 'ALL':
            retval = fundlist
        elif fund in fundlist:
            retval = [fund]
        else:
            retval = []
    else:
        if 'ALL' in fund:
            retval = fundlist
        else:
            retval = [f for f in fund if f in fundlist]
    return get_ordered_fund_list(retval)


def get_uuid():
    return str(uuid.uuid4())