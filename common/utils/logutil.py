import configparser
import logging
import os
import sys
import threading
import warnings
from logging.handlers import RotatingFileHandler

__CURRENT_LOG_FILE__ = None
__CURRENT_LOG_FOLDER__ = r'c:/temp/apo_logs'
_CONFIG_LOADED = False


def _set_log_folder_using_config_ini():
    ini_file = 'apo_config.ini'
    global _CONFIG_LOADED, __CURRENT_LOG_FOLDER__
    if not _CONFIG_LOADED:
        config_ini = configparser.ConfigParser()
        config_file_paths = [ini_file,
                             os.path.join('../', ini_file),
                             os.path.join('../../', ini_file),
                             os.path.join('../../../', ini_file),
                             os.path.join('../../../../', ini_file),
                             os.path.join(__CURRENT_LOG_FOLDER__, ini_file), ]
        for config_file_path in config_file_paths:
            if os.path.exists(config_file_path):
                config_ini.read(config_file_path)
                __CURRENT_LOG_FOLDER__ = config_ini.get('Logging', 'LogFolder', fallback=__CURRENT_LOG_FOLDER__)
                break

        _CONFIG_LOADED = True


_set_log_folder_using_config_ini()


def _set_main_thread_name(name="T_M"):
    threading.main_thread().name = name


def get_log_folder():
    return __CURRENT_LOG_FOLDER__


def getlogfile():
    return __CURRENT_LOG_FILE__


def resetlogfile():
    global __CURRENT_LOG_FILE__
    __CURRENT_LOG_FILE__ = None


def _find_alternative_logfile(base_logfile, max_attempts=100):
    base_name = base_logfile.replace('.log', '')
    for i in range(2, max_attempts):
        alt_log_file = f"{base_name}{i}.log"
        try:
            test_handler = RotatingFileHandler(alt_log_file, backupCount=20, encoding='utf-8',
                                              maxBytes=100 * 1024 * 1024)
            test_handler.close()
            return alt_log_file
        except PermissionError:
            continue
    return None


def _python_pids_via_tasklist():
    import subprocess
    pids = []
    for image in ("python.exe", "pythonw.exe"):
        try:
            r = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {image}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5,
            )
        except Exception:
            continue
        for line in r.stdout.splitlines():
            parts = [p.strip().strip('"') for p in line.split(',')]
            if len(parts) >= 2 and parts[1].isdigit():
                pids.append(int(parts[1]))
    return pids


def _kill_processes_holding(filepath):
    """Kill any python(w) process (other than us) that has filepath open.
    Open-files is the ground truth — no cmdline guessing."""
    try:
        import psutil
    except ImportError:
        return 0

    target = os.path.abspath(filepath).lower()
    my_pid = os.getpid()
    killed = 0

    for pid in _python_pids_via_tasklist():
        if pid == my_pid:
            continue
        try:
            proc = psutil.Process(pid)
            if not any(os.path.abspath(f.path).lower() == target for f in proc.open_files()):
                continue
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        logging.warning(f"Killing PID={pid} holding {filepath}")
        try:
            proc.kill()
            killed += 1
        except Exception as e:
            logging.warning(f"Could not kill PID={pid}: {e}")

    if killed:
        import time
        time.sleep(1.0)
    return killed


def _try_rollover_with_fallback(filehandler, handlers, current_logfile):
    try:
        filehandler.doRollover()
        return filehandler, current_logfile, False
    except PermissionError:
        pass

    if _kill_processes_holding(current_logfile):
        try:
            filehandler.doRollover()
            return filehandler, current_logfile, False
        except PermissionError:
            pass

    alt_log_file = _find_alternative_logfile(current_logfile)
    if alt_log_file:
        handlers.remove(filehandler)
        filehandler.close()
        new_filehandler = RotatingFileHandler(alt_log_file, backupCount=20, encoding='utf-8',
                                              maxBytes=100 * 1024 * 1024)
        handlers.append(new_filehandler)

        if os.path.isfile(alt_log_file):
            try:
                new_filehandler.doRollover()
            except Exception:
                pass

        return new_filehandler, alt_log_file, True
    return filehandler, current_logfile, False


def setuplogger(logfile=None, logfolder=None, logconsole=True, loglevel=logging.INFO, logunhandlederrors=True):
    if (logfile or '').lower() == 'nolog':
        logfile = None

    from imp import reload
    reload(logging)

    _set_main_thread_name()

    global __CURRENT_LOG_FILE__
    handlers = []
    if logconsole:
        handlers += [logging.StreamHandler()]
    warnings.filterwarnings('ignore')

    needroll = False
    switched_logfile = False
    if not logfile is None:
        if logfolder is None:
            logfolder, logfile = os.path.split(logfile)
            logfile = logfile.replace('.log', '')
            if not os.path.isabs(logfolder):
                logfolder = os.path.join(get_log_folder(), logfolder)
            logfolder = os.path.join(logfolder, logfile)

        if not os.path.exists(logfolder):
            os.makedirs(logfolder)
        __CURRENT_LOG_FILE__ = __CURRENT_LOG_FILE__ or \
                               os.path.join(logfolder, '{}.log'.format(logfile))

        needroll = os.path.isfile(__CURRENT_LOG_FILE__)
        filehandler = RotatingFileHandler(__CURRENT_LOG_FILE__, backupCount=20, encoding='utf-8',
                                          maxBytes=100 * 1024 * 1024)
        handlers += [filehandler]

        # Try to rollover if file exists, switch to alternative name if another process is using it
        if needroll:
            filehandler, __CURRENT_LOG_FILE__, switched_logfile = _try_rollover_with_fallback(
                filehandler, handlers, __CURRENT_LOG_FILE__
            )

    logger = logging.getLogger()
    logging.basicConfig(
        level=loglevel,
        format='%(asctime)s| %(threadName)4.4s| %(levelname)4.4s| %(module)s.%(funcName)s| %(message)s',
        handlers=handlers,
        force=True
    )

    # Log warning if we switched to an alternative log file
    if switched_logfile:
        logging.warning(f"Original log file in use by another process, switched to: {__CURRENT_LOG_FILE__}")

    if __CURRENT_LOG_FILE__ is not None:
        logging.info('writing log to: {0}'.format(__CURRENT_LOG_FILE__))
    logging.info('log level set to: {0}'.format(loglevel))

    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logging.error("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))

    if logunhandlederrors:
        sys.excepthook = handle_exception


if __name__ == '__main__':
    setuplogger()
