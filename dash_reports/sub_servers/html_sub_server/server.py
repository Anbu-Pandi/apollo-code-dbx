import base64
import json
import logging
import mimetypes
import os
import threading
import time
from pathlib import Path

import pyarrow.flight as flight

from apo_config.config import required_config
from dash_reports.sub_servers.cdx_options_server.serialization import error_result, json_result
from dash_reports.sub_servers.cdx_options_server.ssl_utils import load_tls_certs
from dash_reports.sub_servers.html_sub_server.constants import (
    APP,
    HTML_FOLDER_CONFIG_KEY,
)


def get_html_folder():
    from apo_config.config import config
    folder = (
        config.getconfig(APP, 'root_folder')
        or config.getconfig(APP, HTML_FOLDER_CONFIG_KEY)
        or config.getconfig(APP, 'folder')
    )
    return os.path.expandvars(os.path.expanduser(str(folder))) if folder else None


class HtmlSubServer(flight.FlightServerBase):
    _request_counter = 0
    _counter_lock = threading.Lock()
    _INLINE_CONTENT_TYPES = frozenset({
        'application/json',
        'application/pdf',
        'application/xml',
        'image/svg+xml',
    })
    _INLINE_PREFIXES = ('image/', 'text/', 'video/', 'audio/')

    def __init__(self, host=None, port=None, root_dir=None, **kwargs):
        host = host or required_config(APP, "server.host")
        port = port or int(required_config(APP, "server.port"))
        ssl_ctx = load_tls_certs(APP)
        if ssl_ctx:
            with open(ssl_ctx[0], 'rb') as f:
                tls_cert = f.read()
            with open(ssl_ctx[1], 'rb') as f:
                tls_key = f.read()
            super().__init__(flight.Location.for_grpc_tls(host, port),
                             tls_certificates=[(tls_cert, tls_key)], **kwargs)
            logging.info('HTML sub-server Flight TLS on %s:%s', host, port)
        else:
            super().__init__(flight.Location.for_grpc_tcp(host, port), **kwargs)
            logging.info('HTML sub-server Flight (no TLS) on %s:%s', host, port)

        self._host = host
        self._port = port
        self._root_dir = self._resolve_root(root_dir or get_html_folder())
        logging.info('HTML sub-server root: %s', self._root_dir or '<missing config>')

    @staticmethod
    def _resolve_root(root_dir):
        if not root_dir:
            return None
        return Path(root_dir).resolve()

    def _resolve_path(self, request_path):
        if self._root_dir is None:
            return None, f'{APP}.{HTML_FOLDER_CONFIG_KEY} is not configured'
        clean_path = str(request_path or '').replace('\\', '/').strip('/')
        target = (self._root_dir / clean_path).resolve() if clean_path else self._root_dir
        if target != self._root_dir and self._root_dir not in target.parents:
            return None, 'html path outside configured folder'
        return target, None

    def _directory_entries(self, target):
        entries = []
        for path in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            stat = path.stat()
            entry_type = 'directory' if path.is_dir() else 'file'
            content_type = '' if path.is_dir() else self._content_type(path)
            if path.is_file() and not self._can_render_inline(content_type):
                continue
            entries.append({
                'name': path.name,
                'path': path.relative_to(self._root_dir).as_posix(),
                'type': entry_type,
                'size': 0 if path.is_dir() else stat.st_size,
                'modified': stat.st_mtime,
                'content_type': content_type,
            })
        return entries

    @staticmethod
    def _content_type(path):
        return mimetypes.guess_type(str(path))[0] or 'application/octet-stream'

    @classmethod
    def _can_render_inline(cls, content_type):
        content_type = (content_type or '').split(';', 1)[0].lower()
        return content_type in cls._INLINE_CONTENT_TYPES or content_type.startswith(cls._INLINE_PREFIXES)

    def _file_payload(self, target, rel_path):
        content_type = self._content_type(target)
        if not self._can_render_inline(content_type):
            return {
                'found': False,
                'status': 415,
                'error': f'File type not browser-renderable: {target.name}',
            }
        data = target.read_bytes()
        logging.info('Serving file: %s (%d bytes)', target, len(data))
        return {
            'found': True,
            'status': 200,
            'type': 'file',
            'path': rel_path,
            'name': target.name,
            'content_type': content_type,
            'content_base64': base64.b64encode(data).decode('ascii'),
        }

    def list_actions(self, context):
        for attr in dir(self):
            if attr.startswith('_act_'):
                name = attr[5:]
                yield (name, name)

    def do_action(self, context, action):
        t0 = time.time()
        t = threading.current_thread()
        if t.name.startswith('Dummy'):
            with self._counter_lock:
                self._request_counter += 1
            t.name = f'HTML_{self._request_counter}'

        body = {}
        if action.body and action.body.to_pybytes():
            try:
                body = json.loads(action.body.to_pybytes().decode('utf-8'))
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass

        handler = getattr(self, f'_act_{action.type}', None)
        if handler is None:
            yield error_result(f'Unknown action: {action.type}')
            return
        try:
            yield handler(body)
        except Exception as e:
            logging.exception('Action %s failed', action.type)
            yield error_result(str(e))
        finally:
            elapsed = (time.time() - t0) * 1000
            if elapsed > 50:
                logging.info('[SERVER] %s took %.0fms', action.type, elapsed)

    def _act_health_check(self, b):
        return json_result({
            'success': True,
            'service': APP,
            'root_dir': str(self._root_dir or ''),
            'root_exists': bool(self._root_dir and self._root_dir.exists()),
        })

    def _act_get_html(self, b):
        html_path = b.get('path')
        target, error = self._resolve_path(html_path)
        if error:
            return json_result({'found': False, 'status': 400, 'error': error})
        if not target.is_file():
            return json_result({'found': False, 'status': 404, 'error': f'HTML not found: {html_path}'})
        return json_result(self._file_payload(target, target.relative_to(self._root_dir).as_posix()))

    def _act_get_path(self, b):
        request_path = b.get('path')
        target, error = self._resolve_path(request_path)
        if error:
            return json_result({'found': False, 'status': 400, 'error': error})
        if not target.exists():
            return json_result({'found': False, 'status': 404, 'error': f'Path not found: {request_path or ""}'})
        rel_path = '' if target == self._root_dir else target.relative_to(self._root_dir).as_posix()
        if target.is_dir():
            return json_result({
                'found': True,
                'status': 200,
                'type': 'directory',
                'path': rel_path,
                'entries': self._directory_entries(target),
            })
        return json_result(self._file_payload(target, rel_path))

    def _act_list_html(self, b):
        if self._root_dir is None or not self._root_dir.exists():
            return json_result([])
        return json_result([
            path.relative_to(self._root_dir).as_posix()
            for path in sorted(self._root_dir.rglob('*'))
            if path.is_file()
        ])
