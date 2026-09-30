import logging
import os
from apo_config.config import config
from dash_reports import APP
# Single source of truth for runtime toggles (see runtime_flags.py).
from dash_reports.app_utils.runtime_flags import is_behind_proxy


class ServerConfig:
    def __init__(self, is_debug_mode, get_port_func):
        self.is_debug_mode = is_debug_mode
        self.get_port_func = get_port_func

    def _behind_proxy(self):
        return is_behind_proxy()

    def get_ssl_context(self):
        if self.is_debug_mode or self._behind_proxy():
            return None  # proxy terminates TLS (or local debug)

        cert_folder = config.getconfig(APP, 'certificate_dir')
        cert_path = os.path.join(cert_folder, 'server.crt')
        key_path = os.path.join(cert_folder, 'server.key')

        return (cert_path, key_path)

    def get_server_kwargs(self):
        behind_proxy = self._behind_proxy()
        kwargs = {
            'debug': self.is_debug_mode,
            # Behind a proxy, bind loopback only (never expose the plain-HTTP app
            # directly); otherwise keep binding all interfaces as before.
            'host': '127.0.0.1' if behind_proxy else '0.0.0.0',
            'port': self.get_port_func()
        }

        ssl_context = self.get_ssl_context()
        if ssl_context:
            kwargs['ssl_context'] = ssl_context

        return kwargs

    def log_server_mode(self):
        if self.is_debug_mode:
            logging.warning("🔓 Running in HTTP mode for local testing")
        elif self._behind_proxy():
            logging.info("🔒 Running in HTTP mode behind a TLS-terminating reverse proxy (loopback bind)")
        else:
            logging.info("🔒 Running in HTTPS mode for production")

