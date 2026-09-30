import os
from pathlib import Path

APP = 'cdx_research_server'


def setting(key, default=None):
    from apo_config.config import config
    return config.getconfig(APP, key) or default


def endpoint(host=None, port=None, client=False):
    from apo_config.config import required_config
    from common.service_discovery import resolve_service_hosts
    host = host or os.environ.get('CDX_RESEARCH_HOST')
    if not host:
        host = resolve_service_hosts(APP)[0] if client else required_config(APP, 'server.host')
    port = port if port is not None else os.environ.get('CDX_RESEARCH_PORT') or required_config(APP, 'server.port')
    return host, int(port)


def tls_files():
    directory = setting('certificate_dir')
    if not directory:
        return None
    cert, key = Path(directory) / 'server.crt', Path(directory) / 'server.key'
    if not cert.is_file() or not key.is_file():
        raise RuntimeError(f'{APP}.certificate_dir must contain server.crt and server.key')
    return cert, key
