import functools
import logging
import os
import base64
import json
from typing import Callable, Tuple, Dict, Any

import pandas as pd


def encode_params(func_name: str, args: tuple, kwargs: dict) -> Tuple[str, str]:
    is_method = args and hasattr(args[0], func_name)
    arg_values = args[1:] if is_method else args

    params_dict = {
        "func": func_name,
        "args": [str(a) for a in arg_values],
        "kwargs": {k: str(v) for k, v in kwargs.items()}
    }

    json_str = json.dumps(params_dict)
    encoded = base64.urlsafe_b64encode(json_str.encode()).decode()

    if len(encoded) > 100:
        encoded = encoded[:100]

    params_info = f"Function: {func_name}\n"
    if arg_values:
        params_info += "Args:\n" + "\n".join(f"  - {arg}" for arg in arg_values)
        params_info += "\n"
    if kwargs:
        params_info += "Kwargs:\n" + "\n".join(f"  - {k}: {v}" for k, v in sorted(kwargs.items()))

    return encoded, params_info


def decode_params(encoded_key: str) -> Dict[str, Any]:
    try:
        padded = encoded_key + '=' * (4 - len(encoded_key) % 4)
        json_str = base64.urlsafe_b64decode(padded).decode()
        return json.loads(json_str)
    except:
        return {"error": "Cannot decode truncated key", "partial": encoded_key}


def cache_data(cache_dir: str = 'C:/temp/_cache_util_dir'):
    os.makedirs(cache_dir, exist_ok=True)

    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            cache_key, params_info = encode_params(func.__name__, args, kwargs)
            cache_file = os.path.join(cache_dir, f"{cache_key}.parquet")

            if os.path.exists(cache_file):
                logging.info(f"Using cached data from {cache_file} with params:\n{params_info}")
                return pd.read_parquet(cache_file)

            result = func(*args, **kwargs)
            if isinstance(result, pd.DataFrame) and not result.empty:
                result.to_parquet(cache_file)
                logging.info(f"Cached result to {cache_file} with params:\n{params_info}")

            return result

        return wrapper

    return decorator