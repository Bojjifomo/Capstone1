"""Live samples in memory, one row per minute in SQLite, energy (Wh) integrated per sample."""
import sqlite3
import threading
import time
from collections import deque
from datetime import datetime, timedelta

FIELDS = ("watts", "cpu_w", "gpu_w", "ane_w", "chip_w")

SCHEMA = """
CREATE TABLE IF NOT EXISTS minutes (
  ts INTEGER PRIMARY KEY,      -- start of the minute, unix seconds
  avg_w REAL, max_w REAL, min_w REAL,
  cpu_w REAL, gpu_w REAL,
  wh REAL,                     -- energy used in this minute
  n INTEGER
);
"""


def local_midnight(now):
    d = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(d.timestamp())


class Meter:
    def __init__(self, db_path, live_seconds=3600, keep_days=90, max_gap=10.0):
        self.lock = threading.Lock()
        self.live = deque(maxlen=live_seconds)   # (ts, watts, cpu_w, gpu_w)
        self.latest = None
        self.max_gap = max_gap                   # longer gaps (sleep, restart) count no energy
        self.keep_days = keep_days
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.executescript(SCHEMA)
        self._minute = None                      # [ts, sum_w, max_w, min_w, sum_cpu, sum_gpu, wh, n]
        self._last_ts = None
        self.source = None
        self.error = None

    # -- writing --------------------------------------------------------------------------
    def add(self, sample, now=None):
        now = time.time() if now is None else now
        w = float(sample["watts"])
        with self.lock:
            dt = 0.0
            if self._last_ts is not None:
                gap = now - self._last_ts
                if 0 < gap <= self.max_gap:
                    dt = gap
            self._last_ts = now
            wh = w * dt / 3600.0
            m = int(now // 60 * 60)
            if self._minute and self._minute[0] != m:
                self._flush()
            if not self._minute:
                self._minute = [m, 0.0, w, w, 0.0, 0.0, 0.0, 0]
            b = self._minute
            b[1] += w
            b[2] = max(b[2], w)
            b[3] = min(b[3], w)
            b[4] += sample.get("cpu_w") or 0.0
            b[5] += sample.get("gpu_w") or 0.0
            b[6] += wh
            b[7] += 1
            self.latest = dict(sample, ts=now)
            self.live.append((round(now, 1), round(w, 2), round(sample.get("cpu_w") or 0, 2),
                              round(sample.get("gpu_w") or 0, 2)))
            self.error = None

    def _flush(self):
        b = self._minute
        self._minute = None
        if not b or not b[7]:
            return
        n = b[7]
        self.db.execute(
            "INSERT INTO minutes (ts, avg_w, max_w, min_w, cpu_w, gpu_w, wh, n) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(ts) DO UPDATE SET avg_w=(avg_w*n+excluded.avg_w*excluded.n)/(n+excluded.n), "
            "max_w=max(max_w,excluded.max_w), min_w=min(min_w,excluded.min_w), "
            "cpu_w=(cpu_w*n+excluded.cpu_w*excluded.n)/(n+excluded.n), "
            "gpu_w=(gpu_w*n+excluded.gpu_w*excluded.n)/(n+excluded.n), "
            "wh=wh+excluded.wh, n=n+excluded.n",
            (b[0], b[1] / n, b[2], b[3], b[4] / n, b[5] / n, b[6], n))
        self.db.execute("DELETE FROM minutes WHERE ts < ?", (int(time.time()) - self.keep_days * 86400,))
        self.db.commit()

    def flush(self):
        with self.lock:
            self._flush()

    # -- reading --------------------------------------------------------------------------
    def _open_minute_wh(self, since):
        b = self._minute
        return b[6] if b and b[0] >= since else 0.0

    def energy_since(self, since):
        with self.lock:
            row = self.db.execute("SELECT COALESCE(SUM(wh),0) FROM minutes WHERE ts >= ?", (since,)).fetchone()
            return row[0] + self._open_minute_wh(since)

    def live_since(self, since):
        with self.lock:
            return [p for p in self.live if p[0] >= since]

    def history(self, since, bucket):
        """Average watts per `bucket` seconds since `since`: [(ts, avg_w, max_w, wh)]."""
        with self.lock:
            rows = self.db.execute(
                "SELECT (ts/?)*? AS b, AVG(avg_w), MAX(max_w), SUM(wh) FROM minutes WHERE ts >= ? "
                "GROUP BY b ORDER BY b", (bucket, bucket, since)).fetchall()
            out = [[r[0], round(r[1], 2), round(r[2], 2), round(r[3], 3)] for r in rows]
            b = self._minute
            if b and b[7] and b[0] >= since:
                key = b[0] // bucket * bucket
                avg = b[1] / b[7]
                if out and out[-1][0] == key:
                    out[-1][2] = round(max(out[-1][2], b[2]), 2)
                    out[-1][3] = round(out[-1][3] + b[6], 3)
                else:
                    out.append([key, round(avg, 2), round(b[2], 2), round(b[6], 3)])
            return out

    def daily(self, days):
        """Energy per local day, newest last: [(YYYY-MM-DD, wh, avg_w, max_w)]."""
        now = time.time()
        start = local_midnight(now)
        out = []
        for i in range(days - 1, -1, -1):
            day0 = int((datetime.fromtimestamp(start) - timedelta(days=i)).timestamp())
            day1 = int((datetime.fromtimestamp(day0) + timedelta(days=1)).timestamp())
            with self.lock:
                r = self.db.execute("SELECT COALESCE(SUM(wh),0), AVG(avg_w), MAX(max_w) FROM minutes "
                                    "WHERE ts >= ? AND ts < ?", (day0, day1)).fetchone()
                wh = r[0] + (self._open_minute_wh(day0) if i == 0 else 0)
            out.append([datetime.fromtimestamp(day0).strftime("%Y-%m-%d"), round(wh, 3),
                        round(r[1], 2) if r[1] is not None else None,
                        round(r[2], 2) if r[2] is not None else None])
        return out
