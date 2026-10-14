"""Copytrade bot: mirrors the leader's DLMM position opens and closes into your own wallet.

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
DEFAULTS = {
    "run": "off",               # off | dry | live
    "budget": "SOL",            # token the bot spends and swaps back to
    "size_usd": 20.0,           # per copied position
    "size_ratio": 0.0,          # >0: leader position value x ratio instead of size_usd
    "max_usd": 100.0,           # hard cap per position
    "max_open": 3,              # bot positions open at once
    "reserve_sol": 0.1,         # never spend below this (fees, rent)
    "max_price_move": 2.0,      # % the price may have moved since the leader opened
    "max_event_age": 180,       # s; older leader events are not copied (e.g. after downtime)
    "strategy": "spot",         # spot | curve | bidask
    "slippage_bps": 100,        # Jupiter swap slippage
    "exit_swap": True,          # swap tokens back to the budget token after closing
    "priority": 50_000,         # micro-lamports per compute unit
    "max_new_bin_arrays": 1,    # bin arrays the bot may create (~0.07 SOL rent each)
}
MAX_FAILURES = 3
FEE_BUFFER_LAMPORTS = 10_000_000  # 0.01 SOL for tx fees / ATAs


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
    return {
        "run": (cfg.get("COPY_RUN") or "off").lower(),
        "budget": (cfg.get("COPY_BUDGET") or "SOL").upper(),
        "size_usd": num("COPY_SIZE_USD", 20.0), "size_ratio": num("COPY_SIZE_RATIO", 0.0),
        "max_usd": num("COPY_MAX_USD", 100.0), "max_open": num("COPY_MAX_OPEN", 3, int),
        "reserve_sol": num("COPY_RESERVE_SOL", 0.1), "max_price_move": num("COPY_MAX_PRICE_MOVE", 2.0),
        "max_event_age": num("COPY_MAX_EVENT_AGE", 180, int),
        "strategy": (cfg.get("COPY_STRATEGY") or "spot").lower(),
        "slippage_bps": num("COPY_SLIPPAGE_BPS", 100, int),
        "exit_swap": (cfg.get("COPY_EXIT_SWAP") or "1") != "0",
        "priority": num("COPY_PRIORITY", 50_000, int),
        "max_new_bin_arrays": num("COPY_MAX_NEW_BIN_ARRAYS", 1, int),
    }


def x_share(lower, upper, active):
    """Value share of token X for a spot-shaped range: bins above the active bin hold X."""
    if upper < active:
        return 0.0
    if lower > active:
        return 1.0
    return (upper - active + 0.5) / (upper - lower + 1)


class Skip(Exception):
    pass


class Copier:
    def __init__(self, rpc, tokens, prices, settings, copy_mode, state_dir=".vault_tracker", keypair=None,
                 sim_wallet=None):
        self.rpc, self.tokens, self.prices = rpc, tokens, prices
        self.s = {**DEFAULTS, **settings}
        if self.s["run"] not in ("off", "dry", "live"):
            raise ValueError("COPY_RUN harus off, dry, atau live")
        if self.s["budget"] not in BUDGET_MINTS:
            raise ValueError("COPY_BUDGET harus SOL atau USDC")
        if self.s["strategy"] not in dlmm_tx.STRATEGY:
            raise ValueError("COPY_STRATEGY harus spot, curve, atau bidask")
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
            self.state = {"positions": {}, "log": [], "failures": 0, "paused": False}
        self._rent = {}
        self.lock = threading.RLock()  # the web UI's close button runs beside the polling loop

    @property
    def live(self):
        return self.s["run"] == "live"

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

    def resume(self):
        with self.lock:
            self.state.update(paused=False, failures=0)
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

    # ---- events --------------------------------------------------------------------

    def handle(self, events, leader):
        """React to new leader events. Returns [(title, lines)] to send as alerts."""
        with self.lock:
            return self._handle(events, leader)

    def _handle(self, events, leader):
        if self.s["run"] == "off":
            return []
        notes = []
        for ev in sorted(events, key=lambda e: e.get("ts") or 0):
            if ev.get("wallet") != leader.label or ev.get("historical"):
                continue
            kind = ev["type"]
            if kind not in ("OPEN", "CLOSE", "REBALANCE", "ORDER_PLACE"):
                continue
            try:
                if kind == "OPEN":
                    note = self.copy_open(ev, leader)
                elif kind == "CLOSE":
                    note = self.copy_close(ev)
                else:
                    what = "Rebalance" if kind == "REBALANCE" else "Limit order"
                    note = self._note("info", f"ℹ️ COPY: {what} leader tidak di-copy otomatis",
                                      ["Cek alert-nya dan ikuti manual kalau perlu."])
                if note:
                    notes.append(note)
                    if self.live and kind in ("OPEN", "CLOSE") and not note[0].startswith("⏭️"):
                        self.state["failures"] = 0
            except Skip as e:
                notes.append(self._note("skip", f"⏭️ COPY DILEWATI: {self._pair_name(ev, leader)}", [str(e)]))
            except Exception as e:
                self.state["failures"] += 1
                notes.append(self._note("error", f"❌ COPY GAGAL: {self._pair_name(ev, leader)}",
                                        [f"{type(e).__name__}: {e}"]))
                if self.live and self.state["failures"] >= MAX_FAILURES:
                    self.state["paused"] = True
                    notes.append(self._note("error", "⏸️ COPYTRADE DIJEDA",
                                            [f"{MAX_FAILURES} kegagalan berturut-turut. Cek wallet bot, lalu klik "
                                             "Lanjutkan di web atau jalankan copy-resume."]))
        self.save()
        return notes

    @staticmethod
    def _pair_name(ev, leader):
        try:
            _, tx, ty = leader.pair_info(ev.get("lb_pair"))
            return f"{tx.symbol}-{ty.symbol}"
        except Exception:
            return "?"

    def copy_open(self, ev, leader):
        s = self.s
        tag = "🧪 DRY RUN" if not self.live else "🤖 COPY"
        if self.state["paused"]:
            raise Skip("copytrade sedang dijeda setelah beberapa kegagalan")
        age = time.time() - (ev.get("ts") or 0)
        if age > s["max_event_age"]:
            raise Skip(f"posisi leader dibuka {int(age)} dtk lalu (batas {s['max_event_age']} dtk)")
        if any(p["leader_position"] == ev["position"] for p in self.state["positions"].values()):
            return None
        if len(self.state["positions"]) >= s["max_open"]:
            raise Skip(f"sudah {len(self.state['positions'])} posisi bot terbuka (maks {s['max_open']})")
        row = next((r for r in (leader.snapshot or {}).get("positions", []) if r["address"] == ev["position"]), None)
        if row is None or row.get("kind") == "order":
            raise Skip("posisi leader sudah tidak ada saat dicek")
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

        size = s["size_ratio"] * (row["value_usd"] or 0) if s["size_ratio"] > 0 else s["size_usd"]
        size = min(size, s["max_usd"])
        if size < 1:
            raise Skip(f"ukuran posisi terlalu kecil ({fmt_usd(size)})")
        mx, my = pool.token_x_mint, pool.token_y_mint
        tx, ty = self.tokens.get(mx), self.tokens.get(my)
        px, py, pb = self._price(mx), self._price(my), self._price(self.budget_mint)
        xs = x_share(lower, upper, pool.active_id)
        need = {mx: size * xs, my: size * (1 - xs)}

        bal = self.balances()
        rent = self._rent_for(8120) + len(missing) * self._rent_for(10136) + FEE_BUFFER_LAMPORTS
        reserve = int(s["reserve_sol"] * 1e9)
        if self.budget_mint == SOL_MINT:
            spendable_usd = max(0, bal["lamports"] - reserve - rent) / 1e9 * pb
        else:
            spendable_usd = bal.get(USDC_MINT, 0) / 1e6
            if bal["lamports"] < reserve + rent:
                raise Skip(f"SOL untuk fee/rent kurang: perlu {(reserve + rent) / 1e9:.3f} SOL")
        if spendable_usd < size * 0.98:
            raise Skip(f"saldo {self.s['budget']} kurang: ada {fmt_usd(spendable_usd)}, perlu {fmt_usd(size)}")

        budget_sym = self.tokens.get(self.budget_mint).symbol
        swaps = [(m, usd) for m, usd in need.items() if usd >= 0.5 and m != self.budget_mint]
        lines = [f"Pool {row['pair']} · step {pool.bin_step} · ukuran {fmt_usd(size)}",
                 f"Range kamu: {fmt_num(sug['price_lower'])} – {fmt_num(sug['price_upper'])} "
                 f"({sug['pct_lower']:+.1f}% / {sug['pct_upper']:+.1f}%) · {upper - lower + 1} bins · {s['strategy']}"]
        for m, usd in swaps:
            lines.append(f"Swap: ≈{fmt_num(usd / pb)} {budget_sym} → {self.tokens.get(m).symbol} ({fmt_usd(usd)})")

        if not self.live:
            amount_x = min(int(need[mx] / px * 10 ** tx.decimals), self._spendable(bal, mx, reserve + rent))
            amount_y = min(int(need[my] / py * 10 ** ty.decimals), self._spendable(bal, my, reserve + rent))
            if swaps and (amount_x < need[mx] / px * 10 ** tx.decimals * 0.99 or
                          amount_y < need[my] / py * 10 ** ty.decimals * 0.99):
                lines.append("Simulasi deposit dilewati: wallet belum punya token hasil swap.")
            else:
                ixs = dlmm_tx.open_position_ixs(self.rpc, pool, self.wallet, Keypair().pubkey(), lower, upper,
                                                amount_x, amount_y, s["strategy"])
                ok, units, logs, err = dlmm_tx.simulate(self.rpc, ixs, self.wallet)
                lines.append(f"Simulasi deposit: {'berhasil' if ok else 'GAGAL'} ({units} CU)"
                             + ("" if ok else f" · {err} · {' / '.join(logs[-2:])[:200]}"))
            lines.append("Mode dry run: tidak ada transaksi yang dikirim.")
            return self._note("dry", f"{tag} BUKA: {row['pair']}", lines)

        # live: swap the budget token into what the range needs, then open + deposit
        for m, usd in swaps:
            amount_in = int(usd / pb * 10 ** self.tokens.get(self.budget_mint).decimals)
            q = jupiter.quote(self.budget_mint, m, amount_in, s["slippage_bps"])
            sig = jupiter.swap(self.rpc, self.kp, q)
            lines.append(f"Swap terkirim: https://solscan.io/tx/{sig}")
        bal = self.balances()
        amount_x = min(int(need[mx] / px * 10 ** tx.decimals), self._spendable(bal, mx, reserve + rent))
        amount_y = min(int(need[my] / py * 10 ** ty.decimals), self._spendable(bal, my, reserve + rent))
        if amount_x == 0 and amount_y == 0:
            raise RuntimeError("tidak ada token untuk didepositkan setelah swap")
        position = Keypair()
        ixs = dlmm_tx.open_position_ixs(self.rpc, pool, self.wallet, position.pubkey(), lower, upper,
                                        amount_x, amount_y, s["strategy"])
        sig = dlmm_tx.send(self.rpc, ixs, self.kp, [position], s["priority"])
        self.state["positions"][str(position.pubkey())] = {
            "leader_position": ev["position"], "lb_pair": pool.address, "pair": row["pair"],
            "lower": lower, "upper": upper, "opened_at": int(time.time()), "opened_sig": sig,
            "deposit_x": amount_x, "deposit_y": amount_y, "usd": size,
        }
        lines.append(f"Deposit: {fmt_num(tx.ui(amount_x))} {tx.symbol} + {fmt_num(ty.ui(amount_y))} {ty.symbol}")
        lines.append(f"Tx: https://solscan.io/tx/{sig}")
        return self._note("ok", f"{tag} BUKA: {row['pair']}", lines)

    def _spendable(self, bal, mint, keep_lamports):
        if mint == SOL_MINT:  # native SOL is wrapped inside the open transaction
            return max(0, bal["lamports"] - keep_lamports) + bal.get(SOL_MINT, 0)
        return bal.get(mint, 0)

    def copy_close(self, ev):
        mine = [k for k, p in self.state["positions"].items() if p["leader_position"] == ev["position"]]
        if not mine:
            return None
        notes_lines, title = [], None
        for key in mine:
            title, lines = self._close_position(key, reason="leader menutup posisinya")
            notes_lines += lines
        return title, notes_lines

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
        tag = "🧪 DRY RUN" if not self.live else "🤖 COPY"
        acc = self.rpc.call("getAccountInfo", [key, {"encoding": "base64"}])["value"]
        if not acc:
            self.state["positions"].pop(key, None)
            return self._note("info", f"{tag} TUTUP: {rec['pair']}", ["Posisi sudah tidak ada di chain, dihapus dari daftar bot."])
        st = dlmm.decode_position(key, dlmm.account_bytes(acc))
        pool = dlmm_tx.PoolContext(self.rpc, st.lb_pair)
        ixs = dlmm_tx.close_position_ixs(pool, self.wallet, st)
        lines = [f"Alasan: {reason}", f"Posisi {key[:4]}…{key[-4:]} · range {st.lower_bin_id}..{st.upper_bin_id}"]
        if not self.live:
            ok, units, logs, err = dlmm_tx.simulate(self.rpc, ixs, self.wallet)
            lines.append(f"Simulasi tutup: {'berhasil' if ok else 'GAGAL'} ({units} CU)" + ("" if ok else f" · {err}"))
            return self._note("dry", f"{tag} TUTUP: {rec['pair']}", lines)
        before = self.balances()
        sig = dlmm_tx.send(self.rpc, ixs, self.kp, [], self.s["priority"])
        self.state["positions"].pop(key, None)
        lines.append(f"Tx: https://solscan.io/tx/{sig}")
        if self.s["exit_swap"]:
            after = self.balances()
            for mint in (pool.token_x_mint, pool.token_y_mint):
                got = after.get(mint, 0) - before.get(mint, 0)
                if mint in (self.budget_mint, SOL_MINT) or got <= 0:  # SOL stays: it pays fees and rent
                    continue
                try:
                    q = jupiter.quote(mint, self.budget_mint, got, self.s["slippage_bps"])
                    s2 = jupiter.swap(self.rpc, self.kp, q)
                    lines.append(f"Swap balik ke {self.s['budget']}: https://solscan.io/tx/{s2}")
                except Exception as e:
                    lines.append(f"Swap balik {self.tokens.get(mint).symbol} gagal: {e}. Token tetap di wallet bot.")
        return self._note("ok", f"{tag} TUTUP: {rec['pair']}", lines)

    def status(self):
        out = {"run": self.s["run"], "wallet": str(self.wallet) if self.wallet else None,
               "paused": self.state["paused"], "failures": self.state["failures"],
               "positions": [{"position": k, **v} for k, v in self.state["positions"].items()],
               "log": self.state["log"][-30:][::-1], "settings": {k: v for k, v in self.s.items()}}
        try:
            bal = self.balances() if self.wallet else {}
            out["balances"] = {"SOL": bal.get("lamports", 0) / 1e9 + bal.get(SOL_MINT, 0) / 1e9,
                               "USDC": bal.get(USDC_MINT, 0) / 1e6}
        except Exception:
            out["balances"] = None
        return out
