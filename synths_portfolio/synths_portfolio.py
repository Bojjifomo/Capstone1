#!/usr/bin/env python3
"""Synths Portfolio: profit calculator + total koin + saldo wallet untuk synth bot (synths.lol).

Halaman synth cuma nampilin saldo ETH wallet dan UPnL per bag, tapi nggak ngitung
total portfolio (ETH + nilai semua koin) dan profit terhadap modal yang di-fund.
Script ini ngitung itu.

Mode:
  scan  baca langsung dari chain lewat Alchemy: saldo ETH, semua koin, riwayat
        beli/jual -> modal, realized/unrealized PnL, total portfolio, profit.
  calc  kalkulator manual dari angka yang tampil di halaman synth
        (saldo wallet + tab Bags), plus skenario take profit / stop loss.

Cuma pakai standard library Python 3.8+.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field

WETH = {
    "base-mainnet": "0x4200000000000000000000000000000000000006",
    "eth-mainnet": "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
    "arb-mainnet": "0x82af49447d8a07e3bd95bd0d56f35241523fbab1",
    "opt-mainnet": "0x4200000000000000000000000000000000000006",
}
DUST = 1e-12


class AlchemyError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Alchemy client
# ---------------------------------------------------------------------------

class Alchemy:
    def __init__(self, api_key, network, timeout=30):
        self.api_key = api_key
        self.network = network
        self.timeout = timeout

    def _request(self, url, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, headers={
            "content-type": "application/json",
            "accept": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            raise AlchemyError(f"HTTP {e.code} dari Alchemy: {e.read()[:300]!r}") from e
        except OSError as e:  # URLError, timeout, koneksi putus
            raise AlchemyError(f"Gagal konek ke Alchemy: {getattr(e, 'reason', e)}") from e

    def rpc(self, method, params):
        url = f"https://{self.network}.g.alchemy.com/v2/{self.api_key}"
        res = self._request(url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        if "error" in res:
            raise AlchemyError(f"{method}: {res['error'].get('message')}")
        return res["result"]

    def eth_balance(self, address):
        return int(self.rpc("eth_getBalance", [address, "latest"]), 16) / 1e18

    def eth_usd(self):
        url = f"https://api.g.alchemy.com/prices/v1/{self.api_key}/tokens/by-symbol?symbols=ETH"
        for item in self._request(url).get("data", []):
            for p in item.get("prices", []):
                if p.get("currency") == "usd":
                    return float(p["value"])
        return None

    def tokens(self, address):
        """Semua ERC-20 yang dipegang wallet, lengkap dengan metadata dan harga USD."""
        url = f"https://api.g.alchemy.com/data/v1/{self.api_key}/assets/tokens/by-address"
        payload = {
            "addresses": [{"address": address, "networks": [self.network]}],
            "withMetadata": True,
            "withPrices": True,
            "includeNativeTokens": False,
        }
        out = []
        while True:
            res = self._request(url, payload)
            if "error" in res:
                raise AlchemyError(f"Portfolio API: {res['error']}")
            data = res.get("data", {})
            out.extend(t for t in data.get("tokens", []) if t.get("tokenAddress"))
            if not data.get("pageKey"):
                return out
            payload["pageKey"] = data["pageKey"]

    def transfers(self, address):
        """Semua transfer ETH (external + internal) dan ERC-20 masuk/keluar wallet."""
        out = []
        for direction in ("fromAddress", "toAddress"):
            params = {
                "fromBlock": "0x0",
                "toBlock": "latest",
                direction: address,
                "category": ["external", "internal", "erc20"],
                "withMetadata": False,
                "excludeZeroValue": True,
                "maxCount": "0x3e8",
            }
            while True:
                res = self.rpc("alchemy_getAssetTransfers", [params])
                out.extend(res.get("transfers", []))
                if not res.get("pageKey"):
                    break
                params["pageKey"] = res["pageKey"]
        return out


# ---------------------------------------------------------------------------
# Ledger: riwayat transfer -> deposit/withdraw + posisi per koin
# ---------------------------------------------------------------------------

@dataclass
class Position:
    token: str
    symbol: str
    qty_bought: float = 0.0
    qty_sold: float = 0.0
    eth_spent: float = 0.0
    eth_received: float = 0.0
    qty_open: float = 0.0     # jumlah yang masih dipegang menurut riwayat
    cost_open: float = 0.0    # modal (ETH) untuk qty_open, metode average cost
    realized: float = 0.0
    buys: int = 0
    sells: int = 0
    received_free: float = 0.0  # token masuk tanpa bayar (airdrop / alokasi launch)

    def buy(self, qty, cost):
        self.qty_bought += qty
        self.eth_spent += cost
        self.qty_open += qty
        self.cost_open += cost
        self.buys += 1

    def receive_free(self, qty):
        self.received_free += qty
        self.qty_open += qty

    def _take(self, qty):
        cost = self.cost_open * min(qty / self.qty_open, 1.0) if self.qty_open > DUST else 0.0
        self.cost_open -= cost
        self.qty_open = max(self.qty_open - qty, 0.0)
        self.qty_sold += qty
        return cost

    def sell(self, qty, proceeds):
        cost = self._take(qty)
        self.eth_received += proceeds
        self.realized += proceeds - cost
        self.sells += 1

    def swap_out(self, qty):
        """Koin dituker ke koin lain: modalnya pindah, bukan realized PnL."""
        self.sells += 1
        return self._take(qty)


@dataclass
class Ledger:
    positions: dict = field(default_factory=dict)
    deposits: float = 0.0
    withdrawals: float = 0.0
    other_in: float = 0.0    # ETH masuk dari selain owner (fee kreator, reward, dll)
    other_out: float = 0.0   # ETH keluar ke selain owner tanpa dapat koin
    tx_count: int = 0


def _amount(t):
    raw = t.get("rawContract") or {}
    if raw.get("value") and raw.get("decimal"):
        return int(raw["value"], 16) / 10 ** int(raw["decimal"], 16)
    return float(t.get("value") or 0)


def build_ledger(wallet, transfers, weth=None, owners=None):
    """Kelompokkan transfer per tx hash, lalu klasifikasi tiap tx jadi
    beli / jual / swap / deposit / withdraw."""
    wallet = wallet.lower()
    weth = (weth or "").lower()
    owners = {o.lower() for o in owners or []}

    txs = {}
    symbols = {}
    seen = set()
    for t in transfers:
        uid = t.get("uniqueId") or json.dumps(t, sort_keys=True)
        if uid in seen:
            continue
        seen.add(uid)
        frm, to = (t.get("from") or "").lower(), (t.get("to") or "").lower()
        if frm == to:
            continue
        sign = 1 if to == wallet else -1 if frm == wallet else 0
        if not sign:
            continue
        tx = txs.setdefault(t["hash"], {
            "block": int(t.get("blockNum") or "0x0", 16),
            "eth": 0.0, "tokens": defaultdict(float), "peers": set(),
        })
        token = ((t.get("rawContract") or {}).get("address") or "").lower()
        amount = sign * _amount(t)
        if t["category"] in ("external", "internal") or (token and token == weth):
            tx["eth"] += amount
            tx["peers"].add(frm if sign > 0 else to)
        elif t["category"] == "erc20" and token:
            tx["tokens"][token] += amount
            symbols.setdefault(token, t.get("asset") or token[:10])

    led = Ledger()

    def pos(token):
        if token not in led.positions:
            led.positions[token] = Position(token, symbols.get(token, token[:10]))
        return led.positions[token]

    for tx in sorted(txs.values(), key=lambda x: x["block"]):
        led.tx_count += 1
        eth = tx["eth"]
        bought = {k: v for k, v in tx["tokens"].items() if v > DUST}
        sold = {k: -v for k, v in tx["tokens"].items() if v < -DUST}

        if not bought and not sold:
            from_owner = not owners or tx["peers"] <= owners
            if eth > 0:
                if from_owner:
                    led.deposits += eth
                else:
                    led.other_in += eth
            elif eth < 0:
                if from_owner:
                    led.withdrawals += -eth
                else:
                    led.other_out += -eth
            continue

        if sold and not bought:
            share = eth / len(sold)
            for token, qty in sold.items():
                pos(token).sell(qty, share)
            continue

        # Beli, atau swap koin -> koin: modal koin yang dijual pindah ke koin yang dibeli.
        carried = sum(pos(token).swap_out(qty) for token, qty in sold.items())
        if eth > 0:
            if sold:
                for token in sold:
                    pos(token).realized += eth / len(sold)
                    pos(token).eth_received += eth / len(sold)
            else:
                led.other_in += eth
        cost = carried + max(-eth, 0.0)
        for token, qty in bought.items():
            if cost > DUST:
                pos(token).buy(qty, cost / len(bought))
            else:
                pos(token).receive_free(qty)
    return led


# ---------------------------------------------------------------------------
# Format
# ---------------------------------------------------------------------------

def f_eth(x):
    return "-" if x is None else f"{x:.9f}"


def f_signed(x):
    if x is None:
        return "-"
    return f"{0.0 if abs(x) < 5e-10 else x:+.9f}"


def f_pct(x):
    return "-" if x is None else f"{x * 100:+.2f}%"


def f_qty(x):
    return f"{x:,.4f}" if x < 1e6 else f"{x:,.0f}"


def f_usd(x, rate):
    if not rate or x is None:
        return ""
    v = x * rate
    return f"  (~{'-' if v < 0 else ''}${abs(v):,.2f})"


def ratio(a, b):
    return a / b if b and abs(b) > DUST else None


def table(headers, rows):
    widths = [max(len(str(c)) for c in col) for col in zip(headers, *rows)]
    fmt = "  ".join(("{:<%d}" if i == 0 else "{:>%d}") % w for i, w in enumerate(widths))
    lines = [fmt.format(*headers), "  ".join("-" * w for w in widths)]
    lines += [fmt.format(*map(str, r)) for r in rows]
    return "\n".join(lines)


def summary_block(lines):
    width = max(len(k) for k, _ in lines if k)
    return "\n".join(f"  {k:<{width}}  {v}" if k else "" for k, v in lines)


# ---------------------------------------------------------------------------
# Mode scan
# ---------------------------------------------------------------------------

def _usd_price(token):
    for p in token.get("tokenPrices") or []:
        if p.get("currency") == "usd":
            return float(p["value"])
    return None


def analyze(eth_balance, holdings, ledger, eth_usd=None, overrides=None, only_traded=False):
    """Gabungin saldo on-chain + ledger jadi angka portfolio dan PnL."""
    overrides = {k.upper() if not k.startswith("0x") else k.lower(): v
                 for k, v in (overrides or {}).items()}
    held = {}
    for t in holdings:
        meta = t.get("tokenMetadata") or {}
        decimals = meta.get("decimals")
        balance = int(t.get("tokenBalance") or "0x0", 16) / 10 ** (18 if decimals is None else decimals)
        if balance > DUST:
            held[t["tokenAddress"].lower()] = {
                "symbol": meta.get("symbol"), "balance": balance, "price_usd": _usd_price(t),
            }

    coins = []
    for addr in sorted(set(held) | set(ledger.positions)):
        p = ledger.positions.get(addr) or Position(addr, addr[:10])
        h = held.get(addr, {})
        symbol = h.get("symbol") or p.symbol
        balance = h.get("balance", 0.0)
        cost = p.cost_open * min(balance / p.qty_open, 1.0) if p.qty_open > DUST else 0.0
        override = overrides.get(addr, overrides.get((symbol or "").upper()))
        if balance <= DUST:
            value, source = 0.0, "sudah dijual"
        elif override is not None:
            value, source = override, "manual"
        elif h.get("price_usd") is not None and eth_usd:
            value, source = balance * h["price_usd"] / eth_usd, "Alchemy"
        elif cost > DUST:
            value, source = cost, "=modal (harga ga ketemu)"
        else:
            value, source = None, "harga ga ketemu"
        coins.append({
            "token": addr, "symbol": symbol, "balance": balance,
            "cost_eth": cost, "value_eth": value, "price_source": source,
            "upnl_eth": None if value is None or balance <= DUST else value - cost,
            "eth_spent": p.eth_spent, "eth_received": p.eth_received,
            "realized_eth": p.realized, "buys": p.buys, "sells": p.sells,
            "free": p.eth_spent <= DUST,
        })

    bags = [c for c in coins if c["balance"] > DUST and not c["free"]]
    free = [c for c in coins if c["balance"] > DUST and c["free"]]
    closed = [c for c in coins if not c["free"] and (c["sells"] or c["balance"] <= DUST)]
    free_value = 0.0 if only_traded else sum(c["value_eth"] or 0.0 for c in free)

    coins_value = sum(c["value_eth"] for c in bags) + free_value
    total = eth_balance + coins_value
    net_deposit = ledger.deposits - ledger.withdrawals
    total_pnl = total - net_deposit
    realized = sum(c["realized_eth"] for c in coins)
    unrealized = sum(c["upnl_eth"] for c in bags)
    other = ledger.other_in - ledger.other_out
    return {
        "eth_usd": eth_usd,
        "eth_balance": eth_balance,
        "coins_value_eth": coins_value,
        "total_portfolio_eth": total,
        "deposits_eth": ledger.deposits,
        "withdrawals_eth": ledger.withdrawals,
        "net_deposit_eth": net_deposit,
        "total_pnl_eth": total_pnl,
        "total_pnl_pct": ratio(total_pnl, net_deposit),
        "realized_eth": realized,
        "unrealized_eth": unrealized,
        "free_coins_value_eth": free_value,
        "other_eth": other,
        "gas_and_fees_eth": total_pnl - realized - unrealized - other - free_value,
        "tx_count": ledger.tx_count,
        "bags": bags, "closed": closed, "free": free,
    }


def render_scan(wallet, network, r, only_traded=False):
    rate = r["eth_usd"]
    out = [f"SYNTH WALLET {wallet}  [{network}]"
           + (f"   ETH/USD ${rate:,.2f}" if rate else ""), ""]

    out.append(f"BAGS - koin yang lagi dipegang ({len(r['bags'])})")
    if r["bags"]:
        rows = [[c["symbol"][:16], f_qty(c["balance"]), f_eth(c["cost_eth"]), f_eth(c["value_eth"]),
                 f_signed(c["upnl_eth"]), f_pct(ratio(c["upnl_eth"], c["cost_eth"])), c["price_source"]]
                for c in r["bags"]]
        cost = sum(c["cost_eth"] for c in r["bags"])
        value = sum(c["value_eth"] for c in r["bags"])
        rows.append(["TOTAL", "", f_eth(cost), f_eth(value), f_signed(value - cost),
                     f_pct(ratio(value - cost, cost)), ""])
        out.append(table(["Koin", "Jumlah", "Modal ETH", "Nilai ETH", "UPnL ETH", "%", "Harga"], rows))
    else:
        out.append("  (kosong)")

    if r["closed"]:
        out += ["", f"RIWAYAT TRADE - realized PnL ({len(r['closed'])} koin)"]
        rows = [[c["symbol"][:16], c["buys"], c["sells"], f_eth(c["eth_spent"]), f_eth(c["eth_received"]),
                 f_signed(c["realized_eth"])] for c in r["closed"]]
        out.append(table(["Koin", "#Beli", "#Jual", "Beli ETH", "Jual ETH", "Realized ETH"], rows))

    priced_free = [c for c in r["free"] if c["value_eth"] is not None]
    if priced_free:
        note = "TIDAK dihitung (--only-traded)" if only_traded else "ikut dihitung"
        out += ["", f"KOIN TANPA MODAL - airdrop / alokasi launch, {note}"]
        rows = [[c["symbol"][:16], f_qty(c["balance"]), f_eth(c["value_eth"]), c["token"]] for c in priced_free]
        out.append(table(["Koin", "Jumlah", "Nilai ETH", "Kontrak"], rows))
    hidden = len(r["free"]) - len(priced_free)
    if hidden:
        out += ["", f"({hidden} koin airdrop/spam tanpa harga disembunyikan, nilainya nggak dihitung. "
                    "Kalau ada yang asli, isi nilainya pakai --value KOIN=ETH)"]

    lines = [
        ("Saldo ETH di wallet", f_eth(r["eth_balance"]) + f_usd(r["eth_balance"], rate)),
        ("Nilai semua koin", f_eth(r["coins_value_eth"]) + f_usd(r["coins_value_eth"], rate)),
        ("TOTAL PORTFOLIO", f_eth(r["total_portfolio_eth"]) + f_usd(r["total_portfolio_eth"], rate)),
        ("", ""),
        ("Total fund (deposit)", f_eth(r["deposits_eth"])),
        ("Total withdraw", f_eth(r["withdrawals_eth"])),
        ("Modal bersih", f_eth(r["net_deposit_eth"]) + f_usd(r["net_deposit_eth"], rate)),
        ("", ""),
        ("PROFIT TOTAL", f"{f_signed(r['total_pnl_eth'])}  {f_pct(r['total_pnl_pct'])}"
                         + f_usd(r["total_pnl_eth"], rate)),
        ("  realized (koin dijual)", f_signed(r["realized_eth"])),
        ("  unrealized (bags)", f_signed(r["unrealized_eth"])),
    ]
    if r["free_coins_value_eth"]:
        lines.append(("  koin tanpa modal", f_signed(r["free_coins_value_eth"])))
    if r["other_eth"]:
        lines.append(("  ETH lain masuk/keluar", f_signed(r["other_eth"])))
    lines.append(("  gas & fee (selisih)", f_signed(r["gas_and_fees_eth"])))
    out += ["", f"RINGKASAN ({r['tx_count']} transaksi)", summary_block(lines)]
    return "\n".join(out)


def cmd_scan(args):
    api_key = args.api_key or os.environ.get("ALCHEMY_API_KEY")
    if not api_key:
        sys.exit("Butuh API key Alchemy: set env ALCHEMY_API_KEY atau pakai --api-key.")
    client = Alchemy(api_key, args.network)
    wallet = args.wallet.lower()
    eth_balance = client.eth_balance(wallet)
    holdings = client.tokens(wallet)
    transfers = client.transfers(wallet)
    try:
        eth_usd = client.eth_usd()
    except AlchemyError:
        eth_usd = None
    ledger = build_ledger(wallet, transfers, WETH.get(args.network), args.owner)
    r = analyze(eth_balance, holdings, ledger, eth_usd, parse_overrides(args.value), args.only_traded)
    if args.json:
        print(json.dumps({"wallet": wallet, "network": args.network, **r}, indent=2))
    else:
        print(render_scan(wallet, args.network, r, args.only_traded))


def parse_overrides(items):
    out = {}
    for item in items or []:
        key, _, val = item.partition("=")
        if not val:
            sys.exit(f"--value harus format KOIN=ETH, contoh MOTHMAN=0.0017 (dapet: {item!r})")
        out[key.strip().lstrip("$")] = float(val)
    return out


# ---------------------------------------------------------------------------
# Mode calc
# ---------------------------------------------------------------------------

def resolve_bag(bag):
    """Lengkapi bag dari dua angka mana aja: cost_eth, value_eth, upnl_eth, pct."""
    cost, value, upnl = bag.get("cost_eth"), bag.get("value_eth"), bag.get("upnl_eth")
    p = bag.get("pct")
    p = None if p is None else p / 100
    if cost is None:
        if value is not None and upnl is not None:
            cost = value - upnl
        elif value is not None and p is not None:
            cost = value / (1 + p)
        elif upnl is not None and p:
            cost = upnl / p
    if cost is None:
        raise ValueError(f"bag {bag.get('coin')!r}: isi minimal dua dari cost_eth, value_eth, upnl_eth, pct")
    if value is None:
        if upnl is None:
            upnl = cost * p if p is not None else None
        if upnl is None:
            raise ValueError(f"bag {bag.get('coin')!r}: isi minimal dua dari cost_eth, value_eth, upnl_eth, pct")
        value = cost + upnl
    upnl = value - cost
    return {"coin": bag.get("coin", "?"), "cost_eth": cost, "value_eth": value,
            "upnl_eth": upnl, "pct": ratio(upnl, cost)}


def parse_bag(spec):
    """KOIN:NILAI_ETH:UPNL_ETH  atau  KOIN:NILAI_ETH:PERSEN%"""
    parts = spec.split(":")
    if len(parts) != 3:
        sys.exit(f"--bag harus format KOIN:NILAI_ETH:UPNL_ETH atau KOIN:NILAI_ETH:PERSEN% (dapet: {spec!r})")
    coin, value, third = (x.strip() for x in parts)
    bag = {"coin": coin.lstrip("$"), "value_eth": float(value)}
    if third.endswith("%"):
        bag["pct"] = float(third[:-1])
    else:
        bag["upnl_eth"] = float(third)
    return bag


def calculate(data):
    bags = [resolve_bag(b) for b in data.get("bags", [])]
    wallet = float(data.get("wallet_eth") or 0.0)
    cost = sum(b["cost_eth"] for b in bags)
    value = sum(b["value_eth"] for b in bags)
    upnl = value - cost
    total = wallet + value
    r = {
        "name": data.get("name"),
        "eth_usd": data.get("eth_usd"),
        "bags": bags,
        "wallet_eth": wallet,
        "coins_cost_eth": cost,
        "coins_value_eth": value,
        "unrealized_eth": upnl,
        "unrealized_pct": ratio(upnl, cost),
        "total_portfolio_eth": total,
    }
    total_pnl = data.get("total_pnl_eth")
    if total_pnl is not None:
        r["total_pnl_eth"] = total_pnl
        r["realized_eth"] = total_pnl - upnl
        r["implied_capital_eth"] = total - total_pnl
    funded = data.get("funded_eth")
    if funded is not None:
        net = funded - float(data.get("withdrawn_eth") or 0.0)
        r["net_deposit_eth"] = net
        r["profit_eth"] = total - net
        r["profit_pct"] = ratio(total - net, net)
    for key, sign in (("take_profit_pct", 1), ("stop_loss_pct", -1)):
        level = data.get(key)
        if level is None or not bags:
            continue
        exit_value = sum(b["cost_eth"] * (1 + sign * abs(level) / 100) for b in bags)
        r[key.replace("_pct", "")] = {
            "level_pct": sign * abs(level),
            "wallet_after_eth": wallet + exit_value,
            "change_vs_now_eth": exit_value - value,
            "bags_pnl_eth": exit_value - cost,
        }
    return r


def render_calc(r):
    rate = r["eth_usd"]
    title = "KALKULATOR PROFIT" + (f" - {r['name']}" if r.get("name") else "")
    out = [title + (f"   ETH/USD ${rate:,.2f}" if rate else ""), ""]
    if r["bags"]:
        rows = [[b["coin"], f_eth(b["cost_eth"]), f_eth(b["value_eth"]), f_signed(b["upnl_eth"]), f_pct(b["pct"])]
                for b in r["bags"]]
        rows.append(["TOTAL", f_eth(r["coins_cost_eth"]), f_eth(r["coins_value_eth"]),
                     f_signed(r["unrealized_eth"]), f_pct(r["unrealized_pct"])])
        out += [f"BAGS ({len(r['bags'])} koin)",
                table(["Koin", "Modal ETH", "Nilai ETH", "UPnL ETH", "%"], rows), ""]

    lines = [
        ("Saldo ETH di wallet", f_eth(r["wallet_eth"]) + f_usd(r["wallet_eth"], rate)),
        ("Nilai semua koin", f_eth(r["coins_value_eth"]) + f_usd(r["coins_value_eth"], rate)),
        ("TOTAL PORTFOLIO", f_eth(r["total_portfolio_eth"]) + f_usd(r["total_portfolio_eth"], rate)),
        ("Unrealized PnL", f"{f_signed(r['unrealized_eth'])}  {f_pct(r['unrealized_pct'])}"),
    ]
    if "total_pnl_eth" in r:
        lines += [
            ("Total PnL (dari halaman)", f_signed(r["total_pnl_eth"])),
            ("  -> realized", f_signed(r["realized_eth"])),
            ("Modal awal (perkiraan)", f_eth(r["implied_capital_eth"]) + f_usd(r["implied_capital_eth"], rate)),
        ]
    if "profit_eth" in r:
        lines += [
            ("", ""),
            ("Modal bersih (fund-withdraw)", f_eth(r["net_deposit_eth"]) + f_usd(r["net_deposit_eth"], rate)),
            ("PROFIT TOTAL", f"{f_signed(r['profit_eth'])}  {f_pct(r['profit_pct'])}"
                             + f_usd(r["profit_eth"], rate)),
        ]
    out += ["RINGKASAN", summary_block(lines)]

    for key, label in (("take_profit", "TAKE PROFIT"), ("stop_loss", "STOP LOSS")):
        s = r.get(key)
        if not s:
            continue
        out += ["", f"SKENARIO {label} {s['level_pct']:+g}% - semua bag ditutup di level ini",
                summary_block([
                    ("Saldo wallet setelah exit", f_eth(s["wallet_after_eth"]) + f_usd(s["wallet_after_eth"], rate)),
                    ("PnL bags", f_signed(s["bags_pnl_eth"])),
                    ("Beda dari nilai sekarang", f_signed(s["change_vs_now_eth"])),
                ])]
    return "\n".join(out)


def cmd_calc(args):
    data = {}
    if args.file:
        with open(args.file) as fh:
            data = json.load(fh)
    flags = {
        "name": args.name, "wallet_eth": args.wallet, "funded_eth": args.funded,
        "withdrawn_eth": args.withdrawn, "total_pnl_eth": args.total_pnl,
        "take_profit_pct": args.tp, "stop_loss_pct": args.sl, "eth_usd": args.eth_usd,
    }
    data.update({k: v for k, v in flags.items() if v is not None})
    if args.bag:
        data["bags"] = [parse_bag(b) for b in args.bag]
    if data.get("wallet_eth") is None and not data.get("bags"):
        sys.exit("Isi minimal --wallet atau --bag (atau --file). Lihat --help.")
    if data.get("eth_usd") is None and not args.offline:
        api_key = args.api_key or os.environ.get("ALCHEMY_API_KEY")
        if api_key:
            try:
                data["eth_usd"] = Alchemy(api_key, "eth-mainnet", timeout=10).eth_usd()
            except AlchemyError:
                pass
    try:
        r = calculate(data)
    except ValueError as e:
        sys.exit(str(e))
    print(json.dumps(r, indent=2) if args.json else render_calc(r))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Profit calculator + total koin + saldo wallet untuk synth bot (synths.lol).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sc = sub.add_parser("scan", help="baca saldo, koin, dan PnL langsung dari chain (Alchemy)")
    sc.add_argument("wallet", help="alamat wallet si synth (0x...)")
    sc.add_argument("--network", default="base-mainnet",
                    help="network Alchemy, default base-mainnet (contoh lain: eth-mainnet, arb-mainnet)")
    sc.add_argument("--owner", action="append", metavar="ADDR",
                    help="wallet lu yang nge-fund/withdraw. Kalau diisi, ETH dari/ke alamat lain "
                         "dihitung sebagai pemasukan/pengeluaran, bukan deposit. Bisa diulang.")
    sc.add_argument("--value", action="append", metavar="KOIN=ETH",
                    help="override nilai bag dalam ETH (pakai angka dari tab Bags), contoh MOTHMAN=0.0017. "
                         "KOIN bisa simbol atau alamat kontrak. Bisa diulang.")
    sc.add_argument("--only-traded", action="store_true",
                    help="jangan hitung koin yang masuk tanpa dibeli (airdrop/spam/alokasi launch)")
    sc.add_argument("--api-key", help="API key Alchemy (default: env ALCHEMY_API_KEY)")
    sc.add_argument("--json", action="store_true", help="output JSON")
    sc.set_defaults(func=cmd_scan)

    ca = sub.add_parser("calc", help="kalkulator manual dari angka di halaman synth")
    ca.add_argument("--file", help="file JSON (lihat example_kinbit.json)")
    ca.add_argument("--name", help="nama synth")
    ca.add_argument("--wallet", type=float, metavar="ETH", help="saldo wallet (angka WALLET - ETH)")
    ca.add_argument("--bag", action="append", metavar="SPEC",
                    help="KOIN:NILAI_ETH:UPNL_ETH atau KOIN:NILAI_ETH:PERSEN%%, "
                         "contoh MOTHMAN:0.0017:0.000035698. Bisa diulang.")
    ca.add_argument("--total-pnl", type=float, metavar="ETH", help="angka Total PnL di halaman synth")
    ca.add_argument("--funded", type=float, metavar="ETH", help="total ETH yang pernah lu fund")
    ca.add_argument("--withdrawn", type=float, metavar="ETH", help="total ETH yang pernah lu withdraw")
    ca.add_argument("--tp", type=float, metavar="PCT", help="take profit %% (contoh 6)")
    ca.add_argument("--sl", type=float, metavar="PCT", help="stop loss %% (contoh 6)")
    ca.add_argument("--eth-usd", type=float, help="harga ETH dalam USD (default: ambil dari Alchemy)")
    ca.add_argument("--offline", action="store_true", help="jangan ambil harga ETH dari internet")
    ca.add_argument("--api-key", help="API key Alchemy (default: env ALCHEMY_API_KEY)")
    ca.add_argument("--json", action="store_true", help="output JSON")
    ca.set_defaults(func=cmd_calc)

    args = ap.parse_args(argv)
    try:
        args.func(args)
    except AlchemyError as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
