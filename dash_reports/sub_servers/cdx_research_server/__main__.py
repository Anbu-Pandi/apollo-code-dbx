from dash_reports.sub_servers.cdx_research_server.settings import APP


def main():
    import os
    for name in ('RAYON_NUM_THREADS', 'TOKIO_WORKER_THREADS', 'OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS'):
        os.environ.setdefault(name, '4')
    import pyarrow as pa
    pa.set_cpu_count(4)
    pa.set_io_thread_count(4)
    from common.utils.envsetup import get_base_parser, init_app_using_parser
    parser = get_base_parser(APP)
    parser.add_argument('--host')
    parser.add_argument('--port', type=int)
    args = init_app_using_parser(parser)
    from dash_reports.sub_servers.cdx_research_server.server import CDXResearchFlightServer
    server = CDXResearchFlightServer(host=args.host, port=args.port)
    try:
        server.serve()
    finally:
        server.shutdown()


if __name__ == '__main__':
    main()
