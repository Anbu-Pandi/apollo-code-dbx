import logging
import uuid
from enum import Enum
from typing import Optional, Dict, Any
from common.utils import sqlutil
from datetime import datetime, timezone
from common.utils.dtutil import tz_convert

APP = "databricks"
JOB_LOG_TABLE = 'apo_job_load_log'


class JobLoadLogger:
    class _Status(Enum):
        STARTED = "STARTED"
        COMPLETED = "COMPLETED"
        FAILED = "FAILED"

    def __init__(self, job_name):
        self.run_id = str(uuid.uuid4())
        self.job_name = job_name
        self.status = self._Status

    def add_start_entry(self):
        status = self.status.STARTED.value
        started_at = tz_convert(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))

        sql = f""" INSERT INTO {JOB_LOG_TABLE} 
        (run_id, job_name, status, started_at)
        VALUES ('{self.run_id}', '{self.job_name}', '{status}', '{started_at}')
        """

        logging.info(
            f'making batch load entry for job: {self.job_name}, started_at: {started_at}, status: {status}')
        sqlutil.execsql(sql, app=APP)

    def update_status(self, status):
        status = status.value

        sql = f"""UPDATE {JOB_LOG_TABLE} 
        SET status='{status}'
        WHERE run_id='{self.run_id}' and job_name = '{self.job_name}'
        """
        logging.info(
            f'updating batch load entry status for job:{self.job_name}, run_id: {self.run_id}, status: {status}')

        sqlutil.execsql(sql, app=APP)


def get_latest_job_status(job_name: str) -> Optional[Dict[str, Any]]:
    """Get the latest status of a job from apo_job_load_log"""
    try:
        sql = f"""
        SELECT status, started_at, job_name, run_id
        FROM {JOB_LOG_TABLE}
        WHERE job_name = '{job_name}'
        ORDER BY started_at DESC
        LIMIT 1
        """
        
        df = sqlutil.readdfsql(sql, app=APP)
        
        if df.empty:
            return None
        
        row = df.iloc[0]
        return {
            'status': row['status'],
            'started_at': row['started_at'],
            'job_name': row['job_name'],
            'run_id': row['run_id']
        }
    except Exception as e:
        logging.warning(f"⚠️ Failed to get job status: {e}")
        return None
