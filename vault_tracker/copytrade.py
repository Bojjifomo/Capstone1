"""Copy-trade helpers: your own LP range for a leader position + leader/follower sync."""

from . import dlmm

POSITION_MAX_BINS = 1400


def parse_mode(mode):
    """'mirror' | 'pct:-10,10' | 'scale:2'  ->  (kind, args)"""
    mode = (mode or "mirror").strip().lower()
    if mode == "mirror":
        return "mirror", ()
    kind, _, arg = mode.partition(":")
    if kind == "pct":
        lo, hi = (float(v) for v in arg.split(","))
        if lo >= hi:
            raise ValueError("mode pct butuh LOW < HIGH, misalnya pct:-10,10")
        return "pct", (lo, hi)
    if kind == "scale":
        f = float(arg)
        if f <= 0:
            raise ValueError("faktor scale harus > 0")
        return "scale", (f,)
    raise ValueError(f"copy mode {mode!r} tidak dikenal (pakai mirror, pct:LOW,HIGH, atau scale:F)")


def suggest_range(row, mode="mirror"):
    """Suggested follower range for a leader position row (from VaultTracker.snapshot)."""
    kind, args = parse_mode(mode)
    active, step = row["active_bin"], row["bin_step"]
    dx, dy = row["decimals_x"], row["decimals_y"]
    if kind == "mirror":
        lower, upper = row["lower_bin"], row["upper_bin"]
    elif kind == "pct":
        lo, hi = args
        price = row["price"]
        lower = dlmm.price_to_bin_id(price * (1 + lo / 100), step, dx, dy, round_down=True)
        upper = dlmm.price_to_bin_id(price * (1 + hi / 100), step, dx, dy, round_down=False)
    else:
        f = args[0]
        lower = active + round((row["lower_bin"] - active) * f)
        upper = active + round((row["upper_bin"] - active) * f)
        if upper < lower:
            lower, upper = upper, lower
    width = upper - lower + 1
    if row.get("kind") == "order":
        if row["side"] == "ask":
            deposit, shape = row["symbol_x"], f"limit order jual {row['symbol_x']} (ask)"
        else:
            deposit, shape = row["symbol_y"], f"limit order beli {row['symbol_x']} pakai {row['symbol_y']} (bid)"
    elif upper < active:
        deposit = row["symbol_y"]
        shape = f"one-sided {row['symbol_y']} (bid di bawah harga)"
    elif lower > active:
        deposit = row["symbol_x"]
        shape = f"one-sided {row['symbol_x']} (ask di atas harga)"
    else:
        deposit = f"{row['symbol_x']} + {row['symbol_y']}"
        shape = "dua sisi (range melingkupi harga sekarang)"
    note = None
    if width > POSITION_MAX_BINS:
        note = f"lebar {width} bins > {POSITION_MAX_BINS}, perlu dipecah jadi beberapa posisi"
    return {
        "mode": mode, "lower_bin": lower, "upper_bin": upper, "width": width,
        "price_lower": dlmm.bin_price(lower, step, dx, dy),
        "price_upper": dlmm.bin_price(upper, step, dx, dy),
        "pct_lower": (dlmm.bin_price(lower, step, dx, dy) / row["price"] - 1) * 100,
        "pct_upper": (dlmm.bin_price(upper, step, dx, dy) / row["price"] - 1) * 100,
        "deposit": deposit, "shape": shape, "note": note,
        "url": f"https://app.meteora.ag/dlmm/{row['lb_pair']}",
    }


def sync_status(leader_snap, follower_snap):
    """Per-pool comparison of leader vs follower open positions."""
    if not leader_snap or not follower_snap:
        return []
    pools = {}
    for side, snap in (("leader", leader_snap), ("follower", follower_snap)):
        for r in snap["positions"]:
            p = pools.setdefault(r["lb_pair"], {"lb_pair": r["lb_pair"], "pair": r["pair"],
                                                "leader": [], "follower": []})
            p[side].append(r)
    out = []
    for p in pools.values():
        lead = [r for r in p["leader"] if r["has_liquidity"]]
        foll = [r for r in p["follower"] if r["has_liquidity"]]
        if lead and foll:
            status = "SYNC"
        elif lead:
            status = "BELUM COPY"
        elif foll:
            status = "LEADER SUDAH CLOSE"
        else:
            continue
        p["status"] = status
        ranged = [r for r in foll if r.get("kind") != "order"]
        p["follower_in_range"] = all(r["in_range"] for r in ranged) if ranged else None
        out.append(p)
    order = {"LEADER SUDAH CLOSE": 0, "BELUM COPY": 1, "SYNC": 2}
    return sorted(out, key=lambda p: order[p["status"]])


def follower_positions_in_pool(follower_snap, lb_pair):
    if not follower_snap:
        return []
    return [r for r in follower_snap["positions"] if r["lb_pair"] == lb_pair and r["has_liquidity"]]
