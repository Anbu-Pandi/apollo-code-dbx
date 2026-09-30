import os
from datetime import datetime

ENV_VAR = 'APO_HEARTBEAT_FILE'


def beat(path=None):
    path = path or os.environ.get(ENV_VAR)
    if not path:
        return
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, 'w') as handle:
        handle.write(datetime.now().isoformat())
