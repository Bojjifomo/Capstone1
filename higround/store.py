"""HiGround: the club's books. Members (who connected intervals.icu), their runs, login sessions.

Standard library only (sqlite3). One row per activity, keyed by the intervals.icu activity id,
so a sync that sees the same run twice just updates it.
"""
import hashlib
import secrets
import sqlite3
import threading
import time

GENDERS = ("", "F", "M")
STATUSES = ("ok", "flagged", "approved", "rejected", "skipped")
COUNTED = ("ok", "approved")          # these statuses count on the leaderboard
SESSION_DAYS = 90

SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
  id INTEGER PRIMARY KEY,
  athlete_id TEXT NOT NULL UNIQUE,          -- intervals.icu athlete id, e.g. i12345
  name TEXT NOT NULL,                       -- as shown on the leaderboard
  gender TEXT NOT NULL DEFAULT '',          -- '', 'F', 'M' (empty = only in "All")
  token TEXT,                               -- intervals.icu access token; NULL = disconnected
  scope TEXT,
  public INTEGER NOT NULL DEFAULT 1,        -- 0 = hidden from the leaderboard
  is_admin INTEGER NOT NULL DEFAULT 0,
  joined TEXT NOT NULL,
  last_sync REAL,
  sync_error TEXT
);
CREATE TABLE IF NOT EXISTS activities (
  id TEXT PRIMARY KEY,                      -- intervals.icu activity id
  member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
  start_local TEXT NOT NULL,                -- 2026-11-23T05:31:00, the runner's local time
  day TEXT NOT NULL,                        -- 2026-11-23
  type TEXT NOT NULL,
  name TEXT,
  distance_m REAL NOT NULL DEFAULT 0,
  moving_s REAL NOT NULL DEFAULT 0,
  elapsed_s REAL NOT NULL DEFAULT 0,
  elev_m REAL NOT NULL DEFAULT 0,
  avg_hr REAL,
  max_hr REAL,
  source TEXT,
  device TEXT,
  status TEXT NOT NULL DEFAULT 'ok',
  reason TEXT,                              -- why it was flagged / skipped
  reviewed INTEGER NOT NULL DEFAULT 0       -- 1 = an admin decided; a re-sync keeps the decision
);
CREATE INDEX IF NOT EXISTS activities_member_day ON activities(member_id, day);
CREATE INDEX IF NOT EXISTS activities_day ON activities(day);
CREATE TABLE IF NOT EXISTS sessions (
  hash TEXT PRIMARY KEY, member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
  expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS oauth_states (state TEXT PRIMARY KEY, verifier TEXT NOT NULL, created REAL NOT NULL);
"""


class Invalid(ValueError):
    """Bad input from the browser; the message goes back as a 400."""


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Club:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.con = sqlite3.connect(str(path), check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA foreign_keys = ON")
        self.con.execute("PRAGMA journal_mode = WAL")
        self.con.executescript(SCHEMA)
        self.con.commit()

    # ── members ──────────────────────────────────────────────────────────────────

    def upsert_member(self, athlete_id, name, token, scope="", admin=False):
        """Called after a successful intervals.icu login. Keeps the name/gender a member set."""
        athlete_id = str(athlete_id)
        with self.lock:
            row = self.con.execute("SELECT id FROM members WHERE athlete_id = ?", (athlete_id,)).fetchone()
            if row:
                self.con.execute("UPDATE members SET token = ?, scope = ?, sync_error = NULL,"
                                 " is_admin = MAX(is_admin, ?) WHERE id = ?",
                                 (token, scope, int(admin), row["id"]))
                mid = row["id"]
            else:
                cur = self.con.execute(
                    "INSERT INTO members (athlete_id, name, token, scope, is_admin, joined)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (athlete_id, (name or "Runner").strip()[:60], token, scope, int(admin),
                     time.strftime("%Y-%m-%d")))
                mid = cur.lastrowid
            self.con.commit()
            return mid

    def member(self, mid):
        r = self.con.execute("SELECT * FROM members WHERE id = ?", (mid,)).fetchone()
        return dict(r) if r else None

    def members(self):
        return [dict(r) for r in self.con.execute("SELECT * FROM members ORDER BY name COLLATE NOCASE")]

    def connected(self):
        return [dict(r) for r in self.con.execute("SELECT * FROM members WHERE token IS NOT NULL")]

    def update_profile(self, mid, d):
        sets, args = [], []
        if "name" in d:
            name = " ".join(str(d["name"] or "").split())
            if not 1 <= len(name) <= 60:
                raise Invalid("Nama 1–60 karakter")
            sets.append("name = ?")
            args.append(name)
        if "gender" in d:
            if d["gender"] not in GENDERS:
                raise Invalid("Kategori nggak dikenal")
            sets.append("gender = ?")
            args.append(d["gender"])
        if "public" in d:
            sets.append("public = ?")
            args.append(1 if d["public"] else 0)
        if not sets:
            return
        with self.lock:
            self.con.execute("UPDATE members SET %s WHERE id = ?" % ", ".join(sets), args + [mid])
            self.con.commit()

    def set_admin_flags(self, mid, public=None):
        with self.lock:
            if public is not None:
                self.con.execute("UPDATE members SET public = ? WHERE id = ?", (int(bool(public)), mid))
            self.con.commit()

    def delete_member(self, mid):
        """Disconnect and forget: the member, their runs and sessions."""
        with self.lock:
            self.con.execute("DELETE FROM members WHERE id = ?", (mid,))
            self.con.commit()

    def sync_done(self, mid, error=None):
        with self.lock:
            if error:
                self.con.execute("UPDATE members SET sync_error = ? WHERE id = ?", (str(error)[:300], mid))
            else:
                self.con.execute("UPDATE members SET last_sync = ?, sync_error = NULL WHERE id = ?",
                                 (time.time(), mid))
            self.con.commit()

    def drop_token(self, mid, why):
        """The member revoked access on intervals.icu: keep their history, stop syncing."""
        with self.lock:
            self.con.execute("UPDATE members SET token = NULL, sync_error = ? WHERE id = ?", (why, mid))
            self.con.commit()

    # ── activities ───────────────────────────────────────────────────────────────

    def save_activities(self, mid, rows):
        """rows: dicts from intervals.normalize(). Returns how many were new."""
        new = 0
        with self.lock:
            for a in rows:
                old = self.con.execute("SELECT status, reviewed FROM activities WHERE id = ?",
                                       (a["id"],)).fetchone()
                status, reason = a["status"], a.get("reason")
                if old and old["reviewed"]:
                    status, reason = old["status"], None
                if not old:
                    new += 1
                self.con.execute(
                    "INSERT INTO activities (id, member_id, start_local, day, type, name, distance_m, moving_s,"
                    " elapsed_s, elev_m, avg_hr, max_hr, source, device, status, reason)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(id) DO UPDATE SET start_local=excluded.start_local, day=excluded.day,"
                    " type=excluded.type, name=excluded.name, distance_m=excluded.distance_m,"
                    " moving_s=excluded.moving_s, elapsed_s=excluded.elapsed_s, elev_m=excluded.elev_m,"
                    " avg_hr=excluded.avg_hr, max_hr=excluded.max_hr, source=excluded.source,"
                    " device=excluded.device, status=excluded.status, reason=excluded.reason",
                    (a["id"], mid, a["start_local"], a["start_local"][:10], a["type"], a.get("name"),
                     a["distance_m"], a["moving_s"], a["elapsed_s"], a["elev_m"], a.get("avg_hr"),
                     a.get("max_hr"), a.get("source"), a.get("device"), status, reason))
            self.con.commit()
        return new

    def forget_missing(self, mid, oldest, newest, seen_ids):
        """Runs deleted on intervals.icu inside the synced window disappear here too."""
        with self.lock:
            rows = self.con.execute("SELECT id FROM activities WHERE member_id = ? AND day BETWEEN ? AND ?",
                                    (mid, oldest, newest)).fetchall()
            gone = [r["id"] for r in rows if r["id"] not in seen_ids]
            for aid in gone:
                self.con.execute("DELETE FROM activities WHERE id = ?", (aid,))
            self.con.commit()
            return len(gone)

    def activities(self, mid=None, start=None, end=None, statuses=None):
        q, args = "SELECT * FROM activities WHERE 1=1", []
        if mid is not None:
            q += " AND member_id = ?"
            args.append(mid)
        if start:
            q += " AND day >= ?"
            args.append(start)
        if end:
            q += " AND day < ?"
            args.append(end)
        if statuses:
            q += " AND status IN (%s)" % ",".join("?" * len(statuses))
            args += list(statuses)
        return [dict(r) for r in self.con.execute(q + " ORDER BY start_local DESC", args)]

    def last_day(self, mid):
        r = self.con.execute("SELECT MAX(day) AS d FROM activities WHERE member_id = ?", (mid,)).fetchone()
        return r["d"]

    def review(self, aid, decision):
        if decision not in ("approved", "rejected"):
            raise Invalid("approved atau rejected")
        with self.lock:
            cur = self.con.execute("UPDATE activities SET status = ?, reviewed = 1 WHERE id = ?", (decision, aid))
            self.con.commit()
            if not cur.rowcount:
                raise Invalid("Aktivitas nggak ketemu")

    def flagged(self):
        return [dict(r) for r in self.con.execute(
            "SELECT a.*, m.name AS member_name FROM activities a JOIN members m ON m.id = a.member_id"
            " WHERE a.status = 'flagged' ORDER BY a.start_local DESC")]

    # ── sessions & OAuth state ───────────────────────────────────────────────────

    def new_session(self, mid):
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.con.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))
            self.con.execute("INSERT INTO sessions VALUES (?, ?, ?)",
                             (_hash(token), mid, time.time() + SESSION_DAYS * 86400))
            self.con.commit()
        return token

    def session_member(self, token):
        if not token:
            return None
        r = self.con.execute("SELECT member_id FROM sessions WHERE hash = ? AND expires > ?",
                             (_hash(token), time.time())).fetchone()
        return self.member(r["member_id"]) if r else None

    def end_session(self, token):
        with self.lock:
            self.con.execute("DELETE FROM sessions WHERE hash = ?", (_hash(token or ""),))
            self.con.commit()

    def new_state(self, verifier):
        """Remember a login attempt: its state (CSRF) and PKCE verifier."""
        state = secrets.token_urlsafe(24)
        with self.lock:
            self.con.execute("DELETE FROM oauth_states WHERE created < ?", (time.time() - 900,))
            self.con.execute("INSERT INTO oauth_states VALUES (?, ?, ?)", (state, verifier, time.time()))
            self.con.commit()
        return state

    def take_state(self, state):
        """One use, 15 minutes. -> the PKCE verifier, or None."""
        with self.lock:
            r = self.con.execute("SELECT verifier FROM oauth_states WHERE state = ? AND created > ?",
                                 (state or "", time.time() - 900)).fetchone()
            self.con.execute("DELETE FROM oauth_states WHERE state = ?", (state or "",))
            self.con.commit()
            return r["verifier"] if r else None
