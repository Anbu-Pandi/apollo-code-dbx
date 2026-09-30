import logging

from dash_reports.industry_tracker.constants import APP


def main():
    from apo_config.config import required_config
    from common.utils.envsetup import init_app_using_parser, get_base_parser, kill_stale_server

    parser = get_base_parser(APP)
    parser.add_argument('--host')
    parser.add_argument('--port', type=int)
    args = init_app_using_parser(parser)
    host = args.host or required_config(APP, "server.host")
    port = args.port or int(required_config(APP, "server.port"))

    kill_stale_server(port)

    logging.info('=' * 60)
    logging.info('INDUSTRY TRACKER FLIGHT SERVER')
    logging.info('Endpoint: %s:%s', host, port)
    logging.info('=' * 60)

    from dash_reports.sub_servers.industry_tracker_server.server import IndustryTrackerFlightServer
    server = IndustryTrackerFlightServer(host=host, port=port)
    server.serve()


if __name__ == '__main__':
    main()
