import logging
import traceback

from apo_config.config import config
from common.utils.envsetup import get_base_parser, init_app_using_parser
from dash_reports.report_common.data.data_model import datastore
from data_access.beta_attribution import APP


def get_dash_report_log_file():
    return 'beta_attribution_dash_report'


def getargparser(default_log_file=None):
    parser = get_base_parser('beta attribution dash report', log_file=default_log_file or get_dash_report_log_file())
    parser.add_argument('--date', required=False, default=None,
                        help='folder date where report data files are saved')
    parser.add_argument('--startdate', required=False, default=None,
                        help='attribution start date')
    parser.add_argument('--enddate', required=False, default=None,
                        help='attribution end date')
    parser.add_argument('--clearcache', default=False, action="store_true",
                        help='clear local cache to force read from network files')
    parser.add_argument('--fastload', default=True, action="store_true",
                        help='clear local cache to force read from network files')
    parser.add_argument('--port', required=False, default='8050',
                        help='port number for website. default: 8050')

    return parser


def prepare_app(is_debug=False):
    try:
        kwargs = {}
        if is_debug:
            kwargs['default_log_file'] = 'nolog'
        argparser = getargparser(**kwargs)
        args = argparser.parse_args()

        init_app_using_parser(argparser)
        logging.info(f'config for app {APP}: {config.getappconfig(APP)}')

        datastore.init(dt=args.date, startdt=args.startdate, enddt=args.enddate,
                       clearlocalcache=args.clearcache, fastload=args.fastload)
    except Exception as e:
        errmsg = 'report failed to start up with error'
        logging.error(f"{errmsg} \n: {traceback.format_exc()}")
        raise Exception(errmsg) from e


def get_port():
    argparser = getargparser()
    args = argparser.parse_args()
    return args.port
