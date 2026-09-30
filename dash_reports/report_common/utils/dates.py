import datetime
import logging
import os

from dateutil.relativedelta import relativedelta, SU, MO, SA

from common.utils import dtutil


def get_prev_week(valuedt):
    enddt = valuedt + relativedelta(weekday=SU(-1))
    startdt = enddt + relativedelta(weekday=MO(-1))

    return (startdt, enddt)


def is_last_week_of_month(week_startdt, week_enddt):
    week_startdt = dtutil.adjustdate(week_startdt, direction='forward')
    week_enddt = dtutil.adjustdate(week_enddt, direction='forward')
    return week_startdt.month != week_enddt.month


def is_first_week_of_month(week_startdt, week_enddt):
    (prevwk_startdt, prevwk_enddt) = get_prev_week(week_startdt)
    return is_last_week_of_month(prevwk_startdt, prevwk_enddt)


def get_analysis_dates(valuedt, startdt=None, enddt=None):
    valuedt = dtutil.todate(valuedt)
    logging.info(f"getting analysis dates for value dt: {valuedt}, start dt: {startdt}, end dt: {enddt}")
    assert ((startdt is None and enddt is None) or not (startdt is None or enddt is None)), \
        "start date and end date need to be either None or dates!"
    if valuedt.weekday() in [SA, SU]:
        valuedt += relativedelta(weekday=MO(1))
    else:
        valuedt += relativedelta(weekday=MO(-1))
    valuedt = dtutil.adjustdate(valuedt, direction='forward')

    if (startdt is None and enddt is None):
        startdt, enddt = get_prev_week(valuedt)

    if is_last_week_of_month(startdt, enddt):
        startdt = dtutil.bomonth(startdt, use_bday=False)
        enddt = dtutil.eomonth(startdt, use_bday=False)

    if is_first_week_of_month(startdt, enddt):
        startdt = dtutil.bomonth(enddt, use_bday=False)

    logging.info(f"analysis dates calculated: start dt: {startdt}, end dt: {enddt}")
    return startdt, enddt


class ReportDates():
    @staticmethod
    def get_dt_folder(base_folder, dt=None):
        if dt is None:
            dt = datetime.datetime.today()
            dt = dtutil.todate(dt)

            _, dt = get_analysis_dates(dt)
            if dt != dtutil.eomonth(dt, use_bday=False):
                dt = dtutil.dateadd(dt, '1b')

        return os.path.join(base_folder, dtutil.todatestr(dt))

    @staticmethod
    def get_report_folder_dates(folder_date=None, start_date=None, end_date=None):
        is_attrib_dt_overriden = not (start_date is None or end_date is None)
        folder_date = dtutil.todate(folder_date or datetime.datetime.today())
        if not start_date is None:
            start_date = dtutil.todate(start_date)
        if not end_date is None:
            end_date = dtutil.todate(end_date)

        if not is_attrib_dt_overriden:
            attrib_start_date, attrib_end_date = get_analysis_dates(folder_date, startdt=start_date, enddt=end_date)
        else:
            attrib_start_date, attrib_end_date = start_date, end_date

        prev_risk_date = attrib_end_date + relativedelta(weekday=SU(-2))
        if is_attrib_dt_overriden or attrib_start_date == dtutil.bomonth(attrib_end_date, use_bday=False):
            prev_risk_date = dtutil.dateadd(attrib_start_date, '-1d')

        year_start_date = dtutil.boyear(attrib_start_date, use_bday=False)
        risk_year_start_date = dtutil.boyear(dtutil.dateadd(dtutil.today(), '-2y'), use_bday=False)

        return (attrib_start_date, attrib_end_date, prev_risk_date, year_start_date, risk_year_start_date)


reportdates = ReportDates()

if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()
    logging.info(reportdates.get_report_folder_dates('20210510'))
    logging.info(reportdates.get_report_folder_dates('20210510'))
    logging.info(reportdates.get_dt_folder('F', '20210510'))
