import logging
import socket

from apo_config.config import config
from common.utils.envsetup import init_app_using_parser
from dash_reports import APP
from flight_server.client_sdk.flight_transport import write_service_certificate


def _extra_hosts():
    hosts = ["pue2mal01", "pue2mal02", socket.gethostname(), socket.getfqdn()]
    for app, key in (
        ("cluster", "internal_service_host"),
        ("dash_reports", "internal_service_host"),
        ("dash_reports", "hosts"),
        ("flight_server", "hosts"),
    ):
        raw = config.getconfig(app, key)
        if raw:
            hosts.append(raw)
    return hosts


def create_self_signed_cert():
    cert_dir = config.getconfig(APP, "certificate_dir")
    write_service_certificate(cert_dir, extra_hosts=_extra_hosts())
    logging.info("Certificates created in %s", cert_dir)


if __name__ == "__main__":
    init_app_using_parser()
    create_self_signed_cert()
