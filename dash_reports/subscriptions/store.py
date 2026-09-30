import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def key(subject, report_id):
        return hashlib.sha256(f"{subject}\0{report_id}".encode()).hexdigest()

    def path(self, key):
        if not re.fullmatch(r"[0-9a-f]{64}", key):
            raise ValueError("Invalid subscription identifier.")
        return self.root / f"{key}.json"

    @contextmanager
    def locked(self, key):
        with FileLock(str(self.path(key)) + ".lock", timeout=2):
            yield

    def read(self, key):
        path = self.path(key)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def write(self, key, value):
        path = self.path(key)
        fd, temporary = tempfile.mkstemp(dir=self.root, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def keys(self):
        return [path.stem for path in self.root.glob("*.json") if re.fullmatch(r"[0-9a-f]{64}", path.stem)]
