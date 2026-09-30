import logging

from dash_reports.sub_servers.html_sub_server.constants import APP


def main():
    from apo_config.config import required_config
    from common.utils.envsetup import get_base_parser, init_app_using_parser, kill_stale_server

    parser = get_base_parser(APP)
    parser.add_argument('--host')
    parser.add_argument('--port', type=int)
    args = init_app_using_parser(parser)
    host = args.host or required_config(APP, "server.host")
    port = args.port or int(required_config(APP, "server.port"))

    kill_stale_server(port)

    logging.info('=' * 60)
    logging.info('HTML SUB-SERVER FLIGHT SERVER')
    logging.info('Endpoint: %s:%s', host, port)
    logging.info('=' * 60)

    from dash_reports.sub_servers.html_sub_server.server import HtmlSubServer
    server = HtmlSubServer(host=host, port=port)
    server.serve()


if __name__ == '__main__':
    main()
