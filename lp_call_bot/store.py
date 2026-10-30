"""State bot di satu file JSON: posisi LP (per nama koin) + daftar subscriber."""
import json
import os
import tempfile
import threading
import time
from pathlib import Path


def locked(fn):
    """Bot (thread Telegram) dan watcher (thread chain) nulis ke state yang sama."""
    def wrapper(self, *args, **kwargs):
        with self.lock:
            return fn(self, *args, **kwargs)
    wrapper.__name__ = fn.__name__
    return wrapper


class Store:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.path = Path(path)
        self.data = {"positions": {}, "subscribers": [], "offset": 0}
        if self.path.exists():
            self.data.update(json.loads(self.path.read_text()))

    @locked
    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, self.path)

    # posisi
    @locked
    def get(self, coin):
        return self.data["positions"].get(coin.lower())

    @locked
    def positions(self):
        return sorted(self.data["positions"].values(), key=lambda p: p["updated"], reverse=True)

    @locked
    def open_position(self, call):
        pos = {k: call[k] for k in ("coin", "ca", "fee", "range")}
        pos.update(status="open", updated=int(time.time()))
        self.data["positions"][pos["coin"]] = pos
        self.save()
        return pos

    @locked
    def set_status(self, coin, status):
        pos = self.data["positions"][coin.lower()]
        pos.update(status=status, updated=int(time.time()))
        self.save()
        return pos

    @locked
    def delete(self, coin):
        removed = self.data["positions"].pop(coin.lower(), None)
        self.save()
        return removed

    # subscriber
    @locked
    def subscribers(self):
        return list(self.data["subscribers"])

    @locked
    def subscribe(self, chat_id):
        if chat_id in self.data["subscribers"]:
            return False
        self.data["subscribers"].append(chat_id)
        self.save()
        return True

    @locked
    def unsubscribe(self, chat_id):
        if chat_id not in self.data["subscribers"]:
            return False
        self.data["subscribers"].remove(chat_id)
        self.save()
        return True

    @property
    def offset(self):
        return self.data["offset"]

    @offset.setter
    def offset(self, value):
        with self.lock:
            self.data["offset"] = value
            self.save()
