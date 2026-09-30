import fnmatch
import io
import json
import logging
import os
import re
from datetime import datetime

import paramiko

from apo_config.config import config
from common.utils.fileutil import TempFolder


class SFTPClient:
    def __init__(self, sftp_connection_name, app=None, timeout=30):
        self.app = app
        sftp_dict = json.loads(config.getconfig(self.app, sftp_connection_name).replace("'", '"'))

        self.host = sftp_dict['host']
        self.username = sftp_dict['username']
        self.password = sftp_dict.get('password')
        self.key_data = sftp_dict.get('key_data')
        self.key_path = sftp_dict.get('key_path')
        self.port = int(sftp_dict.get('port', 22))
        self.directory = sftp_dict.get('directory', '/')
        self.client = None
        self.sftp = None
        self.timeout = timeout

    def __enter__(self):
        self.connect()
        self.sftp.chdir(self.directory)
        return self

    def __exit__(self, type, value, traceback):
        self.disconnect()

    def connect(self):
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        pkey = self._get_private_key()
        connect_kwargs = dict(
            hostname=self.host,
            port=self.port,
            username=self.username,
            timeout=self.timeout,
            disabled_algorithms=dict(ciphers=["aes128-cbc", "aes192-cbc", "aes256-cbc",
                                              "3des-cbc", "blowfish-cbc",
                                              "arcfour", "arcfour128", "arcfour256"])
        )
        if pkey is not None:
            connect_kwargs['pkey'] = pkey
        elif self.password:
            connect_kwargs['password'] = self.password
        else:
            raise ValueError('SFTP config must include password, key_data, or key_path')

        self.client.connect(**connect_kwargs)

        self.client.get_transport().set_keepalive(60)
        self.sftp = self.client.open_sftp()
        logging.info(f'Connected to SFTP server: {self.host}:{self.port} for user: {self.username}')
        self.sftp.chdir(self.directory)
        logging.info(f'Directory changed to : {self.directory}')

    def _get_private_key(self):
        if self.key_data:
            return self._load_private_key(self.key_data)
        if self.key_path:
            with open(self.key_path, encoding='utf-8') as handle:
                return self._load_private_key(handle.read())
        return None

    @staticmethod
    def _private_key_classes():
        key_classes = []
        for key_cls_name in ('RSAKey', 'Ed25519Key', 'ECDSAKey', 'DSSKey'):
            key_cls = getattr(paramiko, key_cls_name, None)
            if key_cls is not None:
                key_classes.append(key_cls)
        return key_classes

    @staticmethod
    def _load_private_key(key_text):
        key_text = key_text.replace('\\n', '\n')
        match = re.search(
            r'(-----BEGIN [A-Z ]*PRIVATE KEY-----)(.*?)(-----END [A-Z ]*PRIVATE KEY-----)',
            key_text,
            flags=re.DOTALL,
        )
        if match:
            header, body, footer = match.groups()
            body = ''.join(body.split())
            key_text = '\n'.join([header] + [body[i:i + 64] for i in range(0, len(body), 64)] + [footer])

        key_errors = []
        for key_cls in SFTPClient._private_key_classes():
            try:
                return key_cls.from_private_key(io.StringIO(key_text))
            except paramiko.SSHException as exc:
                key_errors.append(str(exc))
        last_error = key_errors[-1] if key_errors else 'no private key loaders available'
        raise paramiko.SSHException(f'not a supported private key: {last_error}')

    def disconnect(self):
        if self.sftp:
            self.sftp.close()
        if self.client:
            self.client.close()
        logging.info('Disconnected from SFTP server')

    def get_files_with_mask(self, file_mask):
        if '*' not in file_mask:
            file_mask = f'*{file_mask}'
        for file in self.sftp.listdir():
            if fnmatch.fnmatch(file, file_mask):
                yield file

    def download_files(self, file_mask):
        fdict = {}
        for file in self.get_files_with_mask(file_mask):
            fi = {}
            fi['last_modified'] = self.get_file_modified_time(file)
            tempfolder = TempFolder().get_folder()
            local_file = os.path.join(tempfolder, file)
            self.sftp.get(file, local_file)
            fi['content'] = local_file
            fdict[file] = fi
            logging.info(f'Downloaded file {file} from SFTP server')
        return fdict

    def get_file_modified_time(self, file):
        mtime = self.sftp.stat(file).st_mtime
        mtime = datetime.fromtimestamp(mtime)
        return mtime


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()
    config.init('dev')
    with SFTPClient('jpm_ll_sftp_connection') as client:
        logging.info(list(client.get_files_with_mask('*.csv')))
