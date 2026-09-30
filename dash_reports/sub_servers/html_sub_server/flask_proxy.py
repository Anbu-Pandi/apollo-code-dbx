import base64
import logging
from html import escape
from urllib.parse import quote

from flask import Response

from dash_reports.report_common import theme
from dash_reports.sub_servers.html_sub_server.constants import URL_PATH


def setup_html_sub_server_proxy(server):
    @server.route(URL_PATH, methods=['GET'])
    @server.route(f'{URL_PATH}/', methods=['GET'])
    def html_sub_server_root():
        return _serve_path('')

    @server.route(f'{URL_PATH}/<path:html_path>', methods=['GET'])
    def html_sub_server_file(html_path):
        return _serve_path(html_path)

    logging.info('HTML sub-server proxy registered: %s/<path:html_path>', URL_PATH)


def _serve_path(html_path):
    from dash_reports.sub_servers.html_sub_server.client import get_client
    try:
        data = get_client().get_path(html_path) or {}
    except Exception as e:
        logging.exception('Failed to fetch HTML path %s', html_path)
        return Response(_error_html(str(e)), status=503, content_type='text/html; charset=utf-8')

    if not data.get('found'):
        status = int(data.get('status') or 404)
        return Response(
            _not_found_html(html_path, data.get('error', 'HTML not found')),
            status=status,
            content_type='text/html; charset=utf-8',
        )

    if data.get('type') == 'directory':
        return Response(_directory_html(data), status=200, content_type='text/html; charset=utf-8')

    return _file_response(data)


def _path_url(path):
    return URL_PATH if not path else f'{URL_PATH}/{quote(path)}'


def _directory_html(data):
    current_path = data.get('path') or ''
    title = f'Reports / {current_path}' if current_path else 'Reports'
    entries = data.get('entries') or []
    rows = []
    if current_path:
        parent_path = current_path.rsplit('/', 1)[0] if '/' in current_path else ''
        rows.append(_directory_row('..', parent_path, 'directory'))
    for entry in entries:
        rows.append(_directory_row(entry.get('name'), entry.get('path'), entry.get('type')))
    body = '\n'.join(rows) if rows else '<div class="empty">No files found.</div>'
    return f"""<!DOCTYPE html>
<html>
<head>
<title>{escape(title)}</title>
<style>
body {{ font-family: Calibri, Arial, sans-serif; margin: 0; background: #f7faf9; color: #263238; }}
.wrap {{ max-width: 980px; margin: 32px auto; background: #fff; border: 1px solid #dce8e4; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,.04); }}
.head {{ padding: 24px 28px; border-bottom: 1px solid #e6efec; }}
h1 {{ margin: 0; color: #007B63; font-size: 26px; }}
.path {{ margin-top: 6px; color: #667; font-size: 13px; }}
.list {{ padding: 12px 0; }}
.row {{ display: flex; gap: 12px; align-items: center; padding: 10px 28px; text-decoration: none; color: #263238; }}
.row:hover {{ background: #f0faf7; }}
.tag {{ min-width: 58px; color: #007B63; font-size: 12px; font-weight: 700; }}
.empty {{ padding: 28px; color: #778; }}
</style>
{theme.head_tags()}
</head>
<body>
<div class="wrap">
<div class="head"><h1>{escape(title)}</h1><div class="path">{escape(current_path or '/')}</div></div>
<div class="list">{body}</div>
</div>
</body>
</html>"""


def _directory_row(name, path, entry_type):
    label = 'DIR' if entry_type == 'directory' else 'FILE'
    return f'<a class="row" href="{escape(_path_url(path or ""))}"><span class="tag">{label}</span><span>{escape(str(name or ""))}</span></a>'


def _file_response(data):
    raw = base64.b64decode(data.get('content_base64') or '')
    content_type = data.get('content_type') or 'application/octet-stream'
    response = Response(raw, status=200, content_type=content_type)
    response.headers['Content-Disposition'] = f'inline; filename="{_safe_filename(data.get("name") or "file")}"'
    return response


def _safe_filename(name):
    return str(name).replace('"', '').replace('\r', '').replace('\n', '')


def _error_html(msg):
    msg = escape(str(msg))
    return f"""<!DOCTYPE html><html><body style="font-family:Calibri,sans-serif;padding:40px;
text-align:center;color:#55585A">
<h2 style="color:#007B63;margin-bottom:6px">HTML Sub-Server Unavailable</h2>
<p>The html-sub-server is not reachable.</p>
<p style="font-size:11px;font-style:italic;color:#999">Error: {msg}</p>
<p style="font-size:11px;margin-top:14px">Start it with:
<code>python -m dash_reports.sub_servers.html_sub_server</code></p>
</body></html>"""


def _not_found_html(html_path, msg):
    html_path = escape(str(html_path))
    msg = escape(str(msg))
    return f"""<!DOCTYPE html><html><body style="font-family:Calibri,sans-serif;padding:40px;
text-align:center;color:#55585A">
<h2 style="color:#007B63;margin-bottom:6px">Report Not Available</h2>
<p>{html_path}</p>
<p style="font-size:11px;font-style:italic;color:#999">{msg}</p>
</body></html>"""
