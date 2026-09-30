'''
source: https://github.com/Azure/azure-sdk-for-python/blob/main/sdk/storage/azure-storage-blob/samples/blob_samples_directory_interface.py
'''

import logging
import os
import re
import tempfile

from apo_config.config import config
from data_access.spark import APP

try:
    from azure.storage.blob import BlobServiceClient, ContainerClient, BlobSasPermissions, generate_blob_sas
except:
    logging.warning(f'error import azure: azure.storage.blob')


class BlobDownloader:
    def __init__(self, container=None, app=None, credential=None):
        app = app or APP
        blob_storage_name = config.getconfig(app, 'blob_storage_name').split('.')[0]
        container_name = container or config.getconfig(app, 'blob_container_name').split('.')[0]
        sas_url = config.getconfig(app, 'blob_connection_string')
        if not sas_url:
            sep = config.getconfig(app, 'spark_conf_sep')
            conf_values = config.getconfig(app, 'spark_conf_values').split(sep)
            conf_keys = config.getconfig(app, 'spark_conf_keys').split(sep)

            cred = credential
            for k, v in zip(conf_keys, conf_values):
                if cred is None and 'blob.core.windows.net' in k:
                    cred = v
                    break

            assert cred is not None, f"cannot find credential in the keys: {conf_keys}"
            service_client = BlobServiceClient(account_url=f"https://{blob_storage_name}.blob.core.windows.net",
                                               credential=cred)
            self.container_client = service_client.get_container_client(container_name)
        else:
            self.container_client = ContainerClient.from_container_url(sas_url, credential=credential)

    def abfss_file_path(self, filename, dirname=''):
        if '@' in filename:
            return filename

        dirname = dirname or ''
        if not '@' in dirname:
            blob_storage_name = self.container_client.account_name
            blob_container_name = self.container_client.container_name
            dirname = "abfss://" + blob_container_name + "@" + blob_storage_name + '.blob.core.windows.net' + \
                      (("/" + dirname) if dirname != '' else '')

        return f"{dirname}/{filename}"

    def download(self, source, dest):
        '''
        Download a file or directory to a path on the local filesystem
        '''
        if not dest:
            raise Exception('A destination must be provided')

        blobs = self.ls_files(source, recursive=True)
        if blobs:
            blobs = [x for x in blobs if not x.startswith('.')]
        if blobs and len(blobs) > 0:
            # if source is a directory, dest must also be a directory
            if not source == '' and not source.endswith('/'):
                source += '/'
            if not dest.endswith('/'):
                dest += '/'
            # append the directory name from source to the destination
            dest += os.path.basename(os.path.normpath(source)) + '/'

            blobs = [source + blob for blob in blobs]
            for blob in blobs:
                blob_dest = dest + os.path.relpath(blob, source)
                self.download_file(blob, blob_dest)
        else:
            self.download_file(source, dest)

    def blob_exists(self, path):
        bc = self.container_client.get_blob_client(blob=path)
        return bc.exists()

    def download_file_mem(self, source):
        logging.info(f'Downloading {source} ')
        bc = self.container_client.get_blob_client(blob=source)
        data = bc.download_blob()
        return data

    def download_file(self, source, dest):
        if dest.endswith('.'):
            dest += '/'
        file_dest = dest + os.path.basename(source) if dest.endswith('/') else dest

        logging.info(f'Downloading {source} to {file_dest}')
        os.makedirs(os.path.dirname(file_dest), exist_ok=True)
        with open(file_dest, 'wb') as file:
            data = self.download_file_mem(source)
            file.write(data.readall())

    def ls_files(self, path, recursive=True):
        '''
        List files under a path, optionally recursively
        '''
        if not path == '' and not path.endswith('/'):
            path += '/'

        blob_iter = self.container_client.list_blobs(name_starts_with=path)
        files = []
        for blob in blob_iter:
            relative_path = os.path.relpath(blob.name, path)
            if (blob.content_settings.content_type is not None) and \
                    (recursive or not ('/' in relative_path or relative_path.startswith('.'))):
                files.append(relative_path)
        return files

    def ls_dirs(self, path):
        '''
        List directories under a path, optionally recursively
        '''
        if not path == '' and not path.endswith('/'):
            path += '/'

        blob_iter = self.container_client.walk_blobs(path)
        dirs = []
        for blob in blob_iter:
            relative_dir = os.path.relpath(blob.name, path)
            dirs.append(relative_dir)
        return dirs

    def upload_parquet_dataframe(self, df, blob_name, index=False, engine='pyarrow'):
        '''
        Serialize a pandas DataFrame to Parquet on a temp file, then upload as a block blob
        (streams from disk so the full Parquet payload is not held in a Python BytesIO buffer).

        Uses the same credentials as other BlobDownloader methods (container SAS URL or Spark blob key).

        blob_name: path inside the container, e.g. data/attribution_enhanced_files/foo.parquet
        '''
        blob_name = blob_name.lstrip('/')
        fd, temp_path = tempfile.mkstemp(suffix='.parquet', prefix='upload_parquet_')
        os.close(fd)
        try:
            try:
                parquet_kwargs = {}
                if engine == 'pyarrow':
                    parquet_kwargs = {'coerce_timestamps': 'us', 'allow_truncated_timestamps': True}
                df.to_parquet(temp_path, engine=engine, index=index, **parquet_kwargs)
            except MemoryError as e:
                logging.error(
                    'Out of memory while serializing DataFrame to Parquet for blob %s '
                    '(rows=%s, cols=%s). Increase process memory, narrow the date range, '
                    'or reduce columns before upload.',
                    blob_name,
                    len(df.index),
                    len(df.columns),
                    exc_info=True,
                )
                raise RuntimeError(
                    f'Insufficient memory to write Parquet for blob {blob_name!r}.'
                ) from e
            logging.info(f'Uploading parquet file to blob: {blob_name}')
            blob_client = self.container_client.get_blob_client(blob=blob_name)
            with open(temp_path, 'rb') as data:
                blob_client.upload_blob(data, overwrite=True)
        finally:
            try:
                os.unlink(temp_path)
            except OSError:
                pass

    def upload_file(self, source, dest, metadata=None):
        '''
        Upload a single file to a path inside the container
        '''
        logging.info(f'Uploading {source} to {dest}')
        fname = os.path.split(source)[-1]
        blob_name = f"{dest.rstrip('/')}/{fname}" if dest else fname
        with open(source, 'rb') as data:
            self.container_client.upload_blob(name=blob_name, data=data, metadata=metadata, overwrite=True)

    def copy_blob_to_blob(self, source, dest):
        logging.info(f'Copying blob to blob: {source} to {dest}')

        old_folder_path = source.rstrip('/') + '/'
        blobs_to_move = self.ls_files(source, recursive=True)

        for blob in blobs_to_move:
            old_blob_name = f'{source}//{blob}'
            new_blob_name = dest + old_blob_name[len(old_folder_path):]

            # Get the source blob
            source_blob = self.container_client.get_blob_client(old_blob_name)

            # Start copy operation
            new_blob = self.container_client.get_blob_client(new_blob_name)
            if source_blob.get_blob_properties()['metadata'].get('hdi_isfolder', 'false') != 'true':
                new_blob.start_copy_from_url(source_blob.url)

        logging.info(f'Blob copied successfully: {source} to {dest}')

    def upload_dir(self, source, dest):
        '''
        Upload a directory to a path inside the container
        '''
        prefix = '' if dest == '' else dest + '/'
        prefix += os.path.basename(source) + '/'
        for root, dirs, files in os.walk(source):
            for name in files:
                dir_part = os.path.relpath(root, source)
                dir_part = '' if dir_part == '.' else dir_part + '/'
                file_path = os.path.join(root, name)
                blob_path = prefix + dir_part
                logging.info(f'Uploading file to blob: {file_path} to {blob_path}')
                self.upload_file(file_path, blob_path)
        from pathlib import Path
        sorted(Path('.').iterdir(), key=lambda f: f.stat().st_mtime)

    def delete_dir(self, path, file_pattern=None):
        if not path == '' and not path.endswith('/'):
            path += '/'

        if self.blob_exists(path):
            logging.info(f'deleting blob: {path} with pattern: {file_pattern}')
            for blob_dir in self.ls_dirs(path):
                if not file_pattern:
                    for blob_file in self.ls_files(path + f'/{blob_dir}'):
                        try:
                            self.container_client.delete_blob(path + f'/{blob_dir}/{blob_file}')
                        except:
                            pass

                if not blob_dir.startswith('.'):
                    if file_pattern:
                        if re.match(file_pattern, blob_dir):
                            logging.info(f'Deleting directory: {path + f"{blob_dir}/"}')
                            self.delete_dir(path + f'{blob_dir}/')
                    else:
                        self.delete_dir(path + f'{blob_dir}/')

            blob_iter = self.container_client.list_blobs(name_starts_with=path)
            for blob in blob_iter:
                if file_pattern:
                    if re.match(file_pattern, blob.name):
                        logging.info(f'Deleting blob: {blob.name}')
                        self.container_client.delete_blob(blob.name)
                else:
                    self.container_client.delete_blob(blob)

            if self.blob_exists(path) and not file_pattern:
                self.container_client.delete_blob(path[:-1])

    def _client_to_blob(self, client, file_mask, dest):
        files = client.download_files(file_mask)
        try:
            for fn, fi in files.items():
                data = fi['content']
                if isinstance(data, str):
                    with open(data, 'rb') as f:
                        data = f.read()
                if 'last_modified' in fi:
                    fi['last_modified'] = fi['last_modified'].strftime("%Y-%m-%d %H:%M:%S")
                meta_data = {k: v for k, v in fi.items() if k != 'content'}
                self.container_client.upload_blob(name=dest + '/' + fn, data=data,
                                                  overwrite=True,
                                                  metadata=meta_data)
                logging.info(f'Uploaded file {fn} to Azure Blob Storage')
        except Exception as e:
            logging.error(f'An error occurred: {str(e)}')
            raise

    def sftp_to_blob(self, sftp_client, file_mask, dest):
        sftp_client.connect()
        self._client_to_blob(sftp_client, file_mask, dest)
        sftp_client.disconnect()

    def fileapi_to_blob(self, fileapi_client, file_mask, dest):
        self._client_to_blob(fileapi_client, file_mask, dest)

    def read_blob_properties(self, file):
        bc = self.container_client.get_blob_client(blob=file)
        return bc.get_blob_properties()

    def get_blob_last_modified_time(self, file):
        bc = self.container_client.get_blob_client(blob=file)
        blob_properties = bc.get_blob_properties()
        return blob_properties.last_modified


if __name__ == '__main__':
    from common.utils.logutil import setuplogger

    setuplogger()
    config.init('dev')
    # client = BlobDownloader(app='olympus.quotes')

    client = BlobDownloader()

    # client.ls_files('20230812')

    # source = 'data/bond_issuer_curves_altered'
    # dest = 'data/bond_issuer_curves'
    # client.copy_blob_to_blob(source, dest)

    # client.delete_dir('tables/dbo', file_pattern='data.*.parquet$')
    client.delete_dir('tables/dbo/covenant_review_scores_covenants/')
