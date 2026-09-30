import logging
import threading
import time
from datetime import date
from functools import partial

import schedule

from common.utils import dtutil
from common.utils import jobutil
from common.utils.envsetup import init_app_using_parser, get_base_parser
from data_access.coordination import DeltaLeaseStore
from data_access.storage import SharedFrameCache
from dash_reports.analyst_questionnaire.data.caching import refresh_industry_wide_data, refresh_questions, \
    refresh_issuers_coverage
from dash_reports.cds_cash_basis.data.caching import refresh_all_index_caches as refresh_cds_cash_basis_cache
from dash_reports.cdx_vol_tracker.config import CACHE_SUBDIR as CDX_VOL_CACHE_SUBDIR
from dash_reports.fx_vol_tracker.config import CACHE_SUBDIR as FX_VOL_CACHE_SUBDIR
from dash_reports.rate_vol_tracker.config import CACHE_SUBDIR as RATE_VOL_CACHE_SUBDIR
from dash_reports.issuer_debt_in_index.data.bond_rv import refresh_issuer_curves_history
from dash_reports.rate_vol_tracker.data.vol_data_loader import refresh_all_ccy_caches as refresh_rate_vol_cache
from dash_reports.cdx_vol_tracker.data.cdx_data_loader import refresh_all_ticker_caches as refresh_cdx_vol_cache
from dash_reports.fx_vol_tracker.data.fx_data_loader import refresh_all_ccy_caches as refresh_fx_vol_cache
from dash_reports.cdx_options.data.data_loader import refresh_cdx_options_cache
from dash_reports.industry_tracker.data.data_loader import (
    refresh_all_industries as refresh_industry_tracker_cache,
    refresh_earnings_calls as refresh_industry_tracker_earnings,
)
from dash_reports.issuers_info.data.caching import refresh_sec_master
from dash_reports.report_common.data.data_model import datastore
from data_access.spark.api import sparkmanager

TASK_LEASE_SECONDS = 12 * 60 * 60
_lease_store = None
_lease_lock = threading.Lock()


def _coordination():
    global _lease_store
    if _lease_store is None:
        with _lease_lock:
            if _lease_store is None:
                _lease_store = DeltaLeaseStore.local()
    return _lease_store


def getargparser():
    parser = get_base_parser('beta attribution tasks', log_file='beta_attribution_tasks')
    parser.add_argument('--date', required=False, default=None,
                        help='folder date where report data files are saved')
    parser.add_argument('--startdate', required=False, default=None,
                        help='attribution start date')
    parser.add_argument('--enddate', required=False, default=None,
                        help='attribution end date')
    parser.add_argument('--runnow', default=False, action="store_true",
                        help='run jobs now')
    parser.add_argument('--tag', required=False, default=None,
                        help='run tagged jobs; csv')
    return parser


def update_issuer_debt_df():
    dt = dtutil.dateadd(dtutil.today(), '-1b')
    datastore.paraminit()
    datastore.get_issuer_debt_df(dt)


def update_index_caches():
    datastore.paraminit()
    datastore.indexdatainit()


def update_issuer_universe():
    refresh_issuers_coverage()


def update_analyst_ques_caches():
    refresh_questions()
    time.sleep(1 * 60)
    refresh_industry_wide_data()


def update_sec_master_data():
    refresh_sec_master()


def update_issuer_curves_history():
    datastore.paraminit()
    refresh_issuer_curves_history()


def update_rate_vol_data():
    refresh_rate_vol_cache()


def update_cdx_vol_data():
    refresh_cdx_vol_cache()


def update_fx_vol_data():
    refresh_fx_vol_cache()


def update_cdx_options_data():
    refresh_cdx_options_cache(full_build=False)


def update_cdx_options_data_full():
    refresh_cdx_options_cache(full_build=True)


def update_industry_tracker_data():
    refresh_industry_tracker_cache()
    refresh_industry_tracker_earnings('consumer')
    from dash_reports.industry_tracker.html_render.argus_report import generate_consumer_argus_html
    generate_consumer_argus_html()


def update_cds_cash_basis_data():
    refresh_cds_cash_basis_cache()


def purge_shared_frame_cache():
    cutoff = dtutil.dateadd(dtutil.today(use_bday=False), '-14d')
    cache = SharedFrameCache.local()
    for namespace in (
        RATE_VOL_CACHE_SUBDIR,
        CDX_VOL_CACHE_SUBDIR,
        FX_VOL_CACHE_SUBDIR,
        "cds_cash_basis",
        "risk_query",
        "portfolio_stats",
    ):
        cache.purge_before(namespace, cutoff)
    cache.purge_older_than("sponsor_study", 30 * 24 * 60 * 60)
    cache.purge_older_than("cdx_task_state", 24 * 60 * 60)


def _run_leased_job(job, jobtag, store=None, run_date=None):
    store = store or _coordination()
    run_key = str(run_date or date.today())
    lease = store.acquire(
        f"dash_reports:task:{jobtag}",
        TASK_LEASE_SECONDS,
        completed_key=run_key,
    )
    if lease is None:
        logging.info("skipping %s: another node owns %s", jobtag, run_key)
        return False
    try:
        logging.info(f'starting job: {jobtag}')
        job()
    except Exception as exc:
        store.fail(lease, str(exc))
        raise
    if not store.complete(lease, run_key):
        raise RuntimeError(f"lost lease while completing {jobtag}")
    logging.info(f'completed job: {jobtag}')
    return True


def add_schedule(jobber, scheduleobj, timeat, jobtag, job):
    logging.info(f'adding {jobtag} for time: {timeat}')

    def exec_job(job, jobtag):
        jobber.submitjob_async(_run_leased_job, params=[(job, jobtag)])

    scheduleobj.at(timeat).do(exec_job, job, jobtag).tag(jobtag)


def add_schedules(jobber, args):
    add_schedule(jobber, schedule.every().day, "03:00", 'sec_master', update_sec_master_data)
    add_schedule(jobber, schedule.every().day, "06:15", 'index', update_index_caches)
    add_schedule(jobber, schedule.every().day, "11:59", 'issuer_curves', update_issuer_curves_history)
    add_schedule(jobber, schedule.every().day, "07:00", 'issuer_debt', update_issuer_debt_df)
    add_schedule(jobber, schedule.every().day, "07:00", 'rate_vol', update_rate_vol_data)
    add_schedule(jobber, schedule.every().day, "07:00", 'cdx_vol', update_cdx_vol_data)
    add_schedule(jobber, schedule.every().day, "07:00", 'fx_vol', update_fx_vol_data)
    add_schedule(jobber, schedule.every().day, "06:30", 'cdx_options', update_cdx_options_data)
    add_schedule(jobber, schedule.every().day, "09:00", 'cdx_options_full', update_cdx_options_data_full)
    add_schedule(jobber, schedule.every().day, "09:00", 'industry_tracker', update_industry_tracker_data)
    add_schedule(jobber, schedule.every().day, "07:30", 'cds_cash_basis', update_cds_cash_basis_data)
    add_schedule(jobber, schedule.every().day, "05:30", 'shared_cache_cleanup', purge_shared_frame_cache)
    add_schedule(jobber, schedule.every().day, "21:00", 'issuer_univ', update_issuer_universe)


def run_threaded(job_func):
    job_thread = threading.Thread(target=job_func)
    job_thread.start()


if __name__ == '__main__':
    argparser = getargparser()
    init_app_using_parser(argparser)

    sparkmanager.init()

    args = argparser.parse_args()
    jobber = jobutil.JobExecutor(exec_type='thread', max_workers=5)
    add_schedules(jobber, args)

    no_tag_jobs = [x for x in schedule.jobs if len(x.tags) == 0]
    no_tag_jobs = '\n'.join([str(x) for x in no_tag_jobs])
    assert len(no_tag_jobs) == 0, f"found jobs with no tags: \n{no_tag_jobs}"

    if args.runnow:
        run_tags = args.tag or ','.join([list(x.tags)[0] for x in schedule.jobs if len(x.tags) > 0])
        for tag in run_tags.split(','):
            for job in schedule.get_jobs(tag):
                job.run()

    sleeptime = 5 * 60
    while 1:
        logging.info('will run any pending tasks.')
        schedule.run_pending()
        jobber.async_flush()
        logging.info(f'sleeping for: {sleeptime} seconds')
        time.sleep(sleeptime)
