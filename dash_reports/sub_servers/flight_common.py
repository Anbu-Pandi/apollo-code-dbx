import json
import os

import numpy as np
import pandas as pd
import pyarrow.flight as flight


def sanitize(obj):
    if isinstance(obj, float) and (np.isnan(obj) or np.isinf(obj)):
        return None
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return None if np.isnan(obj) else float(obj)
    if isinstance(obj, np.ndarray):
        return [sanitize(x) for x in obj.tolist()]
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    if isinstance(obj, pd.DataFrame):
        return sanitize(obj.to_dict("records"))
    if hasattr(obj, "to_plotly_json"):
        return sanitize(obj.to_plotly_json())
    if isinstance(obj, dict):
        return {str(k): sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize(x) for x in obj]
    return obj


def json_result(obj):
    return flight.Result(json.dumps(sanitize(obj), default=str).encode("utf-8"))


def error_result(msg):
    return flight.Result(json.dumps({"success": False, "error": str(msg)}).encode("utf-8"))


def cert_dir(app):
    from apo_config.config import config
    return config.getconfig(app, "certificate_dir") or config.getconfig("dash_reports", "certificate_dir")


def load_tls_certs(app):
    root = cert_dir(app)
    if not root:
        return None
    crt, key = os.path.join(root, "server.crt"), os.path.join(root, "server.key")
    return (crt, key) if os.path.exists(crt) and os.path.exists(key) else None


def load_tls_root_cert(app):
    root = cert_dir(app)
    crt = os.path.join(root, "server.crt") if root else None
    if crt and os.path.exists(crt):
        with open(crt, "rb") as f:
            return f.read()
    return None
