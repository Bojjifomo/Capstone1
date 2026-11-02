"""Fund: the books. Accounts with dated balances (net worth), money in / out (expenses and
income), budgets, the USD/IDR rate, and the fund manager's recommendations with what came of them.

Standard library only (sqlite3). Every amount is kept in its account's currency and converted
to IDR with the rate valid on that day, so history does not move when today's rate does.
"""
import sqlite3
import threading
import time
from datetime import date, timedelta

SLEEVES = {
    0: "Safety / runway",
    1: "Saham IDX",
    2: "Crypto core",
    3: "Stable yield",
    4: "LP aktif",
    5: "Lainnya",
}
KINDS = ("cash", "bank", "rdpu", "deposito", "sbn", "stock", "crypto", "stable", "lp", "property", "debt", "other")
REC_STATUS = ("proposed", "accepted", "done", "rejected")
CURRENCIES = ("IDR", "USD")

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, kind TEXT NOT NULL DEFAULT 'other',
  currency TEXT NOT NULL DEFAULT 'IDR', sleeve INTEGER NOT NULL DEFAULT 5,
  auto TEXT,                       -- 'lp' = value filled from the LP tracker, read-only in the UI
  archived INTEGER NOT NULL DEFAULT 0, created TEXT
);
CREATE TABLE IF NOT EXISTS balances (
  account_id INTEGER NOT NULL, day TEXT NOT NULL, amount REAL NOT NULL, note TEXT,
  PRIMARY KEY (account_id, day)
);
CREATE TABLE IF NOT EXISTS fx (day TEXT PRIMARY KEY, usd_idr REAL NOT NULL);
CREATE TABLE IF NOT EXISTS flows (
  id INTEGER PRIMARY KEY, day TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('expense', 'income')),
  amount REAL NOT NULL, currency TEXT NOT NULL DEFAULT 'IDR', category TEXT NOT NULL, note TEXT, created TEXT
);
CREATE INDEX IF NOT EXISTS flows_day ON flows(day);
CREATE TABLE IF NOT EXISTS budgets (category TEXT PRIMARY KEY, monthly REAL NOT NULL);
CREATE TABLE IF NOT EXISTS recs (
  id INTEGER PRIMARY KEY, created TEXT NOT NULL, title TEXT NOT NULL, body TEXT,
  sleeve INTEGER, expected_idr_month REAL,     -- what it should add per month, in IDR
  status TEXT NOT NULL DEFAULT 'proposed', decided TEXT, done TEXT,
  result TEXT, actual_idr_month REAL,           -- what it really added, filled when done
  author TEXT NOT NULL DEFAULT 'claude'
);
"""


class Invalid(ValueError):
    """Bad input from the page or the CLI: shown back to the user as is."""


def today():
    return date.today().isoformat()


def _day(v, name="day"):
    v = (v or "").strip() or today()
    try:
        return date.fromisoformat(v).isoformat()
    except ValueError:
        raise Invalid(f"{name} must be YYYY-MM-DD")


def _num(v, name, positive=False):
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise Invalid(f"{name} must be a number")
    if x != x or x in (float("inf"), float("-inf")):
        raise Invalid(f"{name} must be a number")
    if positive and x <= 0:
        raise Invalid(f"{name} must be above 0")
    return x


def _text(v, name, required=True, limit=2000):
    v = (v or "").strip()
    if required and not v:
        raise Invalid(f"{name} is required")
    if len(v) > limit:
        raise Invalid(f"{name} is too long (max {limit})")
    return v


def connect(path):
    con = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    return con


class Books:
    def __init__(self, path, default_usd_idr=16500.0):
        self.con = connect(path)
        self.lock = threading.RLock()  # one connection, many server threads
        self.default_fx = float(default_usd_idr)

    # ── accounts + balances ───────────────────────────────────────────────────────
    def save_account(self, d):
        name = _text(d.get("name"), "name", limit=80)
        kind = (d.get("kind") or "other").strip()
        if kind not in KINDS:
            raise Invalid(f"kind must be one of {', '.join(KINDS)}")
        cur = (d.get("currency") or "IDR").upper()
        if cur not in CURRENCIES:
            raise Invalid("currency must be IDR or USD")
        sleeve = int(_num(d.get("sleeve", 5), "sleeve"))
        if sleeve not in SLEEVES:
            raise Invalid("sleeve must be 0-5")
        archived = 1 if d.get("archived") else 0
        with self.con:
            if d.get("id"):
                n = self.con.execute("UPDATE accounts SET name=?, kind=?, currency=?, sleeve=?, archived=? WHERE id=?",
                                     (name, kind, cur, sleeve, archived, int(d["id"]))).rowcount
                if not n:
                    raise Invalid("no such account")
                return int(d["id"])
            try:
                return self.con.execute("INSERT INTO accounts(name, kind, currency, sleeve, archived, created) "
                                        "VALUES(?,?,?,?,?,?)", (name, kind, cur, sleeve, archived, today())).lastrowid
            except sqlite3.IntegrityError:
                raise Invalid(f"there is already an account called {name!r}")

    def account(self, ref):
        """By id or by name (case-insensitive)."""
        if isinstance(ref, int) or str(ref).isdigit():
            r = self.con.execute("SELECT * FROM accounts WHERE id=?", (int(ref),)).fetchone()
        else:
            r = self.con.execute("SELECT * FROM accounts WHERE lower(name)=lower(?)", (str(ref).strip(),)).fetchone()
        if not r:
            raise Invalid(f"no account {ref!r}")
        return dict(r)

    def set_balance(self, ref, amount, day=None, note=None, allow_auto=False):
        acc = self.account(ref)
        if acc["auto"] and not allow_auto:
            raise Invalid(f"{acc['name']} is filled automatically")
        amount = _num(amount, "amount")
        day = _day(day)
        with self.con:
            self.con.execute("INSERT INTO balances(account_id, day, amount, note) VALUES(?,?,?,?) "
                             "ON CONFLICT(account_id, day) DO UPDATE SET amount=excluded.amount, note=excluded.note",
                             (acc["id"], day, amount, _text(note, "note", False, 300) or None))
        return acc["id"]

    def ensure_auto_account(self, auto, name, kind, currency, sleeve):
        r = self.con.execute("SELECT id FROM accounts WHERE auto=?", (auto,)).fetchone()
        if r:
            return r["id"]
        with self.con:
            return self.con.execute("INSERT INTO accounts(name, kind, currency, sleeve, auto, created) "
                                    "VALUES(?,?,?,?,?,?)", (name, kind, currency, sleeve, auto, today())).lastrowid

    def balance_history(self, ref, limit=60):
        acc = self.account(ref)
        return [dict(r) for r in self.con.execute(
            "SELECT day, amount, note FROM balances WHERE account_id=? ORDER BY day DESC LIMIT ?", (acc["id"], limit))]

    # ── FX ────────────────────────────────────────────────────────────────────────
    def set_fx(self, usd_idr, day=None):
        with self.con:
            self.con.execute("INSERT OR REPLACE INTO fx(day, usd_idr) VALUES(?,?)",
                             (_day(day), _num(usd_idr, "usd_idr", positive=True)))

    def fx_on(self, day):
        r = self.con.execute("SELECT usd_idr FROM fx WHERE day<=? ORDER BY day DESC LIMIT 1", (day,)).fetchone()
        if r:
            return r["usd_idr"]
        r = self.con.execute("SELECT usd_idr FROM fx ORDER BY day LIMIT 1").fetchone()  # before the first rate
        return r["usd_idr"] if r else self.default_fx

    def to_idr(self, amount, currency, day):
        return amount * (self.fx_on(day) if currency == "USD" else 1.0)

    # ── money in / out ────────────────────────────────────────────────────────────
    def add_flow(self, d):
        kind = d.get("kind") or "expense"
        if kind not in ("expense", "income"):
            raise Invalid("kind must be expense or income")
        cur = (d.get("currency") or "IDR").upper()
        if cur not in CURRENCIES:
            raise Invalid("currency must be IDR or USD")
        with self.con:
            return self.con.execute(
                "INSERT INTO flows(day, kind, amount, currency, category, note, created) VALUES(?,?,?,?,?,?,?)",
                (_day(d.get("day")), kind, _num(d.get("amount"), "amount", positive=True), cur,
                 _text(d.get("category"), "category", limit=40).lower(), _text(d.get("note"), "note", False, 300) or None,
                 time.strftime("%Y-%m-%dT%H:%M:%S"))).lastrowid

    def delete_flow(self, fid):
        with self.con:
            if not self.con.execute("DELETE FROM flows WHERE id=?", (int(fid),)).rowcount:
                raise Invalid("no such entry")

    def flows(self, start, end, kind=None):
        q = "SELECT * FROM flows WHERE day>=? AND day<?" + (" AND kind=?" if kind else "") + " ORDER BY day DESC, id DESC"
        return [dict(r) for r in self.con.execute(q, (start, end, kind) if kind else (start, end))]

    def set_budget(self, category, monthly):
        category = _text(category, "category", limit=40).lower()
        monthly = _num(monthly, "monthly")
        with self.con:
            if monthly <= 0:
                self.con.execute("DELETE FROM budgets WHERE category=?", (category,))
            else:
                self.con.execute("INSERT OR REPLACE INTO budgets(category, monthly) VALUES(?,?)", (category, monthly))

    # ── recommendations ───────────────────────────────────────────────────────────
    def add_rec(self, d, author="claude"):
        sleeve = d.get("sleeve")
        if sleeve not in (None, ""):
            sleeve = int(_num(sleeve, "sleeve"))
            if sleeve not in SLEEVES:
                raise Invalid("sleeve must be 0-5")
        else:
            sleeve = None
        exp = d.get("expected_idr_month")
        exp = None if exp in (None, "") else _num(exp, "expected_idr_month")
        with self.con:
            return self.con.execute(
                "INSERT INTO recs(created, title, body, sleeve, expected_idr_month, author) VALUES(?,?,?,?,?,?)",
                (today(), _text(d.get("title"), "title", limit=160), _text(d.get("body"), "body", False, 4000) or None,
                 sleeve, exp, author if author in ("claude", "me") else "claude")).lastrowid

    def update_rec(self, rid, d):
        r = self.con.execute("SELECT * FROM recs WHERE id=?", (int(rid),)).fetchone()
        if not r:
            raise Invalid("no such recommendation")
        status = d.get("status") or r["status"]
        if status not in REC_STATUS:
            raise Invalid(f"status must be one of {', '.join(REC_STATUS)}")
        decided = r["decided"] or (today() if status != "proposed" else None)
        done = today() if status == "done" and not r["done"] else (r["done"] if status == "done" else None)
        result = _text(d["result"], "result", False, 2000) if "result" in d else r["result"]
        actual = r["actual_idr_month"]
        if "actual_idr_month" in d:
            actual = None if d["actual_idr_month"] in (None, "") else _num(d["actual_idr_month"], "actual_idr_month")
        with self.con:
            self.con.execute("UPDATE recs SET status=?, decided=?, done=?, result=?, actual_idr_month=? WHERE id=?",
                             (status, decided, done, result or None, actual, int(rid)))

    def recs(self, status=None):
        q = "SELECT * FROM recs" + (" WHERE status=?" if status else "") + \
            " ORDER BY CASE status WHEN 'proposed' THEN 0 WHEN 'accepted' THEN 1 WHEN 'done' THEN 2 ELSE 3 END, id DESC"
        return [dict(r) for r in self.con.execute(q, (status,) if status else ())]

    # ── the numbers ───────────────────────────────────────────────────────────────
    def positions_on(self, day):
        """Every live account with its latest balance on or before `day`, in IDR."""
        out = []
        for a in self.con.execute("SELECT * FROM accounts WHERE archived=0 ORDER BY sleeve, name"):
            b = self.con.execute("SELECT day, amount FROM balances WHERE account_id=? AND day<=? "
                                 "ORDER BY day DESC LIMIT 1", (a["id"], day)).fetchone()
            amount = b["amount"] if b else 0.0
            idr = self.to_idr(amount, a["currency"], b["day"] if b else day)
            if a["kind"] == "debt":
                idr = -abs(idr)
            out.append({"id": a["id"], "name": a["name"], "kind": a["kind"], "currency": a["currency"],
                        "sleeve": a["sleeve"], "auto": a["auto"], "amount": amount, "idr": idr,
                        "as_of": b["day"] if b else None,
                        "age_days": (date.fromisoformat(day) - date.fromisoformat(b["day"])).days if b else None})
        return out

    def net_worth_on(self, day):
        return sum(p["idr"] for p in self.positions_on(day))

    def history(self, months=12, end=None):
        """Net worth at each month end for the last `months` months, then `end` (today)."""
        end = date.fromisoformat(end or today())
        points, first = [], date(end.year, end.month, 1)
        for i in range(months, 0, -1):
            y, m = first.year, first.month - i
            while m <= 0:
                y, m = y - 1, m + 12
            month_end = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)).isoformat()
            points.append([month_end, round(self.net_worth_on(month_end))])
        points.append([end.isoformat(), round(self.net_worth_on(end.isoformat()))])
        first_balance = self.con.execute("SELECT MIN(day) d FROM balances").fetchone()["d"]
        return [p for p in points if first_balance and p[0] >= first_balance] or points[-1:]

    def month_flows(self, month_start):
        start = date.fromisoformat(month_start)
        end = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
        spent, income, cats = 0.0, 0.0, {}
        for f in self.flows(start.isoformat(), end.isoformat()):
            v = self.to_idr(f["amount"], f["currency"], f["day"])
            if f["kind"] == "expense":
                spent += v
                cats[f["category"]] = cats.get(f["category"], 0.0) + v
            else:
                income += v
        return {"month": start.isoformat()[:7], "spent": round(spent), "income": round(income), "by_category": cats}

    def summary(self, day=None):
        day = _day(day)
        d = date.fromisoformat(day)
        pos = self.positions_on(day)
        nw = sum(p["idr"] for p in pos)
        nw_30 = self.net_worth_on((d - timedelta(days=30)).isoformat())
        sleeves = []
        for k, label in SLEEVES.items():
            v = sum(p["idr"] for p in pos if p["sleeve"] == k)
            if v or any(p["sleeve"] == k for p in pos):
                sleeves.append({"sleeve": k, "label": label, "idr": round(v),
                                "pct": round(100 * v / nw, 1) if nw > 0 else None})

        this_month = self.month_flows(d.replace(day=1).isoformat())
        past, m = [], d.replace(day=1)
        for _ in range(3):  # the three full months before this one
            m = (m - timedelta(days=1)).replace(day=1)
            past.append(self.month_flows(m.isoformat()))
        with_spend = [p for p in past if p["spent"] > 0]
        avg_spent = sum(p["spent"] for p in with_spend) / len(with_spend) if with_spend else None
        with_income = [p for p in past if p["income"] > 0]
        avg_income = sum(p["income"] for p in with_income) / len(with_income) if with_income else None
        safety = sum(p["idr"] for p in pos if p["sleeve"] == 0)
        budgets = {r["category"]: r["monthly"] for r in self.con.execute("SELECT * FROM budgets")}
        cats = sorted(set(budgets) | set(this_month["by_category"]),
                      key=lambda c: -this_month["by_category"].get(c, 0))
        recs = self.recs()
        done = [r for r in recs if r["status"] == "done"]
        return {
            "day": day,
            "usd_idr": self.fx_on(day),
            "net_worth_idr": round(nw),
            "net_worth_usd": round(nw / self.fx_on(day), 2),
            "change_30d_idr": round(nw - nw_30),
            "sleeves": sleeves,
            "accounts": [dict(p, idr=round(p["idr"])) for p in pos],
            "stale_accounts": [p["name"] for p in pos if not p["auto"] and (p["age_days"] is None or p["age_days"] > 35)],
            "history": self.history(12, day),
            "month": {
                "month": this_month["month"], "spent": this_month["spent"], "income": this_month["income"],
                "categories": [{"category": c, "spent": round(this_month["by_category"].get(c, 0)),
                                "budget": budgets.get(c)} for c in cats],
                "budget_total": round(sum(budgets.values())) if budgets else None,
            },
            "past_months": past,
            "avg_monthly_spend": round(avg_spent) if avg_spent else None,
            "avg_monthly_income": round(avg_income) if avg_income else None,
            "savings_rate_pct": round(100 * (avg_income - avg_spent) / avg_income, 1)
            if avg_income and avg_spent is not None else None,
            "runway_months": round(safety / avg_spent, 1) if avg_spent else None,
            "recs": {
                "open": sum(1 for r in recs if r["status"] in ("proposed", "accepted")),
                "done": len(done),
                "rejected": sum(1 for r in recs if r["status"] == "rejected"),
                "expected_idr_month_done": round(sum(r["expected_idr_month"] or 0 for r in done)),
                "actual_idr_month_done": round(sum(r["actual_idr_month"] or 0 for r in done)),
            },
        }

    def export(self):
        tables = ("accounts", "balances", "fx", "flows", "budgets", "recs")
        return {t: [dict(r) for r in self.con.execute(f"SELECT * FROM {t}")] for t in tables}
