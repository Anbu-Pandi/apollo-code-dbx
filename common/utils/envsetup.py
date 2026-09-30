import argparse
import inspect
import json
import logging
import os
import time
import traceback

from apo_config.config import config
from common.utils.logutil import setuplogger


def get_base_parser(description, log_file=None, include_memory_args=True):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('--env', required=False, default='dev',
                        help='config environment to read')
    parser.add_argument('--runas', required=False, default=None,
                        help='runas to connect to config environment')
    parser.add_argument('--log', metavar='path', required=False, default=log_file or description,
                        help='path to logfile')
    parser.add_argument("--config-timeout", type=float, default=120.0,
                        help="seconds to wait for remote config load before failing startup")
    parser.add_argument("--config-retries", type=int, default=5,
                        help="number of bounded remote config load attempts")
    parser.add_argument("--debug", default=False, action="store_true",
                        help="debug mode. will print debug logging info")
    parser.add_argument("--overrides", metavar="[APP.]KEY=VALUE | JSON", action="append", default=None,
                        help="config override(s), repeatable; same as config.init(overrides=...). "
                             "Either KEY=VALUE, e.g. --overrides azuredb_local_dir=c:/temp/tables (global) "
                             "or --overrides apo_data_access.spark.azuredb_local_dir=c:/temp/tables (app-scoped; "
                             "app is everything before the last dot); or a JSON string, e.g. "
                             "--overrides '{\"azuredb_local_dir\":\"c:/temp/tables\"}' (PowerShell) / "
                             "--overrides \"{\\\"azuredb_local_dir\\\":\\\"c:/temp/tables\\\"}\" (cmd.exe)")
    if include_memory_args:
        parser.add_argument("--memory-limit", type=float, default=5.0,
                            help="memory limit in GB (0 or negative = no limit)")
        parser.add_argument("--hard-memory-limit", action="store_true",
                            help="enforce OS-level hard memory limit")
    return parser


def get_caller_filename():
    caller_frame = inspect.stack()[:3][-1]
    full_path = caller_frame.filename
    base_filename = os.path.basename(full_path)
    return base_filename


def _normalize_override_key(key):
    """Dotted key -> (app, key) tuple (split on the LAST dot); plain key stays a
    global override. config.init only globalizes bare strings, so app-scoped keys
    must be tuples here."""
    key = key.strip()
    return tuple(key.rsplit('.', 1)) if '.' in key else key


def _parse_cli_overrides(raw):
    """Turn --overrides entries into a config.init overrides dict.

    Each entry is either a JSON string ('{"key": "value"}', parsed with
    json.loads) or a [APP.]KEY=VALUE pair. Keys are normalized the same way in
    both forms (see _normalize_override_key), so the result matches
    config.init(overrides=...).
    """
    if not raw:
        return {}
    parsed = {}
    for item in raw:
        item = item.strip()
        if item.startswith('{'):
            for key, value in json.loads(item).items():
                parsed[_normalize_override_key(key)] = value
        elif '=' in item:
            key, value = item.split('=', 1)
            parsed[_normalize_override_key(key)] = value
        else:
            raise ValueError(f"--overrides must be [APP.]KEY=VALUE or a JSON string, got: {item!r}")
    return parsed


def init_app_using_parser(argparser=None, configoverrides=None, *, load_config=True):
    if not argparser:
        argparser = get_base_parser(get_caller_filename())

    args, _ = argparser.parse_known_args()
    # programmatic overrides first, CLI --overrides win on conflict (runtime intent)
    merged_overrides = {**(configoverrides or {}), **_parse_cli_overrides(getattr(args, 'overrides', None))}
    if load_config:
        init_app(args.env, args.log, args.debug, merged_overrides or None, args.runas,
                 config_timeout=args.config_timeout, config_retries=args.config_retries)
    else:
        setuplogger(args.log, loglevel=logging.DEBUG if args.debug else logging.INFO)
    _setup_memory_from_args(args)
    logging.info(f'init app using args: {args}')
    return args


def _setup_memory_from_args(args):
    memory_limit = getattr(args, 'memory_limit', None)
    if memory_limit is None or memory_limit <= 0:
        return
    from common.utils.memutil import setup_memory_limit
    hard_limit = getattr(args, 'hard_memory_limit', False)
    setup_memory_limit(memory_limit, hard_limit=hard_limit, monitor=True)


def _is_interactive():
    import sys
    if hasattr(sys, 'ps1'):
        return True
    try:
        import IPython
        return IPython.get_ipython() is not None
    except ImportError:
        return False


def _load_configdict_with_timeout(env, runas=None, timeout=120.0, retries=5):
    if _is_interactive():
        logging.info("config bootstrap: interactive shell detected; loading inline (Ctrl+C to abort)")
        return None

    from common.utils.jobutil import JobExecutor
    from common.utils.jobutil import retrycode

    def _attempt():
        logging.info("config bootstrap: loading remote config timeout=%.1fs", timeout)
        executor = JobExecutor('process', max_workers=1, timeout=timeout)
        try:
            started = time.time()
            result = executor.submitjob(
                config.init_and_get_configdict,
                [{'env': env, 'runas': runas}],
                job_name='config_bootstrap')[0]
            elapsed = time.time() - started
            err = result.get('error')
            if err:
                etype = TimeoutError if isinstance(err, str) and 'Timeout after' in err else RuntimeError
                raise etype(err)
            configdict = result.get('result')
            logging.info("config bootstrap: loaded %s config entries in %.1fs", len(configdict or {}), elapsed)
            return configdict
        finally:
            executor.cleanup()

    return retrycode(_attempt, retrycount=max(int(retries or 1), 1), wait_on_error_seconds=2)


def init_app(env, logfile=None, debug=None, configoverrides=None, runas=None,
             config_timeout=120.0, config_retries=5):
    logmode = logging.DEBUG if debug else logging.INFO

    setuplogger(logfile, loglevel=logmode)

    # Enumerate the system cert store once per process instead of once per
    # requests adapter. pip_system_certs otherwise re-walks the (large, on
    # domain-joined Windows hosts) cert store on every new Session/adapter while
    # holding the GIL, serializing all worker threads on any requests-heavy
    # threaded server (flight_server, cdx-options, teams, MCP, wiki reader, ...).
    # Idempotent + never raises; a no-op if pip_system_certs isn't active.
    from common.utils.ssl_cert_cache import install_cached_system_ssl_context
    install_cached_system_ssl_context()

    configdict = _load_configdict_with_timeout(
        env, runas=runas, timeout=config_timeout, retries=config_retries)
    config.init(env, overrides=configoverrides, configdict=configdict, runas=runas)


def kill_stale_server(port, cmdline_must_contain=None):
    """Terminate listeners on ``port`` (Windows netstat + taskkill).

    When ``cmdline_must_contain`` is set (non-empty iterable of substrings), only
    PID(s) whose full command line contains any substring (case-insensitive) are
    killed. If other processes hold the port, this raises immediately after the
    kill pass. Filtering requires ``psutil``.
    """
    import subprocess
    import time

    def listening_pids():
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True,
            text=True,
            check=False,
        )
        pids = set()
        token = f":{port}"
        my_pid = os.getpid()
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) < 5 or parts[-2].upper() != "LISTENING":
                continue
            local_addr = parts[1]
            if not (local_addr.endswith(token) or local_addr.endswith(f"{token}]")):
                continue
            pid = int(parts[-1])
            if pid != my_pid and pid > 0:
                pids.add(pid)
        return pids

    def matching_subset(pids):
        if not cmdline_must_contain:
            return set(pids)
        needles = tuple(s.lower() for s in cmdline_must_contain if s)
        if not needles:
            return set(pids)
        try:
            import psutil
        except ImportError as exc:
            raise RuntimeError(
                "kill_stale_server with cmdline_must_contain requires psutil"
            ) from exc
        matched = set()
        for pid in pids:
            try:
                proc = psutil.Process(pid)
                line = " ".join(str(x) for x in (proc.cmdline() or ())).lower()
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess) as ex:
                logging.warning(
                    "Port %s PID=%s skipped (cannot read cmdline: %s)",
                    port,
                    pid,
                    ex,
                )
                continue
            if any(n in line for n in needles):
                matched.add(pid)
            else:
                logging.warning(
                    "Port %s listener PID=%s cmdline does not match %s; not killing",
                    port,
                    pid,
                    needles,
                )
        return matched

    everyone = listening_pids()
    targets = matching_subset(everyone)
    blocked = everyone - targets
    if blocked:
        logging.warning(
            "Port %s has listener PID(s) we are not killing (cmdline filter): %s",
            port,
            sorted(blocked),
        )

    for pid in targets:
        logging.warning("Killing stale server process PID=%s on port %s", pid, port)
        subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            capture_output=True,
            text=True,
            check=False,
        )

    deadline = time.time() + 10
    while time.time() < deadline:
        remaining = listening_pids()
        if not remaining:
            return
        rem_targets = matching_subset(remaining)
        rem_blocked = remaining - rem_targets
        if rem_blocked:
            raise RuntimeError(
                f"Port {port} still held by non-matching PID(s) {sorted(rem_blocked)} "
                f"(cmdline filters {tuple(cmdline_must_contain or ())!r})"
            )
        if not rem_targets:
            return
        time.sleep(0.5)

    remaining = listening_pids()
    if remaining:
        raise RuntimeError(
            f"Port {port} still held by PID(s): {sorted(remaining)}"
        )


def checkif_childprocess(parent_proc=None, is_debug=False):
    if is_debug:
        return False

    import psutil
    parent_proc = parent_proc or 'python.exe'
    fpath = (psutil.Process().parent().exe()[-len(parent_proc):] == parent_proc)
    return fpath


if __name__ == '__main__':
    checkif_childprocess()
