"""Local page host using the dedicated Research Flight process and real data."""


def main():
    import os
    from common.utils.envsetup import get_base_parser, init_app_using_parser
    parser = get_base_parser('cdx_research_local')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8067)
    parser.add_argument('--research-host', default='127.0.0.1')
    parser.add_argument('--research-port', type=int, default=8817)
    args = init_app_using_parser(parser)
    os.environ['CDX_RESEARCH_HOST'] = args.research_host
    os.environ['CDX_RESEARCH_PORT'] = str(args.research_port)
    from dash_reports.report_common.data.data_model import datastore
    datastore.init(fastload=True)
    from dash_reports.app_utils import app_callbacks
    from dash_reports.app_utils.app_factory import create_application
    app = create_application(False, False, lambda: args.port, run_teams_bot=False)

    from waitress import serve
    serve(app.server, host=args.host, port=args.port, threads=8)


if __name__ == '__main__':
    main()
