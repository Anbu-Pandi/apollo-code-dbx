import gc
import logging
import os
import threading
from typing import Callable, Optional

import psutil

_GB = 1024 ** 3
_MB = 1024 ** 2


def _total_rss_bytes(include_children: bool = True) -> int:
    proc = psutil.Process()
    total = proc.memory_info().rss
    if include_children:
        for child in proc.children(recursive=True):
            try:
                total += child.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    return total


def get_process_memory_mb(include_children: bool = True) -> float:
    return _total_rss_bytes(include_children) / _MB


def get_process_memory_gb(include_children: bool = True) -> float:
    return _total_rss_bytes(include_children) / _GB


def set_hard_memory_limit_gb(limit_gb: float) -> bool:
    if os.name != 'nt':
        import resource
        limit_bytes = int(limit_gb * _GB)
        resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, limit_bytes))
        logging.info(f"Set hard memory limit: {limit_gb:.1f}GB (Unix)")
        return True
    
    try:
        import win32job
        import win32api
        import win32process
        
        limit_bytes = int(limit_gb * _GB)
        job = win32job.CreateJobObject(None, f"MemoryLimitJob_{os.getpid()}")
        
        info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        info['BasicLimitInformation']['LimitFlags'] |= win32job.JOB_OBJECT_LIMIT_PROCESS_MEMORY
        info['ProcessMemoryLimit'] = limit_bytes
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
        
        handle = win32api.GetCurrentProcess()
        win32job.AssignProcessToJobObject(job, handle)
        
        logging.info(f"Set hard memory limit: {limit_gb:.1f}GB (Windows Job Object)")
        return True
    except ImportError:
        logging.warning("pywin32 not installed - hard memory limit unavailable on Windows")
        return False
    except Exception as e:
        logging.warning(f"Failed to set hard memory limit: {e}")
        return False


class MemoryMonitor:
    def __init__(
        self,
        soft_limit_gb: float,
        check_interval_sec: float = 30.0,
        warning_threshold: float = 0.8,
        on_warning: Optional[Callable[[float, float], None]] = None,
        on_exceeded: Optional[Callable[[float, float], None]] = None
    ):
        self.soft_limit_gb = soft_limit_gb
        self.check_interval = check_interval_sec
        self.warning_threshold = warning_threshold
        self.on_warning = on_warning or self._default_warning
        self.on_exceeded = on_exceeded or self._default_exceeded
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _log_breakdown(self):
        status = get_memory_status()
        logging.info(
            f"Memory breakdown: self={status['self_rss_mb']:.0f}MB, "
            f"children={status['children_rss_mb']:.0f}MB ({status['child_count']} procs), "
            f"total={status['rss_mb']:.0f}MB"
        )

    def _default_warning(self, current_gb: float, limit_gb: float):
        logging.warning(f"Memory warning: {current_gb:.2f}GB / {limit_gb:.1f}GB ({current_gb/limit_gb*100:.0f}%)")
        self._log_breakdown()
        gc.collect()
        after_gc = get_process_memory_gb()
        logging.info(f"After gc.collect(): {after_gc:.2f}GB")

    def _default_exceeded(self, current_gb: float, limit_gb: float):
        logging.error(f"Memory limit exceeded: {current_gb:.2f}GB > {limit_gb:.1f}GB")
        self._log_breakdown()
        gc.collect()

    def _monitor_loop(self):
        logging.info(f"Memory monitor started: limit={self.soft_limit_gb:.1f}GB, interval={self.check_interval}s")
        while not self._stop_event.wait(self.check_interval):
            current = get_process_memory_gb()
            if current > self.soft_limit_gb:
                self.on_exceeded(current, self.soft_limit_gb)
            elif current > self.soft_limit_gb * self.warning_threshold:
                self.on_warning(current, self.soft_limit_gb)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True, name="MemoryMonitor")
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)


_monitor: Optional[MemoryMonitor] = None


def setup_memory_limit(
    limit_gb: float = 3.0,
    hard_limit: bool = False,
    monitor: bool = True,
    check_interval_sec: float = 30.0
) -> Optional[MemoryMonitor]:
    global _monitor
    
    logging.info(f"Setting up memory limit: {limit_gb:.1f}GB (hard={hard_limit}, monitor={monitor})")
    
    if hard_limit:
        set_hard_memory_limit_gb(limit_gb)
    
    if monitor:
        _monitor = MemoryMonitor(limit_gb, check_interval_sec)
        _monitor.start()
        return _monitor
    
    return None


def get_memory_status() -> dict:
    proc = psutil.Process()
    mem = proc.memory_info()
    children = proc.children(recursive=True)
    children_rss = 0
    for child in children:
        try:
            children_rss += child.memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    total_rss = mem.rss + children_rss
    return {
        'rss_gb': total_rss / _GB,
        'rss_mb': total_rss / _MB,
        'self_rss_mb': mem.rss / _MB,
        'children_rss_mb': children_rss / _MB,
        'child_count': len(children),
        'vms_gb': mem.vms / _GB,
        'percent': proc.memory_percent()
    }
