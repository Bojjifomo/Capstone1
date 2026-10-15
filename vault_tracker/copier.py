"""Copytrade bot: mirrors the leader's DLMM positions into your own wallet.

Follows the leader's opens and closes and, optionally, the liquidity it adds or withdraws later
(in proportion). On top of that, your own exits: take profit, stop loss, out of range for too long,
manual withdraw / add / close, and an emergency exit that closes everything and pauses the bot.

Modes: off | dry (plan + on-chain simulation, nothing is signed) | live (signs and sends).
Every live action goes through the limits below; three failures in a row pause the bot.
"""

import json
import os
import threading
import time

from solders.keypair import Keypair
from solders.pubkey import Pubkey

from . import copytrade, dlmm, dlmm_tx, jupiter
from .alerts import fmt_num, fmt_usd
from .tokens import SOL_MINT, USDC_MINT

BUDGET_MINTS = {"SOL": SOL_MINT, "USDC": USDC_MINT}
SIZE_MODES = ("usd", "ratio", "vault")
DEFAULTS = {
    "run": "off",               # off | dry | live
    "budget": "SOL",            # token the bot spends and swaps back to
    "size_mode": "usd",         # usd: size_usd · ratio: leader position x size_ratio · vault: same % of wallet
    "size_usd": 20.0,           # per copied position (size_mode usd)
    "size_ratio": 0.0,          # leader position value x ratio (size_mode ratio)
    "follow_pct": 100.0,        # size_mode vault: 100 = same weight as in the vault, 50 = half of it
    "max_usd": 100.0,           # hard cap per position (cost basis, adds included)
    "max_open": 3,              # bot positions open at once
    "reserve_sol": 0.1,         # never spend below this (fees, rent)
    "max_price_move": 2.0,      # % the price may have moved since the leader opened
    "max_event_age": 180,       # s; older leader events are not copied (e.g. after downtime)
    "strategy": "spot",         # spot | curve | bidask
    "slippage_bps": 100,        # Jupiter swap slippage
    "exit_swap": True,          # swap tokens back to the budget token after closing / withdrawing
    "priority": 50_000,         # micro-lamports per compute unit
    "max_new_bin_arrays": 1,    # bin arrays the bot may create (~0.07 SOL rent each)
    "follow_liquidity": True,   # copy the leader's later adds / withdrawals in proportion
    "min_action_usd": 2.0,      # follow adds / withdrawals smaller than this are ignored
    "tp_pct": 0.0,              # close at +tp_pct % profit (fees included); 0 = off
    "sl_pct": 0.0,              # close at -sl_pct % loss; 0 = off
    "oor_close_min": 0,         # close after this many minutes out of range; 0 = off
    "tg_commands": True,        # accept /status, /tarik, /darurat ... from your Telegram chat
}
MAX_FAILURES = 3
FEE_BUFFER_LAMPORTS = 10_000_000  # 0.01 SOL for tx fees / ATAs
PENDING_TTL = 900               # s to wait for liquidity in a position the leader opened empty
HITS_TO_EXIT = 2                # consecutive checks past TP / SL before closing (no exits on one bad price)
DONE_KEPT = 300


def load_keypair(path):
    with open(os.path.expanduser(path)) as f:
        raw = f.read().strip()
    if raw.startswith("["):
        return Keypair.from_bytes(bytes(json.loads(raw)))
    return Keypair.from_base58_string(raw)


def save_keypair(kp, path):
    path = os.path.expanduser(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(list(bytes(kp)), f)
    return path


def settings_from_config(cfg):
    def num(key, default, cast=float):
        try:
            return cast(float(cfg.get(key) or default))
        except ValueError:
            return default
    ratio = num("COPY_SIZE_RATIO", 0.0)
    return {
        "run": (cfg.get("COPY_RUN") or "off").lower(),
        "budget": (cfg.get("COPY_BUDGET") or "SOL").upper(),
        "size_mode": (cfg.get("COPY_SIZE_MODE") or ("ratio" if ratio > 0 else "usd")).lower(),
        "size_usd": num("COPY_SIZE_USD", 20.0), "size_ratio": ratio,
        "follow_pct": num("COPY_FOLLOW_PCT", 100.0),
        "max_usd": num("COPY_MAX_USD", 100.0), "max_open": num("COPY_MAX_OPEN", 3, int),
        "reserve_sol": num("COPY_RESERVE_SOL", 0.1), "max_price_move": num("COPY_MAX_PRICE_MOVE", 2.0),
        "max_event_age": num("COPY_MAX_EVENT_AGE", 180, int),
        "strategy": (cfg.get("COPY_STRATEGY") or "spot").lower(),
        "slippage_bps": num("COPY_SLIPPAGE_BPS", 100, int),
        "exit_swap": (cfg.get("COPY_EXIT_SWAP") or "1") != "0",
        "priority": num("COPY_PRIORITY", 50_000, int),
        "max_new_bin_arrays": num("COPY_MAX_NEW_BIN_ARRAYS", 1, int),
        "follow_liquidity": (cfg.get("COPY_FOLLOW_LIQUIDITY") or "1") != "0",
        "min_action_usd": num("COPY_MIN_ACTION_USD", 2.0),
        "tp_pct": num("COPY_TP_PCT", 0.0), "sl_pct": num("COPY_SL_PCT", 0.0),
        "oor_close_min": num("COPY_OOR_CLOSE_MIN", 0, int),
        "tg_commands": (cfg.get("COPY_TG_COMMANDS") or "1") != "0",
    }


def x_share(lower, upper, active):
    """Value share of token X for a spot-shaped range: bins above the active bin hold X."""
    if upper < active:
        return 0.0
    if lower > active:
        return 1.0
    return (upper - active + 0.5) / (upper - lower + 1)


def follow_plan(add_usd, remove_usd, leader_now_usd):
    """What the leader did to one position in one poll, as ("add", usd, before) / ("remove", fraction, before)."""
    before = leader_now_usd - add_usd + remove_usd
    net = add_usd - remove_usd
    if net > 0:
        return "add", net, max(0.0, before)
    if net < 0:
        if leader_now_usd < 0.5 or before <= 0:
            return "remove", 1.0, before
        f = min(1.0, -net / before)
        return "remove", 1.0 if f >= 0.97 else f, before
    return None, 0.0, before


def _cost(rec):
    """Cost basis of a bot position (USD in, minus what was withdrawn)."""
    return rec.get("cost_usd", rec.get("usd") or 0)


class Skip(Exception):
    pass


class Copier:
    def __init__(self, rpc, tokens, prices, settings, copy_mode, state_dir=".vault_tracker", keypair=None,
                 sim_wallet=None):
        self.rpc, self.tokens, self.prices = rpc, tokens, prices
        self.s = {**DEFAULTS, **settings}
        if "size_mode" not in settings and self.s["size_ratio"] > 0:
            self.s["size_mode"] = "ratio"
        if self.s["run"] not in ("off", "dry", "live"):
            raise ValueError("COPY_RUN harus off, dry, atau live")
        if self.s["budget"] not in BUDGET_MINTS:
            raise ValueError("COPY_BUDGET harus SOL atau USDC")
        if self.s["strategy"] not in dlmm_tx.STRATEGY:
            raise ValueError("COPY_STRATEGY harus spot, curve, atau bidask")
        if self.s["size_mode"] not in SIZE_MODES:
            raise ValueError("COPY_SIZE_MODE harus usd, ratio, atau vault")
        copytrade.parse_mode(copy_mode)
        self.copy_mode = copy_mode
        self.kp = keypair
        self.wallet = keypair.pubkey() if keypair else (Pubkey.from_string(sim_wallet) if sim_wallet else None)
        if self.s["run"] == "live" and not keypair:
            raise ValueError("mode live butuh COPY_KEYPAIR (file keypair wallet bot)")
        if self.s["run"] == "dry" and not self.wallet:
            raise ValueError("mode dry butuh COPY_KEYPAIR atau Wallet kamu (alamat untuk simulasi)")
        self.budget_mint = BUDGET_MINTS[self.s["budget"]]
        name = str(self.wallet) if self.wallet else "none"
        self.path = os.path.join(state_dir, f"copier-{name}.json")
        try:
            with open(self.path) as f:
                self.state = json.load(f)
        except (OSError, ValueError):
            self.state = {}
        for k, v in (("positions", {}), ("pending", {}), ("done", []), ("log", []), ("failures", 0),
                     ("paused", False), ("pause_reason", "")):
            self.state.setdefault(k, v)
        if self.live:  # dry-run bookkeeping must never mix with real positions
            for k in [k for k, p in self.state["positions"].items() if p.get("virtual")]:
                self.state["positions"].pop(k)
        self._rent = {}
        self.lock = threading.RLock()  # web buttons and Telegram commands run beside the polling loop

    @property
    def live(self):
        return self.s["run"] == "live"

    @property
    def tag(self):
        return "🤖 COPY" if self.live else "🧪 DRY RUN"

    def save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.state, f, indent=1)
        os.replace(tmp, self.path)

    def _note(self, kind, title, lines):
        self.state["log"] = (self.state["log"] + [{"ts": int(time.time()), "kind": kind, "title": title,
                                                   "lines": lines}])[-100:]
        return title, lines

    def _done(self, leader_position):
        if leader_position and leader_position not in self.state["done"]:
            self.state["done"] = (self.state["done"] + [leader_position])[-DONE_KEPT:]

    def pause(self, reason="dijeda manual"):
        with self.lock:
            self.state.update(paused=True, pause_reason=reason)
            self.save()

    def resume(self):
        with self.lock:
            self.state.update(paused=False, failures=0, pause_reason="")
            self.save()

    # ---- wallet --------------------------------------------------------------------

    def balances(self):
        """{"lamports": int, mint: raw_amount} for the bot wallet."""
        out = {"lamports": self.rpc.get_balance(str(self.wallet))}
        for ta in self.rpc.get_token_accounts(str(self.wallet)):
            info = ta["account"]["data"]["parsed"]["info"]
            out[info["mint"]] = out.get(info["mint"], 0) + int(info["tokenAmount"]["amount"])
        return out

    def _rent_for(self, size):
        if size not in self._rent:
            self._rent[size] = self.rpc.call("getMinimumBalanceForRentExemption", [size])
        return self._rent[size]

    def _price(self, mint):
        p = self.prices.prices.get(mint)
        if p is None:
            raise Skip(f"harga {self.tokens.get(mint).symbol} belum diketahui")
        return p

    def position_values(self, keys=None):
        """Live value of bot positions: {key: dict, or None when the account is gone}."""
        keys = [k for k in (self.state["positions"] if keys is None else keys)
                if not self.state["positions"].get(k, {}).get("virtual")]
        if not keys:
            return {}
        out, states = {}, {}
        for k, acc in zip(keys, self.rpc.get_multiple_accounts(keys)):
            data = dlmm.account_bytes(acc)
            if data is None or acc.get("owner") != dlmm.DLMM_PROGRAM:
                out[k] = None
                continue
            states[k] = dlmm.decode_position(k, data)
        pair_keys = sorted({st.lb_pair for st in states.values()})
        pairs = {k: dlmm.decode_lb_pair(k, dlmm.account_bytes(a))
                 for k, a in zip(pair_keys, self.rpc.get_multiple_accounts(pair_keys) if pair_keys else []) if a}
        bin_keys = list(dict.fromkeys(b for st in states.values()
                                      for b in dlmm.bin_arrays_for_range(st.lb_pair, st.lower_bin_id, st.upper_bin_id)))
        bins = {}
        for acc in (self.rpc.get_multiple_accounts(bin_keys) if bin_keys else []):
            data = dlmm.account_bytes(acc)
            if data is not None:
                bins.setdefault(dlmm._pk(data[24:56]), {}).update(dlmm.decode_bin_array(data)[1])
        mints = {m for p in pairs.values() for m in (p.token_x_mint, p.token_y_mint)}
        self.tokens.load(mints)
        try:
            self.prices.refresh(mints, pairs.values())
        except Exception:
            pass  # keep the last known prices
        for k, st in states.items():
            pair = pairs.get(st.lb_pair)
            if pair is None:
                continue
            tx, ty = self.tokens.get(pair.token_x_mint), self.tokens.get(pair.token_y_mint)
            am = dlmm.position_amounts(st, bins.get(st.lb_pair, {}))
            v = [self.prices.usd(tx.mint, tx.ui(am.amount_x)), self.prices.usd(ty.mint, ty.ui(am.amount_y))]
            f = [self.prices.usd(tx.mint, tx.ui(am.fee_x)), self.prices.usd(ty.mint, ty.ui(am.fee_y))]
            out[k] = {"state": st, "pair": pair, "in_range": st.lower_bin_id <= pair.active_id <= st.upper_bin_id,
                      "value_usd": None if None in v else sum(v), "fees_usd": None if None in f else sum(f),
                      "has_liquidity": any(b.share for b in st.bins)}
        return out

    def equity_usd(self, bal=None, values=None):
        """What the bot can work with: SOL above the reserve + USDC + its open positions."""
        bal = self.balances() if bal is None else bal
        psol = self._price(SOL_MINT)
        sol = max(0, bal["lamports"] + bal.get(SOL_MINT, 0) - int(self.s["reserve_sol"] * 1e9)) / 1e9 * psol
        usdc = bal.get(USDC_MINT, 0) / 1e6 * (self.prices.prices.get(USDC_MINT) or 1.0)
        values = self.position_values() if values is None else values
        pos = sum((v["value_usd"] or 0) + (v["fees_usd"] or 0) for v in values.values() if v)
        return sol + usdc + pos  # dry-run positions never left the wallet, so they are already in sol / usdc

    # ---- events --------------------------------------------------------------------

    def handle(self, events, leader):
        """React to new leader events. Returns [(title, lines)] to send as alerts."""
        with self.lock:
            return self._handle(events, leader)

    def _guard(self, notes, label, fn):
        """Run one bot action: Skip -> note, error -> note + failure count (pauses after MAX_FAILURES)."""
        try:
            out = fn()
        except Skip as e:
            notes.append(self._note("skip", f"⏭️ COPY DILEWATI: {label}", [str(e)]))
            return
        except Exception as e:
            self.state["failures"] += 1
            notes.append(self._note("error", f"❌ COPY GAGAL: {label}", [f"{type(e).__name__}: {e}"]))
            if self.live and self.state["failures"] >= MAX_FAILURES and not self.state["paused"]:
                self.state.update(paused=True, pause_reason=f"{MAX_FAILURES} kegagalan berturut-turut")
                notes.append(self._note("error", "⏸️ COPYTRADE DIJEDA",
                                        [f"{MAX_FAILURES} kegagalan berturut-turut. Cek wallet bot, lalu klik "
                                         "Lanjutkan di web, kirim /lanjut di Telegram, atau jalankan copy-resume."]))
            return
        for note in ([out] if isinstance(out, tuple) else out or []):
            if note:
                notes.append(note)
                if self.live:
                    self.state["failures"] = 0

    def _handle(self, events, leader):
        if self.s["run"] == "off":
            return []
        notes = []
        evs = [e for e in sorted(events, key=lambda e: e.get("ts") or 0)
               if e.get("wallet") == leader.label and not e.get("historical")]
        closes = {e.get("position") for e in evs if e["type"] == "CLOSE"}
        latest_add = {e["position"]: e for e in evs if e["type"] == "ADD"}
        opened_now = set()
        for ev in evs:
            kind = ev["type"]
            if kind == "OPEN":
                if ev["position"] in closes:
                    continue  # opened and closed between two checks: nothing left to copy
                opened_now.add(ev["position"])
                self._guard(notes, self._pair_name(ev, leader), lambda ev=ev: self.copy_open(ev, leader))
            elif kind == "CLOSE":
                self._guard(notes, self._pair_name(ev, leader), lambda ev=ev: self.copy_close(ev))
            elif kind in ("REBALANCE", "ORDER_PLACE"):
                what = "Rebalance" if kind == "REBALANCE" else "Limit order"
                notes.append(self._note("info", f"ℹ️ COPY: {what} leader tidak di-copy otomatis",
                                        ["Cek alert-nya dan ikuti manual kalau perlu."]))
        # positions the leader opened empty and filled later
        for lp, pend in list(self.state["pending"].items()):
            if lp in opened_now:
                continue
            self.state["pending"].pop(lp)  # copy_open puts it back while the position is still empty
            if lp in closes or time.time() - pend["since"] > PENDING_TTL:
                continue
            ev = dict(pend["ev"])
            if lp in latest_add and latest_add[lp].get("active_bin_id") is not None:
                ev["active_bin_id"] = latest_add[lp]["active_bin_id"]
            opened_now.add(lp)
            self._guard(notes, self._pair_name(ev, leader),
                        lambda ev=ev, since=pend["since"]: self.copy_open(ev, leader, pending_since=since))
        # liquidity the leader added / withdrew later: same share of yours
        if self.s["follow_liquidity"]:
            agg = {}
            for ev in evs:
                lp = ev.get("position")
                if ev["type"] not in ("ADD", "REMOVE") or lp in closes or lp in opened_now:
                    continue
                usd = self._event_usd(ev, leader)
                if usd is None:
                    continue
                a = agg.setdefault(lp, {"add": 0.0, "remove": 0.0, "ev": ev})
                a["add" if ev["type"] == "ADD" else "remove"] += usd
            for lp, a in agg.items():
                self._guard(notes, self._pair_name(a["ev"], leader),
                            lambda lp=lp, a=a: self.follow_liquidity(lp, a["add"], a["remove"], leader))
        self.save()
        return notes

    @staticmethod
    def _pair_name(ev, leader):
        try:
            _, tx, ty = leader.pair_info(ev.get("lb_pair"))
            return f"{tx.symbol}-{ty.symbol}"
        except Exception:
            return "?"

    def _event_usd(self, ev, leader):
        try:
            _, tx, ty = leader.pair_info(ev["lb_pair"])
        except Exception:
            return None
        px, py = self.prices.prices.get(tx.mint), self.prices.prices.get(ty.mint)
        if px is None or py is None:
            return None
        return tx.ui(ev.get("amount_x") or 0) * px + ty.ui(ev.get("amount_y") or 0) * py

    @staticmethod
    def _leader_row(leader, leader_position):
        return next((r for r in (leader.snapshot or {}).get("positions", []) if r["address"] == leader_position), None)

    def _mine(self, leader_position):
        return [k for k, p in self.state["positions"].items() if p["leader_position"] == leader_position]

    def _size(self, row, leader, bal):
        """USD size of a new copy + a line explaining it."""
        s = self.s
        if s["size_mode"] == "ratio":
            size, why = s["size_ratio"] * (row["value_usd"] or 0), f"{s['size_ratio']} × posisi vault"
        elif s["size_mode"] == "vault":
            nav = (leader.snapshot or {}).get("total_usd") or 0
            if nav <= 0:
                raise Skip("nilai total vault belum diketahui")
            weight = (row["value_usd"] or 0) / nav
            equity = self.equity_usd(bal)
            size = equity * weight * s["follow_pct"] / 100
            why = (f"bobot di vault {weight * 100:.1f}% × modal bot {fmt_usd(equity)}"
                   + (f" × {s['follow_pct']:g}%" if s["follow_pct"] != 100 else ""))
        else:
            size, why = s["size_usd"], "ukuran tetap"
        if size > s["max_usd"]:
            size, why = s["max_usd"], why + f" → dibatasi {fmt_usd(s['max_usd'])}"
        return size, why

    def copy_open(self, ev, leader, pending_since=None):
        """Copy a leader OPEN. pending_since: the leader opened it empty at that time and has now
        (maybe) deposited; such opens skip the age check."""
        s = self.s
        lp = ev["position"]
        if self.state["paused"]:
            raise Skip(f"copytrade sedang dijeda ({self.state['pause_reason'] or 'dijeda'})")
        if lp in self.state["done"] or self._mine(lp):
            return None
        age = time.time() - (ev.get("ts") or 0)
        if pending_since is None and age > s["max_event_age"]:
            raise Skip(f"posisi leader dibuka {int(age)} dtk lalu (batas {s['max_event_age']} dtk)")
        if len(self.state["positions"]) >= s["max_open"]:
            raise Skip(f"sudah {len(self.state['positions'])} posisi bot terbuka (maks {s['max_open']})")
        row = self._leader_row(leader, lp)
        if row is None or row.get("kind") == "order":
            raise Skip("posisi leader sudah tidak ada saat dicek")
        if not row.get("has_liquidity") or (row.get("value_usd") or 0) < 1:
            # the vault often creates the position first and deposits in a later transaction
            self.state["pending"][lp] = {"ev": ev, "since": pending_since or time.time()}
            return None
        self.state["pending"].pop(lp, None)
        pool = dlmm_tx.PoolContext(self.rpc, row["lb_pair"])
        if pool.hooks:
            raise Skip(f"{row['pair']}: token dengan transfer hook belum didukung")
        sug = copytrade.suggest_range({**row, "active_bin": pool.active_id,
                                       "price": dlmm.bin_price(pool.active_id, pool.bin_step, *pool.decimals)},
                                      self.copy_mode)
        lower, upper = sug["lower_bin"], sug["upper_bin"]
        if upper - lower + 1 > dlmm_tx.MAX_BINS_PER_TX:
            raise Skip(f"range kamu {upper - lower + 1} bins, bot maksimal {dlmm_tx.MAX_BINS_PER_TX} bins")
        leader_active = ev.get("active_bin_id", row["active_bin"])
        move = abs((1 + pool.bin_step / 10_000) ** (pool.active_id - leader_active) - 1) * 100
        if move > s["max_price_move"]:
            raise Skip(f"harga sudah bergerak {move:.1f}% sejak leader buka (batas {s['max_price_move']}%)")
        missing = dlmm_tx.missing_bin_arrays(self.rpc, pool.address, lower, upper)
        if len(missing) > s["max_new_bin_arrays"]:
            raise Skip(f"perlu membuat {len(missing)} bin array baru (~0.07 SOL per bin array, batas "
                       f"{s['max_new_bin_arrays']})")

        bal = self.balances()
        size, why = self._size(row, leader, bal)
        if size < 1:
            raise Skip(f"ukuran posisi terlalu kecil ({fmt_usd(size)}; {why})")
        lines = [f"Pool {row['pair']} · step {pool.bin_step} · ukuran {fmt_usd(size)} ({why})",
                 f"Range kamu: {fmt_num(sug['price_lower'])} – {fmt_num(sug['price_upper'])} "
                 f"({sug['pct_lower']:+.1f}% / {sug['pct_upper']:+.1f}%) · {upper - lower + 1} bins · {s['strategy']}"]
        rent = self._rent_for(8120) + len(missing) * self._rent_for(10136) + FEE_BUFFER_LAMPORTS
        key, ax, ay, sig, cost = self._deposit(pool, lower, upper, size, lines, rent, bal)
        rec = {"leader_position": lp, "lb_pair": pool.address, "pair": row["pair"], "lower": lower,
               "upper": upper, "opened_at": int(time.time()), "usd": size, "cost_usd": cost,
               "ratio": cost / row["value_usd"]}
        if not self.live:
            rec["virtual"] = True
            self.state["positions"][f"dry-{lp}"] = rec
            lines.append("Mode dry run: tidak ada transaksi yang dikirim. Bot mencatat posisi simulasi ini "
                         "untuk mengikuti tambah/tarik/tutup vault berikutnya.")
            return self._note("dry", f"{self.tag} BUKA: {row['pair']}", lines)
        rec.update(opened_sig=sig, deposit_x=ax, deposit_y=ay)
        self.state["positions"][key] = rec
        return self._note("ok", f"{self.tag} BUKA: {row['pair']}", lines)

    def _spendable(self, bal, mint, keep_lamports):
        if mint == SOL_MINT:  # native SOL is wrapped inside the deposit transaction
            return max(0, bal["lamports"] - keep_lamports) + bal.get(SOL_MINT, 0)
        return bal.get(mint, 0)

    def _deposit(self, pool, lower, upper, size, lines, keep_extra_lamports, bal, existing=None):
        """Swap the budget token into what [lower, upper] needs and deposit `size` USD, into a new
        position (existing=None) or into `existing` (a decoded position). Returns
        (position key, amount_x, amount_y, signature, deposited USD)."""
        s = self.s
        mx, my = pool.token_x_mint, pool.token_y_mint
        tx, ty = self.tokens.get(mx), self.tokens.get(my)
        px, py, pb = self._price(mx), self._price(my), self._price(self.budget_mint)
        xs = x_share(lower, upper, pool.active_id)
        need = {mx: size * xs, my: size * (1 - xs)}
        keep = int(s["reserve_sol"] * 1e9) + keep_extra_lamports
        if self.budget_mint == SOL_MINT:
            spendable_usd = max(0, bal["lamports"] + bal.get(SOL_MINT, 0) - keep) / 1e9 * pb
        else:
            spendable_usd = bal.get(USDC_MINT, 0) / 1e6
            if bal["lamports"] < keep:
                raise Skip(f"SOL untuk fee/rent kurang: perlu {keep / 1e9:.3f} SOL")
        if not self.live:  # dry run: what the simulated positions would have used is not spendable
            spendable_usd -= sum(_cost(p) for p in self.state["positions"].values() if p.get("virtual"))
        if spendable_usd < size * 0.98:
            raise Skip(f"saldo {s['budget']} kurang: ada {fmt_usd(spendable_usd)}, perlu {fmt_usd(size)}")
        budget = self.tokens.get(self.budget_mint)
        swaps = [(m, usd) for m, usd in need.items() if usd >= 0.5 and m != self.budget_mint]
        for m, usd in swaps:
            lines.append(f"Swap: ≈{fmt_num(usd / pb)} {budget.symbol} → {self.tokens.get(m).symbol} ({fmt_usd(usd)})")
        want_x, want_y = int(need[mx] / px * 10 ** tx.decimals), int(need[my] / py * 10 ** ty.decimals)

        def build(ax, ay, position):
            if existing is None:
                return dlmm_tx.open_position_ixs(self.rpc, pool, self.wallet, position, lower, upper, ax, ay,
                                                 s["strategy"])
            return dlmm_tx.add_liquidity_ixs(pool, self.wallet, existing, ax, ay, s["strategy"])

        if not self.live:
            ax, ay = min(want_x, self._spendable(bal, mx, keep)), min(want_y, self._spendable(bal, my, keep))
            if swaps and (ax < want_x * 0.99 or ay < want_y * 0.99):
                lines.append("Simulasi deposit dilewati: wallet belum punya token hasil swap.")
            else:
                ok, units, logs, err = dlmm_tx.simulate(self.rpc, build(ax, ay, Keypair().pubkey()), self.wallet)
                lines.append(f"Simulasi deposit: {'berhasil' if ok else 'GAGAL'} ({units} CU)"
                             + ("" if ok else f" · {err} · {' / '.join(logs[-2:])[:200]}"))
            return None, ax, ay, None, size

        for m, usd in swaps:
            q = jupiter.quote(self.budget_mint, m, int(usd / pb * 10 ** budget.decimals), s["slippage_bps"])
            lines.append(f"Swap terkirim: https://solscan.io/tx/{jupiter.swap(self.rpc, self.kp, q)}")
        bal = self.balances()
        ax, ay = min(want_x, self._spendable(bal, mx, keep)), min(want_y, self._spendable(bal, my, keep))
        if ax == 0 and ay == 0:
            raise RuntimeError("tidak ada token untuk didepositkan setelah swap")
        if existing is None:
            position = Keypair()
            sig = dlmm_tx.send(self.rpc, build(ax, ay, position.pubkey()), self.kp, [position], s["priority"])
            key = str(position.pubkey())
        else:
            sig = dlmm_tx.send(self.rpc, build(ax, ay, None), self.kp, [], s["priority"])
            key = existing.address
        lines.append(f"Deposit: {fmt_num(tx.ui(ax))} {tx.symbol} + {fmt_num(ty.ui(ay))} {ty.symbol}")
        lines.append(f"Tx: https://solscan.io/tx/{sig}")
        return key, ax, ay, sig, tx.ui(ax) * px + ty.ui(ay) * py

    def _swap_back(self, pool, before, lines):
        """Swap what a withdrawal / close returned back into the budget token (SOL stays for fees)."""
        if not self.s["exit_swap"]:
            return
        after = self.balances()
        for mint in (pool.token_x_mint, pool.token_y_mint):
            got = after.get(mint, 0) - before.get(mint, 0)
            if mint in (self.budget_mint, SOL_MINT) or got <= 0:
                continue
            try:
                q = jupiter.quote(mint, self.budget_mint, got, self.s["slippage_bps"])
                lines.append(f"Swap balik ke {self.s['budget']}: https://solscan.io/tx/{jupiter.swap(self.rpc, self.kp, q)}")
            except Exception as e:
                lines.append(f"Swap balik {self.tokens.get(mint).symbol} gagal: {e}. Token tetap di wallet bot.")

    def _onchain(self, key):
        acc = self.rpc.call("getAccountInfo", [key, {"encoding": "base64"}])["value"]
        if not acc:
            return None, None
        st = dlmm.decode_position(key, dlmm.account_bytes(acc))
        return st, dlmm_tx.PoolContext(self.rpc, st.lb_pair)

    # ---- follow the leader's liquidity -------------------------------------------------

    def follow_liquidity(self, leader_position, add_usd, remove_usd, leader):
        mine = self._mine(leader_position)
        if not mine:
            return []
        row = self._leader_row(leader, leader_position)
        action, amount, before = follow_plan(add_usd, remove_usd, (row or {}).get("value_usd") or 0)
        notes = []
        for key in mine:
            if action == "remove":
                notes.append(self._withdraw(key, amount, f"vault menarik {amount * 100:.0f}% likuiditasnya "
                                                         f"({fmt_usd(remove_usd)})", follow=True))
            elif action == "add":
                notes.append(self._follow_add(key, amount, before))
        return notes

    def _follow_add(self, key, add_usd, leader_before):
        rec = self.state["positions"][key]
        if self.state["paused"]:
            raise Skip(f"vault menambah likuiditas, tapi copytrade sedang dijeda ({self.state['pause_reason']})")
        ratio = rec.get("ratio")
        if not ratio and not rec.get("virtual") and leader_before > 1:
            v = self.position_values([key]).get(key)
            ratio = v and v["value_usd"] and v["value_usd"] / leader_before
        if not ratio:
            raise Skip("vault menambah likuiditas, tapi rasio copy posisi ini belum diketahui")
        usd = add_usd * ratio
        room = self.s["max_usd"] - _cost(rec)
        if usd > room:
            if room < self.s["min_action_usd"]:
                raise Skip(f"vault menambah {fmt_usd(add_usd)}, posisi bot sudah di batas {fmt_usd(self.s['max_usd'])}")
            usd = room
        if usd < self.s["min_action_usd"]:
            return None
        return self._add(key, usd, f"vault menambah {fmt_usd(add_usd)} ke posisinya")

    def _add(self, key, usd, reason):
        rec = self.state["positions"].get(key)
        if rec is None:
            raise Skip("posisi ini bukan posisi bot")
        lines = [f"Alasan: {reason}", f"Tambah {fmt_usd(usd)} ke {rec['pair']}"]
        if rec.get("virtual"):
            rec["cost_usd"] = rec.get("cost_usd", 0) + usd
            lines.append("Mode dry run: tidak ada transaksi yang dikirim.")
            return self._note("dry", f"{self.tag} TAMBAH: {rec['pair']}", lines)
        st, pool = self._onchain(key)
        if st is None:
            raise Skip("posisi bot sudah tidak ada di chain")
        _, ax, ay, sig, cost = self._deposit(pool, st.lower_bin_id, st.upper_bin_id, usd, lines,
                                             FEE_BUFFER_LAMPORTS, self.balances(), existing=st)
        if self.live:
            rec["cost_usd"] = _cost(rec) + cost
        return self._note("ok" if self.live else "dry", f"{self.tag} TAMBAH: {rec['pair']}", lines)

    def _withdraw(self, key, fraction, reason, follow=False):
        rec = self.state["positions"].get(key)
        if rec is None:
            raise Skip("posisi ini bukan posisi bot")
        bps = max(1, min(10_000, round(fraction * 10_000)))
        title = f"{self.tag} TARIK {bps / 100:g}%: {rec['pair']}"
        if rec.get("virtual"):
            usd = rec.get("cost_usd", 0) * bps / 10_000
            rec["cost_usd"] = rec.get("cost_usd", 0) - usd
            return self._note("dry", title, [f"Alasan: {reason}", f"Tarik ≈{fmt_usd(usd)} (perkiraan)",
                                             "Mode dry run: tidak ada transaksi yang dikirim."])
        st, pool = self._onchain(key)
        if st is None:
            self.state["positions"].pop(key, None)
            self._done(rec["leader_position"])
            return self._note("info", f"{self.tag} TARIK: {rec['pair']}", ["Posisi sudah tidak ada di chain."])
        if not any(b.share for b in st.bins):
            return None if follow else self._note("info", title, ["Posisi bot sudah kosong."])
        v = self.position_values([key]).get(key) or {}
        usd = (v.get("value_usd") or 0) * bps / 10_000
        if follow and bps < 10_000 and usd < self.s["min_action_usd"]:
            return None
        ixs = dlmm_tx.remove_liquidity_ixs(pool, self.wallet, st, bps, claim=bps == 10_000)
        lines = [f"Alasan: {reason}", f"Tarik {bps / 100:g}% dari posisi bot · ≈{fmt_usd(usd)}"
                 + (" · fee ikut di-claim" if bps == 10_000 else "")]
        if not self.live:
            ok, units, logs, err = dlmm_tx.simulate(self.rpc, ixs, self.wallet)
            lines.append(f"Simulasi tarik: {'berhasil' if ok else 'GAGAL'} ({units} CU)" + ("" if ok else f" · {err}"))
            return self._note("dry", title, lines)
        before = self.balances()
        sig = dlmm_tx.send(self.rpc, ixs, self.kp, [], self.s["priority"])
        rec["cost_usd"] = _cost(rec) * (1 - bps / 10_000)
        lines.append(f"Tx: https://solscan.io/tx/{sig}")
        self._swap_back(pool, before, lines)
        return self._note("ok", title, lines)

    # ---- closing --------------------------------------------------------------------

    def copy_close(self, ev):
        self.state["pending"].pop(ev.get("position"), None)
        self._done(ev.get("position"))
        mine = self._mine(ev["position"])
        if not mine:
            return None
        notes = []
        for key in mine:
            notes.append(self._close_position(key, reason="leader menutup posisinya"))
        return notes

    def close_position(self, key, reason="ditutup manual"):
        """Close one bot position (live) or simulate closing it (dry)."""
        with self.lock:
            try:
                return self._close_position(key, reason)
            finally:
                self.save()

    def _close_position(self, key, reason):
        rec = self.state["positions"].get(key)
        if rec is None:
            raise Skip("posisi ini bukan posisi bot")
        title = f"{self.tag} TUTUP: {rec['pair']}"
        if rec.get("virtual"):
            self.state["positions"].pop(key)
            self._done(rec["leader_position"])
            return self._note("dry", title, [f"Alasan: {reason}", "Mode dry run: tidak ada transaksi yang dikirim."])
        st, pool = self._onchain(key)
        if st is None:
            self.state["positions"].pop(key, None)
            self._done(rec["leader_position"])
            return self._note("info", title, ["Posisi sudah tidak ada di chain, dihapus dari daftar bot."])
        ixs = dlmm_tx.close_position_ixs(pool, self.wallet, st)
        lines = [f"Alasan: {reason}", f"Posisi {key[:4]}…{key[-4:]} · range {st.lower_bin_id}..{st.upper_bin_id}"]
        if rec.get("pnl_pct") is not None:
            lines.append(f"Terakhir dicek: {fmt_usd(rec.get('value_usd'))} · PnL {rec['pnl_pct']:+.1f}%")
        if not self.live:
            ok, units, logs, err = dlmm_tx.simulate(self.rpc, ixs, self.wallet)
            lines.append(f"Simulasi tutup: {'berhasil' if ok else 'GAGAL'} ({units} CU)" + ("" if ok else f" · {err}"))
            return self._note("dry", title, lines)
        before = self.balances()
        sig = dlmm_tx.send(self.rpc, ixs, self.kp, [], self.s["priority"])
        self.state["positions"].pop(key, None)
        self._done(rec["leader_position"])
        lines.append(f"Tx: https://solscan.io/tx/{sig}")
        self._swap_back(pool, before, lines)
        return self._note("ok", title, lines)

    # ---- your own exits -------------------------------------------------------------

    def check(self):
        """Value the bot positions; close on take profit / stop loss / out of range too long."""
        with self.lock:
            notes = []
            if self.s["run"] == "off" or not self.state["positions"]:
                return notes
            try:
                values = self.position_values()
            except Exception:
                return notes  # RPC hiccup: try again next cycle
            s, now = self.s, time.time()
            for key, v in values.items():
                rec = self.state["positions"].get(key)
                if rec is None:
                    continue
                if v is None:
                    self.state["positions"].pop(key)
                    self._done(rec["leader_position"])
                    notes.append(self._note("info", f"{self.tag} HILANG: {rec['pair']}",
                                            ["Posisi bot sudah tidak ada di chain (ditutup di luar bot?)."]))
                    continue
                cost = _cost(rec)
                rec.update(value_usd=v["value_usd"], fees_usd=v["fees_usd"], in_range=v["in_range"], checked_at=int(now))
                if v["value_usd"] is None or cost < 0.5 or not v["has_liquidity"]:
                    rec.pop("pnl_pct", None)
                    continue
                pnl = ((v["value_usd"] + (v["fees_usd"] or 0)) / cost - 1) * 100
                rec["pnl_pct"] = round(pnl, 2)
                hit = ("tp" if s["tp_pct"] > 0 and pnl >= s["tp_pct"]
                       else "sl" if s["sl_pct"] > 0 and pnl <= -s["sl_pct"] else None)
                rec["hits"] = rec.get("hits", 0) + 1 if hit and rec.get("hit") == hit else (1 if hit else 0)
                rec["hit"] = hit
                reason = None
                if hit and rec["hits"] >= HITS_TO_EXIT:
                    reason = (f"take profit: PnL {pnl:+.1f}% (target +{s['tp_pct']:g}%)" if hit == "tp"
                              else f"stop loss: PnL {pnl:+.1f}% (batas -{s['sl_pct']:g}%)")
                if not reason and s["oor_close_min"] > 0:
                    if v["in_range"]:
                        rec.pop("oor_since", None)
                    else:
                        rec.setdefault("oor_since", int(now))
                        if now - rec["oor_since"] >= s["oor_close_min"] * 60:
                            reason = f"out of range lebih dari {s['oor_close_min']} menit"
                if reason:
                    self._guard(notes, rec["pair"], lambda key=key, reason=reason: self._close_position(key, reason))
            self.save()
            return notes

    # ---- manual controls (web buttons, CLI, Telegram) --------------------------------

    def withdraw(self, key, pct):
        """Withdraw pct % of a bot position now (it stays open)."""
        if not 0 < pct <= 100:
            raise ValueError("persen harus 1–100")
        with self.lock:
            try:
                return self._withdraw(key, pct / 100, f"tarik manual {pct:g}%")
            finally:
                self.save()

    def add(self, key, usd):
        """Deposit usd more into a bot position now (ignores the pause, keeps max_usd)."""
        with self.lock:
            rec = self.state["positions"].get(key)
            if rec is None:
                raise Skip("posisi ini bukan posisi bot")
            room = self.s["max_usd"] - _cost(rec)
            if usd > room:
                raise Skip(f"melewati batas per posisi {fmt_usd(self.s['max_usd'])}: maksimal tambah {fmt_usd(max(0, room))}")
            try:
                return self._add(key, usd, f"tambah manual {fmt_usd(usd)}")
            finally:
                self.save()

    def emergency_exit(self, reason="tarik darurat"):
        """Close every bot position now and pause the bot so it opens nothing new."""
        with self.lock:
            self.state.update(paused=True, pause_reason=reason)
            self.state["pending"].clear()
            keys = list(self.state["positions"])
            notes = [self._note("error", f"🚨 DARURAT: tutup semua posisi bot ({len(keys)})",
                                ["Bot dijeda: tidak membuka posisi baru sampai kamu klik Lanjutkan / kirim /lanjut."])]
            for key in keys:
                pair = self.state["positions"][key]["pair"]
                try:
                    notes.append(self._close_position(key, reason))
                except Exception as e:
                    notes.append(self._note("error", f"❌ GAGAL TUTUP: {pair}",
                                            [f"{type(e).__name__}: {e}", "Tutup manual di Meteora kalau perlu."]))
            self.save()
            return notes

    def positions_list(self):
        return sorted(({"position": k, **v} for k, v in self.state["positions"].items()),
                      key=lambda p: p.get("opened_at") or 0)

    def status(self):
        out = {"run": self.s["run"], "wallet": str(self.wallet) if self.wallet else None,
               "paused": self.state["paused"], "pause_reason": self.state.get("pause_reason", ""),
               "failures": self.state["failures"], "pending": len(self.state["pending"]),
               "positions": self.positions_list(),
               "log": self.state["log"][-30:][::-1], "settings": {k: v for k, v in self.s.items()}}
        try:
            bal = self.balances() if self.wallet else {}
            out["balances"] = {"SOL": bal.get("lamports", 0) / 1e9 + bal.get(SOL_MINT, 0) / 1e9,
                               "USDC": bal.get(USDC_MINT, 0) / 1e6}
        except Exception:
            out["balances"] = None
        return out
