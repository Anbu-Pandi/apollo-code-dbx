import datetime
import logging
import os
import shutil
import tempfile

from common.utils import dtutil


class TempFolder():
    def __init__(self):
        self._dload_dir = tempfile.mkdtemp()
        logging.info(f'created temp dir: {self._dload_dir}')

    def __enter__(self):
        return self._dload_dir

    def __exit__(self, exc_type, exc_value, exc_traceback):
        shutil.rmtree(self._dload_dir)
        logging.info(f'deleted temp dir: {self._dload_dir}')

    def get_folder(self):
        return self._dload_dir


def delete_old_files(folder_path: str, cutoff_date):
    cutoff = dtutil.todate(cutoff_date)

    for filename in os.listdir(folder_path):
        file_path = os.path.join(folder_path, filename)

        if os.path.isfile(file_path):

            file_time = datetime.datetime.fromtimestamp(os.path.getmtime(file_path))

            if dtutil.todate(file_time) < cutoff:
                os.remove(file_path)
                logging.info(f"Deleted {file_path} file time: {file_time} cutoff: {cutoff}")
