"""Small JSON files next to server.py, written atomically."""
import json
import os
import tempfile
import threading
from pathlib import Path

DATA_DIR = Path(os.environ.get("AGENT_OFFICE_DATA") or Path(__file__).resolve().parent)
_lock = threading.Lock()


def path(name):
    return DATA_DIR / name


def load(name, default):
    try:
        with open(path(name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save(name, data, private=False):
    target = path(name)
    with _lock:
        fd, tmp = tempfile.mkstemp(prefix="." + name, dir=str(DATA_DIR))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            if private:
                os.chmod(tmp, 0o600)
            os.replace(tmp, target)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
