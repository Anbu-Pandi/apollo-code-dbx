import logging

from dash_reports.sub_servers.vc_server import APP


def main():
    from apo_config.config import required_config
    from common.utils.envsetup import get_base_parser, init_app_using_parser, kill_stale_server

    parser = get_base_parser(APP)
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    args = init_app_using_parser(parser)
    host = args.host or required_config(APP, "server.host")
    port = args.port or int(required_config(APP, "server.port"))

    kill_stale_server(port)
    logging.info("VC FLIGHT SERVER %s:%s", host, port)

    from dash_reports.sub_servers.vc_server.server import VCFlightServer
    VCFlightServer(host=host, port=port).serve()


if __name__ == "__main__":
    main()
