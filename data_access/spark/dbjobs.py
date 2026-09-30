"""Databricks Jobs / Compute / Volume helpers shared by the parallel backends.

All Databricks-SDK plumbing for spinning up ephemeral job clusters and shipping
files lives HERE (data_access/spark owns the WorkspaceClient + jobs/compute surface,
see SparkManager). The ray backend (data_access/ray) imports these helpers instead of
re-implementing job submission. Nothing in this module is ray- or spark-specific.

Config: values come from the `apo_data_access.parallel` app, falling back to the
constants proven in credit_strategy/beta_estimation/scale_probe/submit_scale_probe.py.
Override any of them by adding the key under `apo_data_access.parallel` in config.
"""
import io
import logging
import math
import posixpath
import time

from databricks.sdk import WorkspaceClient
from databricks.sdk.service import compute, jobs
from databricks.sdk.service.workspace import ImportFormat

from apo_config.config import config

SPARK_APP = 'apo_data_access.spark'
PARALLEL_APP = 'apo_data_access.parallel'

# Libs come from the volume requirements.txt — the same pinned file the cluster-policy setup
# uses — attached as a single `requirements` task library. It installs the APO packages
# (common/data_access/credit_strategy/...) AND all third-party deps, so we don't hardcode
# individual wheels. Uses the `with_apo_req_v2_jc` policy (no init scripts); the
# `with_apo_libs_jc` init-script policy broke the ML env on ephemeral clusters.
_DEFAULTS = {
    'job_policy_id': '001B72F617750156',              # with_apo_req_v2_jc (no init scripts)
    'job_spark_version': '15.4.x-scala2.12',          # non-ML (thread/ephemeral)
    'job_spark_version_ml': '15.4.x-cpu-ml-scala2.12',  # ML runtime (ray preinstalled)
    'job_node_type_id': 'Standard_DS3_v2',
    'cores_per_node': 4,                              # Standard_DS3_v2 has 4 vCPUs
    'job_max_nodes': 30,                              # safety ceiling on worker nodes
    'workspace_dir': None,                            # default computed from current user
    'marshal_volume_dir': '/Volumes/apollo_market_data_dev/default/apo_parallel',
    'requirements': '/Volumes/apollo_market_data_dev/default/libs/requirements.txt',
}


def pconf(key):
    """Read an `apo_data_access.parallel` config value, falling back to the proven default."""
    val = config.getconfig(PARALLEL_APP, key)
    return val if val is not None else _DEFAULTS.get(key)


def require_env_config():
    """Fail fast outside dev when apo_data_access.parallel isn't configured: the _DEFAULTS
    target the DEV catalog/volume/policy, so a prod/uat run with no override would silently
    operate against dev resources."""
    env = config.getenv()
    if env and env != 'dev':
        missing = [k for k in ('marshal_volume_dir', 'requirements', 'job_policy_id')
                   if config.getconfig(PARALLEL_APP, k) is None]
        if missing:
            raise RuntimeError(
                f"parallel backend in env={env!r} but apo_data_access.parallel is unconfigured "
                f"(missing {missing}); the code defaults target the dev catalog. Set these in "
                f"config for {env} before running.")


def client():
    """WorkspaceClient against the configured Databricks workspace (same host/token as spark)."""
    host = config.getconfig(SPARK_APP, 'databricks_host')
    token = config.getconfig(SPARK_APP, 'databricks_pat')
    if host and not host.startswith('http'):
        host = f'https://{host}'
    return WorkspaceClient(host=host, token=token)


def current_user(w):
    return w.current_user.me().user_name


def workspace_dir(w):
    return pconf('workspace_dir') or f'/Users/{current_user(w)}/apo_parallel'


def upload_workspace_file(w, file_bytes, remote_path):
    """Upload bytes as a workspace FILE (ImportFormat.AUTO — must NOT become a NOTEBOOK)."""
    parent = posixpath.dirname(remote_path)
    w.workspace.mkdirs(parent)
    try:
        w.workspace.delete(remote_path, recursive=False)
    except Exception:
        pass
    w.workspace.upload(remote_path, file_bytes, overwrite=True, format=ImportFormat.AUTO)
    st = w.workspace.get_status(remote_path)
    if st.object_type and st.object_type.value != 'FILE':
        raise RuntimeError(f'workspace asset is {st.object_type}, need FILE at {remote_path}')
    logging.info(f'uploaded workspace file: {remote_path}')
    return remote_path


def volume_put(w, remote_path, data):
    """Write bytes to a UC volume path via the Files API (used from a local driver)."""
    w.files.upload(remote_path, io.BytesIO(data), overwrite=True)
    return remote_path


def volume_get(w, remote_path):
    """Read bytes from a UC volume path via the Files API (used from a local driver)."""
    return w.files.download(remote_path).contents.read()


def worker_node_bounds(max_workers=None, n_tasks=None, num_cpus_per_task=1, cores_per_node=None):
    """Worker nodes sized to desired task CONCURRENCY, returned as (min, max).

    max_workers = target concurrent tasks (same meaning as thread/process). Each task needs
    num_cpus_per_task cores; a node has `cores_per_node` cores. So
    nodes = ceil(min(max_workers, n_tasks) * num_cpus_per_task / cores_per_node), capped at the
    configured node ceiling. e.g. 8 one-core tasks on 4-core nodes -> 2 nodes (not 8); 1 task -> 1.
    Returns a fixed size (min == max) so Ray has exactly the cores it needs.
    """
    cores_per_node = max(1, int(cores_per_node or pconf('cores_per_node')))
    ceiling = int(pconf('job_max_nodes'))
    wanted = [int(v) for v in (max_workers, n_tasks) if v]
    tasks = min(wanted) if wanted else 1
    nodes = max(1, math.ceil(tasks * max(1, int(num_cpus_per_task)) / cores_per_node))
    nodes = min(nodes, ceiling)
    return nodes, nodes


def new_cluster_spec(w, use_ml_runtime=False, min_workers=None, max_workers=None, node_type_id=None):
    """SINGLE_USER ephemeral job-cluster spec. ML runtime when Ray is needed (preinstalled).
    Caller supplies the node bounds (see worker_node_bounds); falls back to config defaults.

    SINGLE_USER (Dedicated) is required both for the proven wheel/UC recipe and for
    setup_ray_cluster (Ray needs Dedicated or no-isolation-shared, never standard-shared).
    """
    if min_workers is None or max_workers is None:
        min_workers, max_workers = worker_node_bounds()
    min_workers, max_workers = int(min_workers), int(max_workers)
    spec = dict(
        policy_id=pconf('job_policy_id'),
        spark_version=pconf('job_spark_version_ml') if use_ml_runtime else pconf('job_spark_version'),
        node_type_id=node_type_id or pconf('job_node_type_id'),
        data_security_mode=compute.DataSecurityMode.SINGLE_USER,
        single_user_name=current_user(w),
        spark_env_vars={'APO_ENV': config.getenv()},
        apply_policy_default_values=True,
    )
    # fixed size when bounds are equal (Databricks rejects autoscale with min == max)
    if min_workers == max_workers:
        spec['num_workers'] = max_workers
    else:
        spec['autoscale'] = compute.AutoScale(min_workers=min_workers, max_workers=max_workers)
    return compute.ClusterSpec(**spec)


def task_libraries():
    """Single `requirements` library -> the volume requirements.txt (APO packages + all deps,
    including ray once the environment is rebuilt). No hardcoded per-package list."""
    req = pconf('requirements')
    return [compute.Library(requirements=req)] if req else None


def cleanup_run(w, workspace_dir=None, volume_files=None):
    """Best-effort removal of a run's per-run launcher dir and volume marshalling files.
    Swallows errors (cleanup must never fail a successful run)."""
    if workspace_dir:
        try:
            w.workspace.delete(workspace_dir, recursive=True)
        except Exception:
            logging.warning(f'cleanup: could not delete workspace dir {workspace_dir}')
    for path in (volume_files or []):
        try:
            w.files.delete(path)
        except Exception:
            logging.warning(f'cleanup: could not delete volume file {path}')


def first_task_error(w, run):
    """Best-effort: the first task's error message + trace tail, for diagnosing a failed run."""
    try:
        if run and run.tasks:
            out = w.jobs.get_run_output(run_id=run.tasks[0].run_id)
            return out.error or (out.error_trace or '')[-1500:] or None
    except Exception as e:
        return f'(could not fetch task error: {e})'
    return None


def submit_python_task_and_poll(w, run_name, python_file, parameters, new_cluster,
                                libraries=None, poll_sec=30, timeout_sec=7200):
    """Submit a one-shot spark_python_task on an ephemeral cluster and poll to completion.

    Libraries normally come from the cluster POLICY (with_apo_libs_jc init script), so
    `libraries` defaults to None. Returns (run_id, result_state, run). Do NOT set cluster_name
    on new_cluster (rejected by jobs.submit). python_file must be a /Users/... workspace path
    with source=WORKSPACE.
    """
    task = jobs.SubmitTask(
        task_key=(run_name or 'apo_parallel')[:90],
        new_cluster=new_cluster,
        spark_python_task=jobs.SparkPythonTask(python_file=python_file,
                                               source=jobs.Source.WORKSPACE,
                                               parameters=parameters),
        libraries=libraries,
    )
    run = w.jobs.submit(run_name=run_name, tasks=[task])
    run_id = run.run_id
    logging.info(f'submitted parallel run_id={run_id} file={python_file} params={parameters}')

    t0 = time.time()
    r = None
    life = None
    terminal = ('TERMINATED', 'SKIPPED', 'INTERNAL_ERROR')
    while time.time() - t0 < timeout_sec:
        r = w.jobs.get_run(run_id)
        life = r.state.life_cycle_state.value if r.state and r.state.life_cycle_state else None
        result = r.state.result_state.value if r.state and r.state.result_state else None
        logging.info(f'parallel run {run_id} life={life} result={result}')
        if life in terminal:
            break
        time.sleep(poll_sec)

    if life not in terminal:
        # timed out while still running — cancel so we don't orphan a billing cluster
        logging.warning(f'parallel run {run_id} timed out after {timeout_sec}s; cancelling')
        try:
            w.jobs.cancel_run(run_id)
        except Exception:
            logging.warning(f'could not cancel timed-out run {run_id}')

    result_state = r.state.result_state.value if r and r.state and r.state.result_state else None
    return run_id, result_state, r
