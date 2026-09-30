import json
import os
import pickle
import uuid


def _publish(path, write):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    temp_path = f"{path}.{uuid.uuid4().hex}.tmp"
    try:
        write(temp_path)
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
    return path


def write_parquet(frame, path, **kwargs):
    return _publish(path, lambda temp: frame.to_parquet(temp, **kwargs))


def write_json(payload, path, **kwargs):
    def write(temp):
        with open(temp, "w", encoding="utf-8") as output:
            json.dump(payload, output, **kwargs)

    return _publish(path, write)


def write_text(text, path):
    def write(temp):
        with open(temp, "w", encoding="utf-8") as output:
            output.write(text)

    return _publish(path, write)


def write_pickle(value, path):
    def write(temp):
        with open(temp, "wb") as output:
            pickle.dump(value, output)

    return _publish(path, write)
