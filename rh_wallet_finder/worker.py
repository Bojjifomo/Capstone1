"""Background loop for the dashboard: sync, score, repeat."""
import threading
import time
import traceback
from collections import deque

import config
import db
import indexer
from rpc import RPCError, make_rpcs
from scorer import Scorer

SEGMENTS_PER_CYCLE = 6  # score in between, so a long backfill shows results early


class Worker:
    def __init__(self, interval=600):
        self.interval = interval
        self.logs = deque(maxlen=400)
        self.state = "idle"
        self.error = None
        self.progress = None
        self._wake = threading.Event()
        self._stop = False

    def log(self, msg):
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        self.logs.append(line)
        print(line, flush=True)

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def kick(self):
        self._wake.set()

    def stop(self):
        self._stop = True
        self._wake.set()

    def _loop(self):
        while not self._stop:
            cfg = config.load_env()
            wait = self.interval
            if not cfg["RPC_URL"]:
                self.state = "no_rpc"
                self.log("No RPC link yet. Open Settings (⚙) and paste your Robinhood Chain RPC URL.")
                self._wake.wait()
                self._wake.clear()
                continue
            con = None
            try:
                con = db.connect()
                rpc, call_rpc = make_rpcs(cfg)
                self.state = "syncing"
                self.log("Sync: scanning new LP activity…")
                res = indexer.sync(con, rpc, cfg, self.log, max_segments=SEGMENTS_PER_CYCLE,
                                   stop=lambda: self._stop, call_rpc=call_rpc)
                self.progress = res
                if not res["done"]:
                    left = res["back"] - res["target"]
                    self.log(f"Backfill: {left:,} blocks of history still to load")
                    wait = 1
                self.state = "scoring"
                self.log("Score: pricing positions and ranking wallets…")
                out = Scorer(con, call_rpc, cfg, self.log).run(stop=lambda: self._stop)
                calls = rpc.calls + (call_rpc.calls if call_rpc is not rpc else 0)
                self.log(f"Scored {out['scored']:,} wallets ({out['priced']:,} positions priced, "
                         f"{calls:,} RPC calls)")
                self.error = None
            except RPCError as e:
                self.error = str(e)
                self.log(f"RPC error: {e}")
                wait = 60
            except Exception as e:  # noqa: BLE001 - keep the loop alive, show it in the UI
                self.error = f"{type(e).__name__}: {e}"
                self.log("Error: " + self.error)
                traceback.print_exc()
                wait = 120
            finally:
                if con is not None:
                    con.close()
            self.state = "idle"
            self._wake.wait(wait)
            self._wake.clear()
