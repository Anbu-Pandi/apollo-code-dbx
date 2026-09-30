from dash_reports.sub_servers.cdx_research_server.settings import APP

CATALOG = {
    'hosts': {'dev': '127.0.0.1', 'prod': 'pue2mal02'},
    'server.host': {'dev': '127.0.0.1', 'prod': '0.0.0.0'},
    'server.port': {'dev': '8817', 'prod': '8817'},
    'jobs.workers': {'dev': '2', 'prod': '2'},
    'jobs.max_jobs': {'dev': '24', 'prod': '24'},
    'market.refresh_seconds': {'dev': '30', 'prod': '30'},
    'client.timeout_seconds': {'dev': '10', 'prod': '10'},
}
