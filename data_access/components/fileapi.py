import fnmatch
import io
import json
import zipfile
from datetime import datetime

import requests

from apo_config.config import config


class FileApiClient:
    def __init__(self, connection_name, app=None, default_file_name=None, **kwargs):
        self.app = app
        self.connection_details = json.loads(config.getconfig(self.app, connection_name).replace("'", '"'))

        self.url = self.connection_details['url']
        self.params = self.connection_details.get('params', {})
        self.headers = self.connection_details.get('headers', {})
        self.default_file_name = default_file_name or 'file'

        self.params.update(kwargs.get('params', {}))
        self.headers.update(kwargs.get('headers', {}))

    def download_files(self, file_mask, unzip=False):
        response = requests.post(self.url, data=self.params, headers=self.headers)
        if response.status_code == 200:
            content_type = response.headers.get('Content-Type')
            last_modified = response.headers.get('Last-Modified') or datetime.now()
            if unzip and any([(x in content_type) for x in
                              ['application/zip', 'application/x-zip', 'application/x-zip-compressed',
                               'application/x-compressed', 'application/x-compress']]):
                return self._unzip_content(response.content, file_mask)
            else:
                if fnmatch.fnmatch(self.default_file_name, file_mask):
                    return {self.default_file_name: {'content': response.content,
                                                     'last_modified': last_modified}}

            return {}
        else:
            raise Exception(f"Failed to download file: {response.status_code}, {response.text}")

    def _unzip_content(self, content, file_mask, last_modified):
        file_contents = {}
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            for file_info in z.infolist():
                if fnmatch.fnmatch(file_info.filename, file_mask):
                    with z.open(file_info.filename) as f:
                        file_contents[file_info.filename] = {
                            'content': f.read(),
                            'last_modified': datetime(*file_info.date_time) if file_info.date_time else last_modified
                        }
        if (len(file_contents) == 1) and (self.default_file_name is not None):
            file_contents = {self.default_file_name: file_contents[list(file_contents.keys())[0]]}
        return file_contents
