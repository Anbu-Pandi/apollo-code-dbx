import os


APP = 'cdx_options_server'


def get_cert_dir(app_name=APP):
    try:
        from apo_config.config import config
        cert_dir = config.getconfig(app_name, 'certificate_dir')
        if not cert_dir:
            cert_dir = config.getconfig('dash_reports', 'certificate_dir')
        return cert_dir
    except Exception:
        return None


def load_tls_certs(app_name=APP):
    cert_dir = get_cert_dir(app_name)
    if not cert_dir:
        return None
    cert_path = os.path.join(cert_dir, 'server.crt')
    key_path = os.path.join(cert_dir, 'server.key')
    if os.path.exists(cert_path) and os.path.exists(key_path):
        return cert_path, key_path
    return None


def load_tls_root_cert(app_name=APP):
    cert_dir = get_cert_dir(app_name)
    if not cert_dir:
        return None
    cert_path = os.path.join(cert_dir, 'server.crt')
    if os.path.exists(cert_path):
        with open(cert_path, 'rb') as f:
            return f.read()
    return None
