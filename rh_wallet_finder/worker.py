"""Background loop for the app: sync, score, repeat every SYNC_MINUTES."""
import threading
import time
import traceback
from collections import deque

import config
import db
import indexer
import smartlp
from rpc import RPCError, make_rpcs
from scorer import Scorer

SEGMENTS_PER_CYCLE = 3  # score in between, so a long backfill shows results early


class Worker:
    def __init__(self):
        self.logs = deque(maxlen=400)
        self.state = "idle"
        self.error = None
        self.progress = None
        self.cycle_at = None
        self._wake = threading.Event()
        self._stop = False
        self._ranked_once = False

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

    def import_smart_lp(self):
        try:
            con = db.connect()
            try:
                got = smartlp.import_all(con, log=self.log)
            finally:
                con.close()
            if (got["saved"] or got["ranked"]) and got != getattr(self, "_smartlp_last", None):
                self.log(f"RH Smart LP Finder: {got['saved']} saved wallets, {got['ranked']} pool placings")
            self._smartlp_last = got
        except Exception as e:  # noqa: BLE001 - optional extra, never stops the loop
            self.log(f"RH Smart LP Finder import skipped: {e}")

    def _loop(self):
        while not self._stop:
            cfg = config.load_env()
            wait = max(30, float(cfg.get("SYNC_MINUTES") or 5) * 60)
            self.import_smart_lp()
            if not cfg["RPC_URL"]:
                self.state = "no_rpc"
                self.log("No RPC link yet. Open the Settings tab and paste your Robinhood Chain RPC URL.")
                self._wake.wait()
                self._wake.clear()
                continue
            con = None
            try:
                con = db.connect()
                rpc, call_rpc = make_rpcs(cfg)
                if not self._ranked_once:  # current rules on the board now, not after a long sync
                    self._ranked_once = True
                    self.state = "scoring"
                    self.log("Rank: re-ranking what is already priced with the current rules…")
                    self.log(f"Ranked {Scorer(con, call_rpc, cfg, self.log).rerank():,} wallets")
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
            self.cycle_at = time.time()
            self._wake.wait(wait)
            self._wake.clear()
