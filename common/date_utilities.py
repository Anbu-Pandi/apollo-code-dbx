'''
Created on Sep 1, 2017

@author: yma
'''
import datetime
from datetime import timedelta

import pandas as pd
from pandas.tseries.offsets import Day, BDay

from common.utils import dtutil


class DateTimeUtilities(object):
    @staticmethod
    def getUSHolidays(years):
        dts = DateTimeUtilities.getAllTradingHolidays()
        dts = [d for d in dts if d.year in years]
        return dts

    @staticmethod
    def dateToStr(curDdate, formatStr):
        return curDdate.strftime(formatStr)

    @staticmethod
    def strToDate(dateStr, formatStr):
        tgtDate = datetime.datetime.strptime(dateStr, formatStr).date()
        return tgtDate

    @staticmethod
    def genWeeklyDate(startDate, endDate):
        dateList = [startDate + timedelta(days=x) for x in range(0, (endDate - startDate).days + 1)]
        dateList = [d for d in dateList if d.weekday() == 4]

        return dateList

    @staticmethod
    def isWeekend(asofdate):
        return asofdate.weekday() > 4

    @staticmethod
    def genMonthEndDate(startDate, endDate):
        start_month = startDate.month
        end_months = (endDate.year - startDate.year) * 12 + endDate.month

        monthEndList = []
        for month in range(start_month + 1, end_months + 2):
            # Get years in the date range, add it to the start date's year
            year = (month - 1) / 12 + startDate.year
            month = (month - 1) % 12 + 1

            mDate = datetime.datetime(year, month, 1) - timedelta(days=1)
            monthEndList.append(mDate.date())

        return monthEndList

    @staticmethod
    def genMonthEndBusiDate(startDate, endDate):
        dateList = DateTimeUtilities.genMonthEndDate(startDate, endDate)
        us_holidays = DateTimeUtilities.getTradingHolidays(startDate, endDate)

        busiDateList = []
        for curDate in dateList:
            while curDate in us_holidays or DateTimeUtilities.isWeekend(curDate):
                curDate = (curDate + Day(-1)).date()

            busiDateList.append(curDate)

        return busiDateList

    '''
    Generate Friday list
    If Friday is holiday use previous non busi day
    '''

    @staticmethod
    def genWeeklyBusiDate(startDate, endDate):
        dateList = DateTimeUtilities.genWeeklyDate(startDate, endDate)
        us_holidays = DateTimeUtilities.getTradingHolidays(startDate, endDate)

        busiDateList = []
        for curDate in dateList:
            while curDate in us_holidays:
                curDate = (curDate + Day(-1)).date()

            busiDateList.append(curDate)

        return busiDateList

    @staticmethod
    def getNextWeeklyBusiDate(asofdate):
        us_holidays = DateTimeUtilities.getTradingHolidays(asofdate, (asofdate + Day(14)).date())

        curDate = (asofdate + Day(1)).date()
        while curDate.weekday() != 4 or curDate in us_holidays:
            curDate = (curDate + Day(1)).date()

        while curDate in us_holidays:
            curDate = (curDate + Day(-1)).date()

        return curDate

    @staticmethod
    def getPrevBusiDate(asofdate, holidayList):
        nextBusiDate = asofdate + BDay(-1)
        nextBusiDate = nextBusiDate.date()

        while nextBusiDate in holidayList:
            nextBusiDate = nextBusiDate + BDay(-1)
            nextBusiDate = nextBusiDate.date()

        return nextBusiDate

    @staticmethod
    def getNextBusiDate(asofdate, holidayList):
        nextBusiDate = asofdate + BDay(1)
        nextBusiDate = nextBusiDate.date()

        while nextBusiDate in holidayList:
            nextBusiDate = nextBusiDate + BDay(1)

        return nextBusiDate.date()

    @staticmethod
    def genDailyBusiDate(startDate, endDate):
        dateList = pd.date_range(startDate, endDate).tolist()
        dateList = [d for d in dateList if d.weekday() != 5 and d.weekday() != 6]

        us_holidays = DateTimeUtilities.getTradingHolidays(startDate, endDate)

        busiDateList = []
        for curDate in dateList:
            if curDate.date() not in us_holidays:
                busiDateList.append(curDate.date())

        return busiDateList

    @staticmethod
    def getLastFriday(asofdate):
        lastFriday = (asofdate - datetime.timedelta(days=asofdate.weekday())
                      + datetime.timedelta(days=4, weeks=-1))

        return lastFriday

    @staticmethod
    def getPreviousWeeklyClose(asofdate, holidayList):
        lastFriday = (asofdate - datetime.timedelta(days=asofdate.weekday())
                      + datetime.timedelta(days=4, weeks=-1))

        while lastFriday in holidayList:
            lastFriday = (lastFriday + Day(-1)).date()

        return lastFriday

    @staticmethod
    def getAllTradingHolidays():
        dateList = dtutil.US_CALENDAR.holidays
        dateList = pd.to_datetime(dateList)
        dateList = [d.date() for d in dateList]
        return dateList

    @staticmethod
    def getTradingHolidays(startDate, endDate):
        return DateTimeUtilities.getAllTradingHolidays()

    @staticmethod
    def getPrevMonthEnd(asofdate):
        curMonthStart = datetime.datetime(asofdate.year, asofdate.month, 1)
        prevMonthEnd = datetime.datetime(curMonthStart.year, curMonthStart.month, 1) - Day(1)

        return prevMonthEnd.date()

    @staticmethod
    def getPrevMonthEndBusi(asofdate, tradingHolidays):
        curMonthStart = datetime.datetime(asofdate.year, asofdate.month, 1)
        prevMonthEnd = datetime.datetime(curMonthStart.year, curMonthStart.month, 1) - Day(1)
        prevMonthEnd = prevMonthEnd.date()

        while prevMonthEnd in tradingHolidays or DateTimeUtilities.isWeekend(prevMonthEnd):
            prevMonthEnd = (prevMonthEnd + Day(-1)).date()

        return prevMonthEnd

    @staticmethod
    def getPrevQuarterEnd(asofdate):
        qEnd = None

        if asofdate.month < 4:
            qEnd = datetime.date(asofdate.year - 1, 12, 31)
        elif asofdate.month < 7:
            qEnd = datetime.date(asofdate.year, 3, 31)
        elif asofdate.month < 10:
            qEnd = datetime.date(asofdate.year, 6, 30)
        else:
            qEnd = datetime.date(asofdate.year, 9, 30)

        return qEnd

    @staticmethod
    def getPrevYearEnd(asofdate):
        yEnd = datetime.datetime(asofdate.year - 1, 12, 31).date()

        return yEnd

    @staticmethod
    def todatestr(dt, format='%Y%m%d'):
        if isinstance(dt, (str, int, datetime.date, datetime.datetime)):
            return pd.to_datetime(str(dt)).strftime(format)
        return dt

    @staticmethod
    def todatetime(dt):
        if isinstance(dt, (str, int, datetime.date, datetime.datetime)):
            return pd.to_datetime(str(dt))
        return dt

    @staticmethod
    def todate(dt):
        if isinstance(dt, (str, int, datetime.date, datetime.datetime)):
            return pd.to_datetime(str(dt)).date()
        return dt


if __name__ == '__main__':
    import logging
    from common.utils import dtutil, envsetup
    envsetup.init_app_using_parser()

    for dt in dtutil.daterange('2025-04-01', '2025-04-27'):
        logging.info(f"Last Friday: {DateTimeUtilities.getLastFriday(dtutil.todate(dt))}, End of Week: {dtutil.eoweek(dtutil.dateadd(dt, '-1w'), use_bday=True)}")

