#!/usr/bin/env python3
"""Fund from the terminal: what the fund manager (Claude) reads and writes.

  python3 fund.py report                      everything that matters, JSON
  python3 fund.py lp [--days 30]              LP value + money flow (read-only, no RPC)
  python3 fund.py export                      every table, JSON
  python3 fund.py rec add "title" --body "…" [--sleeve N] [--expected IDR/month]
  python3 fund.py rec list [--status proposed|accepted|done|rejected]
  python3 fund.py rec set ID --status done [--result "…"] [--actual IDR/month]
  python3 fund.py account "BCA" --kind bank --sleeve 0 [--currency IDR]
  python3 fund.py balance "BCA" 25000000 [--day 2026-11-01] [--note …]
  python3 fund.py spend 85000 makan [--note …] [--day …]
  python3 fund.py earn 15000000 gaji [--note …] [--day …]
  python3 fund.py budget makan 3000000
  python3 fund.py fx 16450
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import lp  # noqa: E402
import server  # noqa: E402
import store  # noqa: E402


def out(x):
    print(json.dumps(x, indent=1, ensure_ascii=False, default=str))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("report")
    s.add_argument("--day")
    s = sub.add_parser("lp")
    s.add_argument("--days", type=int, default=30)
    sub.add_parser("export")
    rec = sub.add_parser("rec").add_subparsers(dest="rcmd", required=True)
    s = rec.add_parser("add")
    s.add_argument("title")
    s.add_argument("--body")
    s.add_argument("--sleeve", type=int)
    s.add_argument("--expected", type=float, help="IDR per month it should add")
    s = rec.add_parser("list")
    s.add_argument("--status", choices=store.REC_STATUS)
    s = rec.add_parser("set")
    s.add_argument("id", type=int)
    s.add_argument("--status", choices=store.REC_STATUS)
    s.add_argument("--result")
    s.add_argument("--actual", type=float, help="IDR per month it really added")
    s = sub.add_parser("account")
    s.add_argument("name")
    s.add_argument("--kind", default="other", choices=store.KINDS)
    s.add_argument("--sleeve", type=int, default=5)
    s.add_argument("--currency", default="IDR", choices=store.CURRENCIES)
    s = sub.add_parser("balance")
    s.add_argument("account")
    s.add_argument("amount", type=float)
    s.add_argument("--day")
    s.add_argument("--note")
    for name in ("spend", "earn"):
        s = sub.add_parser(name)
        s.add_argument("amount", type=float)
        s.add_argument("category")
        s.add_argument("--note")
        s.add_argument("--day")
        s.add_argument("--currency", default="IDR", choices=store.CURRENCIES)
    s = sub.add_parser("budget")
    s.add_argument("category")
    s.add_argument("monthly", type=float, help="0 removes it")
    s = sub.add_parser("fx")
    s.add_argument("usd_idr", type=float)
    s.add_argument("--day")
    a = ap.parse_args(argv)

    cfg = server.load_config()
    books = store.Books(server.db_path(cfg), cfg["usd_idr"])
    try:
        if a.cmd == "report":
            r = books.summary(a.day)
            r["lp"] = server.LPTracker(books, cfg).status() if cfg.get("lp_wallets") else {"enabled": False}
            r["recs_open"] = [x for x in books.recs() if x["status"] in ("proposed", "accepted")]
            out(r)
        elif a.cmd == "lp":
            t = server.LPTracker(books, cfg)
            if not t.enabled:
                return out({"enabled": False, "hint": "add your wallets to lp_wallets in config.json"})
            t.refresh()
            out(t.status(a.days))
        elif a.cmd == "export":
            out(books.export())
        elif a.cmd == "rec":
            if a.rcmd == "add":
                out({"id": books.add_rec({"title": a.title, "body": a.body, "sleeve": a.sleeve,
                                          "expected_idr_month": a.expected})})
            elif a.rcmd == "list":
                out(books.recs(a.status))
            else:
                d = {k: v for k, v in (("status", a.status), ("result", a.result),
                                       ("actual_idr_month", a.actual)) if v is not None}
                books.update_rec(a.id, d)
                out({"ok": True})
        elif a.cmd == "account":
            out({"id": books.save_account({"name": a.name, "kind": a.kind, "sleeve": a.sleeve,
                                           "currency": a.currency})})
        elif a.cmd == "balance":
            out({"id": books.set_balance(a.account, a.amount, a.day, a.note)})
        elif a.cmd in ("spend", "earn"):
            out({"id": books.add_flow({"kind": "expense" if a.cmd == "spend" else "income", "amount": a.amount,
                                       "category": a.category, "note": a.note, "day": a.day,
                                       "currency": a.currency})})
        elif a.cmd == "budget":
            books.set_budget(a.category, a.monthly)
            out({"ok": True})
        elif a.cmd == "fx":
            books.set_fx(a.usd_idr, a.day)
            out({"ok": True})
    except (store.Invalid, lp.Unavailable) as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
