from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

_HANDLE: object | None = None
_LOGGED_STATUS = False


def _running_on_windows() -> bool:
    return sys.platform == "win32"


def _log_status(msg: str) -> None:
    global _LOGGED_STATUS
    if _LOGGED_STATUS:
        return
    _LOGGED_STATUS = True
    logging.info("process group: %s", msg)


def _create_and_attach_pywin32(name: str | None) -> object | None:
    import win32api
    import win32job

    handle = win32job.CreateJobObject(None, name or "")
    info = win32job.QueryInformationJobObject(handle, win32job.JobObjectExtendedLimitInformation)
    info["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(handle, win32job.JobObjectExtendedLimitInformation, info)
    win32job.AssignProcessToJobObject(handle, win32api.GetCurrentProcess())
    return handle


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_void_p),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JobObjectExtendedLimitInformation = 9


def _create_and_attach_ctypes(name: str | None) -> object | None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE

    handle = kernel32.CreateJobObjectW(None, name)
    if not handle:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")

    info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel32.SetInformationJobObject(
        handle, _JobObjectExtendedLimitInformation,
        ctypes.byref(info), ctypes.sizeof(info),
    ):
        raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")

    if not kernel32.AssignProcessToJobObject(handle, kernel32.GetCurrentProcess()):
        raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
    return handle


def _create_and_attach(name: str | None) -> object | None:
    try:
        return _create_and_attach_pywin32(name)
    except ImportError:
        pass
    except OSError as exc:
        logging.info("process group: pywin32 attach failed (%s); trying ctypes", exc)
    return _create_and_attach_ctypes(name)


def attach_current_process_to_kill_on_close_job(name: str | None = None) -> object | None:
    global _HANDLE
    if _HANDLE is not None:
        return _HANDLE
    if not _running_on_windows():
        _log_status("skipped: non-Windows platform")
        return None
    try:
        handle = _create_and_attach(name)
    except OSError as exc:
        _log_status(f"skipped: assignment failed ({exc})")
        return None
    if handle is None:
        _log_status("skipped: assignment returned no handle")
        return None
    _HANDLE = handle
    _log_status(f"kill-on-close enabled (name={name or 'unnamed'})")
    return _HANDLE


def ensure_child_processes_inherit_job() -> None:
    if not _running_on_windows():
        return
    if _HANDLE is None:
        logging.info("process group: child inheritance inactive (no Job assigned)")
        return
    logging.info("process group: child processes inherit Job (kill-on-close)")
