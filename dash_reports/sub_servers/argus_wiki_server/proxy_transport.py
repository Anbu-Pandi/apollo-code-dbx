"""Shared HTTP forwarding and recovery for every configured wiki instance."""
import logging

import requests
from flask import Response, request, stream_with_context

from dash_reports.sub_servers.argus_wiki_server.recovery import error_page

HOP_BY_HOP = {
    'connection', 'content-encoding', 'content-length', 'host', 'keep-alive',
    'proxy-authenticate', 'proxy-authorization', 'te', 'trailer',
    'transfer-encoding', 'upgrade',
}


def backend_down_html(url_path: str) -> str:
    return error_page('This wiki is temporarily unavailable. You can return home or try again.', url_path)


def forward(path: str, *, base_url: str, url_path: str) -> Response:
    target = base_url if not path else f'{base_url}/{path}'
    if request.query_string:
        target += '?' + request.query_string.decode('utf-8')
    headers = {key: value for key, value in request.headers if key.lower() not in HOP_BY_HOP}
    try:
        upstream = requests.request(request.method, target, headers=headers,
                                    data=request.get_data(), stream=True,
                                    timeout=(2.0, 120), allow_redirects=False)
    except (requests.ConnectionError, requests.Timeout) as exc:
        logging.warning('Wiki backend unavailable at %s: %s', base_url, exc)
        return Response(backend_down_html(url_path),
                        status=503, content_type='text/html; charset=utf-8')
    response_headers = [(key, value) for key, value in upstream.raw.headers.items()
                        if key.lower() not in HOP_BY_HOP]

    def stream():
        with upstream:
            for chunk in upstream.iter_content(chunk_size=8192):
                if chunk:
                    yield chunk

    return Response(stream_with_context(stream()), status=upstream.status_code,
                    headers=response_headers, direct_passthrough=True)
