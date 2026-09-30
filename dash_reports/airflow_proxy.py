import logging
import re
from urllib.parse import quote

import requests
from flask import Response, request, stream_with_context

from apo_config.config import required_config
from dash_reports.report_common.utils.dash_authentication import dash_access_check

URL_PREFIX = '/jobs'

_HOP_HEADERS = {'host', 'connection', 'content-length', 'content-encoding', 'transfer-encoding'}
_TEXT_TYPES = ('text/html', 'application/javascript', 'text/javascript', 'text/css', 'application/json')
_ROOT_REF = re.compile(rb'(href|src|action|data-[\w-]+)=(["\'])/(?!/|jobs/)')


def get_backend_url():
    return required_config("dash_reports", "airflow_backend_url").rstrip("/")


def setup_airflow_proxy(server, identity_provider=None):
    @server.route(URL_PREFIX, defaults={'path': ''})
    @server.route(f'{URL_PREFIX}/', defaults={'path': ''})
    @server.route(f'{URL_PREFIX}/<path:path>',
                  methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'])
    def airflow_proxy(path):
        if identity_provider:
            user, groups = identity_provider()
            if not dash_access_check(groups, request.path):
                logging.warning('Jobs Admin access denied for user: %s', user)
                return Response('Jobs Admin access denied', status=403)
        return _forward(path)

    logging.info('Airflow proxy registered: %s -> %s', URL_PREFIX, get_backend_url())


def _forward(path):
    backend_url = get_backend_url()
    url = f'{backend_url}/{path}'
    if request.query_string:
        url = f'{url}?{request.query_string.decode("utf-8")}'

    headers = {k: v for k, v in request.headers if k.lower() not in _HOP_HEADERS}
    headers['X-Forwarded-Host'] = request.host
    headers['X-Forwarded-Proto'] = request.scheme
    headers['X-Forwarded-Prefix'] = URL_PREFIX
    headers['X-Forwarded-For'] = request.remote_addr or ''

    try:
        resp = requests.request(
            request.method, url,
            headers=headers,
            data=request.get_data(),
            stream=True,
            allow_redirects=False,
            timeout=60,
        )
    except requests.exceptions.ConnectionError:
        logging.error('Airflow backend unreachable at %s', backend_url)
        return Response(_backend_down_html(), status=503, content_type='text/html; charset=utf-8')

    excluded = {'content-encoding', 'content-length', 'transfer-encoding', 'connection'}
    out_headers = [(k, v) for k, v in resp.raw.headers.items() if k.lower() not in excluded]

    location = resp.headers.get('Location')
    if location:
        out_headers = [(k, v) for k, v in out_headers if k.lower() != 'location']
        out_headers.append(('Location', _rewrite_location(location, backend_url)))

    content_type = resp.headers.get('Content-Type', '')
    if any(t in content_type for t in _TEXT_TYPES):
        body = _ROOT_REF.sub(rb'\1=\2' + URL_PREFIX.encode() + rb'/', resp.content)
        return Response(body, status=resp.status_code, headers=out_headers)

    return Response(
        stream_with_context(resp.iter_content(chunk_size=8192)),
        status=resp.status_code,
        headers=out_headers,
        direct_passthrough=True,
    )


def _rewrite_location(location, backend_url=None):
    backend_url = backend_url or get_backend_url()
    location = location.replace(quote(backend_url, safe=''), quote(URL_PREFIX, safe=''))
    location = location.replace(backend_url, URL_PREFIX)
    if location.startswith('/') and not location.startswith(f'{URL_PREFIX}/') and location != URL_PREFIX:
        return f'{URL_PREFIX}{location}'
    return location


def _backend_down_html():
    backend_url = get_backend_url()
    return (
        '<!DOCTYPE html><html><body style="font-family:Calibri,sans-serif;padding:40px;'
        'text-align:center;color:#55585A">'
        '<h2 style="color:#C85250;margin-bottom:6px">Airflow Unavailable</h2>'
        f'<p>The Airflow webserver at <code>{backend_url}</code> is not reachable.</p>'
        '</body></html>'
    )
