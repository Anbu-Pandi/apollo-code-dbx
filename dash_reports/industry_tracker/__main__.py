import logging


def main():
    from common.utils.envsetup import init_app_using_parser, get_base_parser

    parser = get_base_parser('industry_tracker_refresh', log_file='industry_tracker_refresh')
    parser.add_argument('--industry', default=None,
                        help='industry key to refresh (omit to refresh all configured industries)')
    parser.add_argument('--skip-spark-init', action='store_true',
                        help='skip sparkmanager.init() (use only if already initialized in this process)')
    parser.add_argument('--skip-argus-html', action='store_true',
                        help='skip writing HTML/Consumer_Tracker_Argus.html')
    parser.add_argument('--skip-earnings-calls', action='store_true',
                        help='skip earnings_calls.parquet refresh from FactSet Vector API')
    parser.add_argument('--earnings-days-back', type=int, default=30,
                        help='lookback window (days) for earnings call discovery')
    args = init_app_using_parser(parser)

    if not args.skip_spark_init:
        from data_access.spark.api import sparkmanager
        sparkmanager.init()

    from dash_reports.industry_tracker.constants import get_cache_dir
    from dash_reports.industry_tracker.data import data_loader
    from dash_reports.industry_tracker.data.universe import INDUSTRY_UNIVERSE, get_industry

    targets = [args.industry] if args.industry else list(INDUSTRY_UNIVERSE.keys())
    logging.info('=' * 60)
    logging.info('INDUSTRY TRACKER REFRESH')
    logging.info('Industries: %s', targets)
    logging.info('=' * 60)

    for key in targets:
        ind = get_industry(key)
        if ind is None:
            logging.error('Unknown industry: %s. Available: %s', key, list(INDUSTRY_UNIVERSE.keys()))
            continue
        cache_dir = get_cache_dir(key)
        logging.info('--- %s (%s) -> %s', key, ind.display_name, cache_dir)
        result = data_loader.refresh_industry_data(ind)
        logging.info('Done: %s', result)
        if not args.skip_earnings_calls:
            ec_result = data_loader.refresh_earnings_calls(ind, days_back=args.earnings_days_back)
            logging.info('Earnings calls: %s', ec_result)
        if key == 'consumer' and not args.skip_argus_html:
            from dash_reports.industry_tracker.html_render.argus_report import generate_consumer_argus_html
            generate_consumer_argus_html()

    logging.info('=' * 60)
    logging.info('REFRESH COMPLETE')
    logging.info('=' * 60)


if __name__ == '__main__':
    main()
