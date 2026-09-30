import calendar
import datetime
import logging
import re
import time

import numpy as np
import pandas as pd
import pandas_market_calendars as mcal
from dateutil import relativedelta
from dateutil.relativedelta import TH
from pandas.tseries.holiday import AbstractHolidayCalendar, Holiday, USThanksgivingDay

eq_calendar = mcal.get_calendar('NYSE')
bond_calendar = mcal.get_calendar('SIFMA_US')

from pandas.tseries.offsets import BDay, Day

DATE_STRING_IDS = {
    'k': 'k',
    'b': 'b',
    'd': 'days',
    'w': 'weeks',
    'm': 'months',
    'y': 'years'
}

BlackFriday = Holiday('BlackFriday', month=11, day=1, offset=[pd.DateOffset(weekday=TH(4)), Day(1)])

class BlackFridayCalendar(AbstractHolidayCalendar):
    rules = [BlackFriday]

_black_friday_cal = BlackFridayCalendar()
_black_fridays = _black_friday_cal.holidays(start='1950-01-01', end='2150-12-31', return_name=False)

US_CALENDAR = pd.offsets.CustomBusinessDay(holidays=eq_calendar.holidays().holidays + bond_calendar.holidays().holidays + tuple(_black_fridays))

REL_DATE_PATTERN = f"([\-\+]?)(\d+)([{''.join(DATE_STRING_IDS.keys())}])"


def todatestr(dt):
    dt = todate(dt)
    if isinstance(dt, datetime.date):
        return dt.strftime('%Y%m%d')
    return dt


def todate(dt):
    if isinstance(dt, (str, int, datetime.date, datetime.datetime, np.datetime64)):
        return pd.to_datetime(str(dt)).date()
    return dt


def dateadd(dt, offset):
    retdt = None
    if dt is not None:
        retdt = todate(dt)
        dtoffsetparts = _parse_dtoffset(offset)
        for dtoffsetpart in dtoffsetparts:
            dtoffset = _get_reldelta_args(*dtoffsetpart)
            retdt = retdt + dtoffset
        retdt = todate(retdt)
    return retdt


def isreldate(dt):
    return re.match(REL_DATE_PATTERN, str(dt).lower())


def extract_date_from_text(text, default_date=None):
    """
    Extracts the first matching date from a text string.
    Supported formats include: '12 Jan 2024', 'Jan 12 2024', '2024-01-12', '01/12/2024'.
    Returns default_date if no match is found.
    """
    text_str = str(text or "")
    if not text_str:
        return default_date

    date_patterns = (
        (
            r"\b\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{4}\b",
            ["%d %B %Y", "%d %b %Y"],
        ),
        (
            r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{1,2},?\s+\d{4}\b",
            ["%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y"],
        ),
        (r"\b\d{4}-\d{2}-\d{2}\b", ["%Y-%m-%d"]),
        (r"\b\d{1,2}/\d{1,2}/\d{4}\b", ["%m/%d/%Y", "%d/%m/%Y"]),
    )
    for pattern, formats in date_patterns:
        for match in re.findall(pattern, text_str, flags=re.IGNORECASE):
            candidate = re.sub(r"\s+", " ", str(match or "").replace(",", ", ")).strip()
            candidate = re.sub(r",\s+", ", ", candidate)
            for fmt in formats:
                try:
                    return datetime.datetime.strptime(candidate, fmt)
                except Exception:
                    continue
    return default_date


def parse_yyyymmdd_date(value, default_date=None):
    """
    Parses a string containing an 8-digit YYYYMMDD date.
    Returns default_date if parsing fails.
    """
    candidate = re.sub(r"[^0-9]", "", str(value or ""))
    if len(candidate) != 8:
        return default_date
    try:
        return datetime.datetime.strptime(candidate, "%Y%m%d")
    except Exception:
        return default_date


def adjustdate(dt, direction='backward'):
    dt = todate(dt)
    if isholiday(dt):
        if direction == 'backward':
            dt = dateadd(dt, '-1b')
        else:
            dt = dateadd(dt, '1b')
    return dt


def eoweek(dt, use_bday=True):
    dt = todate(dt)
    if dt.isoweekday() > 5:
        dt = dt + relativedelta.relativedelta(weekday=relativedelta.FR(-1))
    elif dt.isoweekday() < 5:
        dt = dt + relativedelta.relativedelta(weekday=relativedelta.FR(1))
    if use_bday:
        dt = adjustdate(dt, direction='backward')
    return dt


def boweek(dt, use_bday=True):
    dt = todate(dt)
    if dt.isoweekday() > 1:
        dt = dt + relativedelta.relativedelta(weekday=relativedelta.MO(-1))
    if use_bday:
        dt = adjustdate(dt, direction='forward')
    return dt


def eomonth(dt, use_bday=True):
    dt = todate(dt)
    dt = dt.replace(day=calendar.monthrange(dt.year, dt.month)[1])
    if use_bday:
        dt = adjustdate(dt)
    return dt


def bomonth(dt, use_bday=True):
    dt = todate(dt)
    dt = dt.replace(day=1)
    if use_bday:
        dt = adjustdate(dt, direction='forward')
    return dt


def eoqtr(dt, use_bday=True):
    dt = todate(dt)
    qtr_start_month = 3 * ((dt.month - 1) // 3) + 3
    dt = dt.replace(day=calendar.monthrange(dt.year, qtr_start_month)[1], month=qtr_start_month)

    if use_bday:
        dt = adjustdate(dt)
    return dt


def boqtr(dt, use_bday=True):
    dt = todate(dt)
    qtr_start_month = 3 * ((dt.month - 1) // 3) + 1
    dt = dt.replace(day=1, month=qtr_start_month)
    if use_bday:
        dt = adjustdate(dt, direction='forward')
    return dt


def eoyear(dt, use_bday=True):
    dt = todate(dt)
    dt = dt.replace(month=12, day=31)
    if use_bday:
        dt = adjustdate(dt)
    return dt


def boyear(dt, use_bday=True):
    dt = todate(dt)
    dt = dt.replace(day=1, month=1)
    if use_bday:
        dt = adjustdate(dt, direction='forward')
    return dt


def isholiday(dt):
    dt = todate(dt)
    return (dt.isoweekday() > 5) or (dt in US_CALENDAR.holidays)


def today(use_bday=False):
    dt = datetime.datetime.today().date()
    if use_bday:
        dt = adjustdate(dt, direction='backward')
    return dt


def _parse_dtoffset(dtoffset):
    dtoffset = dtoffset.replace(' ', '').lower()
    assert isreldate(dtoffset), f'incorrect offset inputs: {dtoffset}'
    return re.findall(REL_DATE_PATTERN, dtoffset)


def _get_reldelta_args(multpart, intpart, stringpart):
    mult = -1 if multpart == '-' else 1
    if DATE_STRING_IDS[stringpart] == 'b':
        return mult * int(intpart) * US_CALENDAR
    elif DATE_STRING_IDS[stringpart] == 'k':
        return BDay(mult * int(intpart))
    else:
        kwargs = {DATE_STRING_IDS[stringpart]: int(intpart)}
        return mult * relativedelta.relativedelta(**kwargs)


def daterange(fromdt, todt, step='1b', use_bday=True, direction='forward', adj_direction='backward'):
    # Convert to date only once
    fromdt = todate(fromdt)
    todt = todate(todt)

    # Return immediately if fromdt and todt are equal
    if fromdt == todt:
        return [fromdt]  # Return a list containing the single date

    # Use Python's built-in string methods
    direction = direction.lower()
    if direction not in ['forward', 'backward']:
        raise ValueError("direction must be 'forward' or 'backward'")

    # Optimize the step adjustment based on direction
    step = step.lstrip('-') if direction == 'forward' else '-' + step.lstrip('-')

    # Determine start and end dates based on direction
    dt = max(fromdt, todt) if direction == 'backward' else min(fromdt, todt)
    end_cond = max if direction == 'backward' else min

    # Predefine functions to avoid lookups in the loop
    _adj_date = adjustdate if use_bday else lambda x, y: x
    step_func_map = {'bow': boweek, 'eow': eoweek, 'bom': bomonth, 'eom': eomonth, 'boy': boyear, 'eoy': eoyear}

    i = 0
    dates = []  # Initialize an empty list to collect dates
    while True:
        if i != 0:
            if step.lower() in step_func_map:
                dt = step_func_map[step.lower()](dt, use_bday=False)
                dt = dateadd(dt, ('-' if direction == 'backward' else '') + '1' + step[-1])
            else:
                dt = dateadd(dt, step)

        # Break out of the loop if the condition is met
        if end_cond(dt, todt) != dt:
            break

        dates.append(_adj_date(dt, adj_direction))  # Collect each date
        i += 1

    return dates  # Return the list of collected dates


def cdsdate_pre_sep2015(dt, tenor='0d'):
    dt = todate(dt)
    mar_roll = todate(f'{dt.year}0320')
    jun_roll = todate(f'{dt.year}0620')
    sep_roll = todate(f'{dt.year}0920')
    dec_roll = todate(f'{dt.year}1220')
    prev_roll = dateadd(dec_roll, '-1y')
    if mar_roll <= dt and dt < jun_roll:
        prev_roll = mar_roll
    elif jun_roll <= dt and dt < sep_roll:
        prev_roll = jun_roll
    elif sep_roll <= dt and dt < dec_roll:
        prev_roll = sep_roll
    elif dec_roll <= dt and dt < dateadd(mar_roll, '1y'):
        prev_roll = dec_roll

    retdt = dateadd(prev_roll, tenor)
    return adjustdate(retdt, direction='forward')


def cdsdate(dt, tenor='0d'):
    dt = todate(dt)
    if dt < todate('20150920'):
        return cdsdate_pre_sep2015(dt, tenor)

    mar_roll = todate(f'{dt.year}0320')
    sep_roll = todate(f'{dt.year}0920')

    prev_roll = dateadd(sep_roll, '-1y')
    if mar_roll <= dt and dt < sep_roll:
        prev_roll = mar_roll
    elif sep_roll <= dt and dt < dateadd(mar_roll, '1y'):
        prev_roll = sep_roll

    retdt = dateadd(prev_roll, tenor + '3m')
    return adjustdate(retdt, direction='forward')


def cdsoptiondate(dt, tenor='0d'):
    next_dt = dateadd(dt, tenor)
    next_dt = nth_weekday_of_month(next_dt.month, next_dt.year, 3, 2)
    return adjustdate(next_dt, direction='forward')


def nth_weekday_of_month(month, year, nth_week, week_day):
    the_date = datetime.datetime(year, month, 1)
    temp = the_date.replace(day=1)
    adj = (week_day - temp.weekday()) % 7
    temp += datetime.timedelta(days=adj)
    temp += datetime.timedelta(weeks=nth_week - 1)

    return todate(temp)


def immdate(dt, tenor='0d'):
    next_date = dateadd(dt, tenor)

    y = next_date.year
    m = next_date.month
    d = next_date.day

    y_imm = y

    if m == 12 and d >= nth_weekday_of_month(m, y, 3, 2).day:
        m_imm = 3
        y_imm = y + 1
    elif m == 10 or m == 11 or m == 12:
        m_imm = 12
    elif m == 9 and d >= nth_weekday_of_month(m, y, 3, 2).day:
        m_imm = 12
    elif m == 7 or m == 8 or m == 9:
        m_imm = 9
    elif m == 6 and d >= nth_weekday_of_month(m, y, 3, 2).day:
        m_imm = 9
    elif m == 4 or m == 5 or m == 6:
        m_imm = 6
    elif m == 3 and d >= nth_weekday_of_month(m, y, 3, 2).day:
        m_imm = 6
    elif m == 1 or m == 2 or m == 3:
        m_imm = 3

    d_imm = nth_weekday_of_month(m_imm, y_imm, 3, 2).day

    imm_date = todate(f"{y_imm}-{m_imm}-{d_imm}")
    return imm_date


def calendar_qtr(dt):
    dt = dateadd(eomonth(dateadd(dt, '-3m'), use_bday=False), '1d')
    qtr = f'{str(dt.year)} Q{str(pd.Timestamp(dt).quarter)}'
    return qtr


def ds_tz_convert(ds, from_tz='UTC', to_tz='America/New_York', has_tz=False):
    if has_tz:
        return ds.map(lambda x: tz_convert(x, from_tz, to_tz, has_tz))

    return pd.to_datetime(ds.astype(str).str[:19], errors='coerce') \
        .dt.tz_localize(from_tz) \
        .dt.tz_convert(to_tz) \
        .dt.tz_localize(None)


def tz_convert(dt, from_tz='UTC', to_tz='America/New_York', has_tz=False):
    if has_tz:
        return pd.to_datetime(str(dt), errors='coerce') \
            .tz_convert(to_tz).tz_localize(None)
    return pd.to_datetime(str(dt)[:19], errors='coerce') \
        .tz_localize(from_tz).tz_convert(to_tz).tz_localize(None)


def remove_tz(df):
    for column in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[column]) and df[column].dt.tz is not None:
            df[column] = pd.to_datetime(df[column].astype(str).str[:19], errors='coerce')

    return df


class Timer(object):
    def __init__(self, name=None):
        self.name = name
        self.elapsed_ms = 0.0

    def __enter__(self):
        self.tstart = time.time()
        return self

    def __exit__(self, type, value, traceback):
        self.elapsed_ms = (time.time() - self.tstart) * 1000
        elapsed_s = self.elapsed_ms / 1000
        if self.name:
            logging.info('[%s] elapsed: %.3fs' % (self.name, elapsed_s))
        else:
            logging.info('elapsed: %.3fs' % elapsed_s)


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()

    dt = '20250418'
    logging.info(f"is holiday:{dt} {isholiday(dt)}")

    dt = '20221230'
    dts = daterange('20231118', '20231120', step='1d', use_bday=False)
    logging.info(f'dts: {list(dts)}')

    dts = daterange(dateadd(dt, '-5b'), dt, step='1d')
    logging.info(f'dts: {list(dts)}')

    logging.info(f"is holiday:{dt} {isholiday(dt)}")
    logging.info(f"is holiday -1d:{dateadd(dt, '-1d')} {isholiday(dateadd(dt, '-1d'))}")

    logging.info(f'backward:{adjustdate(dt)}')
    logging.info(f"forward{adjustdate(dt, direction='forward')}")

    logging.info(f"eoweek:{eoweek(dt)}")
    logging.info(f"boweek:{boweek(dt)}")

    logging.info(f"eomonth:{eomonth(dt)}")
    logging.info(f"bomonth{bomonth(dt)}")

    logging.info(f"eoqtr:{eoqtr(dt)}")
    logging.info(f"boqtr{boqtr(dt)}")

    logging.info(f"eoyear{eoyear(dt)}")
    logging.info(f"boyear:{boyear(dt)}")
    logging.info(f"immdate:{immdate(dt, '5y')}")

    dt = '20230920'
    logging.info(f"cdsdate 0m:{cdsdate(dt, '0m')}")
    logging.info(f"cdsdate 3m:{cdsdate(dt, '3m')}")
    logging.info(f"cdsdate 6m:{cdsdate(dt, '6m')}")
    logging.info(f"cdsdate 9m:{cdsdate(dt, '9m')}")
    logging.info(f"cdsdate 1y:{cdsdate(dt, '1y')}")
    logging.info(f"cdsdate 2y:{cdsdate(dt, '2y')}")

    dt = '20150621'
    logging.info(f"cdsdate 3m:{cdsdate(dt, '3m')}")
    logging.info(f"cdsdate 6m:{cdsdate(dt, '6m')}")
    logging.info(f"cdsdate 9m:{cdsdate(dt, '9m')}")
    logging.info(f"cdsdate 1y:{cdsdate(dt, '1y')}")
    logging.info(f"cdsdate 2y:{cdsdate(dt, '2y')}")
