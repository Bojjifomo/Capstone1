"""State bot di satu file JSON: posisi LP (per nama koin) + daftar subscriber."""
import json
import os
import tempfile
import time
from pathlib import Path


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.data = {"positions": {}, "subscribers": [], "offset": 0}
        if self.path.exists():
            self.data.update(json.loads(self.path.read_text()))

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, self.path)

    # posisi
    def get(self, coin):
        return self.data["positions"].get(coin.lower())

    def positions(self):
        return sorted(self.data["positions"].values(), key=lambda p: p["updated"], reverse=True)

    def open_position(self, call):
        pos = {k: call[k] for k in ("coin", "ca", "fee", "range")}
        pos.update(status="open", updated=int(time.time()))
        self.data["positions"][pos["coin"]] = pos
        self.save()
        return pos

    def set_status(self, coin, status):
        pos = self.data["positions"][coin.lower()]
        pos.update(status=status, updated=int(time.time()))
        self.save()
        return pos

    def delete(self, coin):
        removed = self.data["positions"].pop(coin.lower(), None)
        self.save()
        return removed

    # subscriber
    def subscribers(self):
        return list(self.data["subscribers"])

    def subscribe(self, chat_id):
        if chat_id in self.data["subscribers"]:
            return False
        self.data["subscribers"].append(chat_id)
        self.save()
        return True

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
        self.data["offset"] = value
        self.save()
