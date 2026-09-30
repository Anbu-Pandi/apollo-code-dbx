APP = "vc_server"

CATALOG = {
    "server.host": {"dev": "0.0.0.0", "prod": "0.0.0.0",
                    "note": "Bind host for the VC Flight server."},
    "server.port": {"dev": "8834", "prod": "8834",
                    "note": "gRPC port for the VC Flight server."},
    "server.scheme": {"dev": "grpc", "prod": "grpc",
                      "note": "grpc or grpc+tls (tls when certificate_dir is set)."},
    "hosts": {"dev": "pue2mal01/pue2mal02", "prod": "pue2mal01/pue2mal02",
              "note": "Co-located node list for local-first resolution."},
    "es_url": {"dev": "http://localhost:9201", "prod": "http://localhost:9201",
               "note": "Elasticsearch URL for the vc_news index (reuse the wiki cluster)."},
    "proxy_disabled": {"dev": "false", "prod": "false",
                       "note": "If true, dash_reports skips the sub-server and queries Delta directly."},
}
