#!/usr/bin/env python3
"""HiGround Run Club: monthly mileage leaderboard + each runner's dashboard.

Members connect intervals.icu once (OAuth); a background loop pulls their runs every
`sync_every_minutes`. Standard library only. Listens on 127.0.0.1; members reach it through
Tailscale Funnel (or Cloudflare Tunnel) on the Mac mini, see README.md.

    python3 server.py            the real thing (needs config.json, see config.example.json)
    python3 server.py --demo     fake members and runs in demo.db, to look around
"""
import argparse
import json
import mimetypes
import os
import sys
import threading
import time
import traceback
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import intervals  # noqa: E402
import stats  # noqa: E402
import store  # noqa: E402

STATIC = HERE / "static"
CONFIG_PATH = HERE / "config.json"
MAX_BODY = 16 * 1024
COOKIE = "hg_session"

DEFAULT_CONFIG = {
    "port": 7820,
    "bind": ["127.0.0.1"],
    "public_url": "",                 # https://<mac>.<tailnet>.ts.net, what members open
    "club_name": "HIGROUND RUN CLUB",
    "intervals_client_id": "",
    "intervals_client_secret": "",    # HG_INTERVALS_SECRET in the env wins
    "admins": [],                     # intervals.icu athlete ids, e.g. ["i12345"]
    "sync_every_minutes": 30,         # 48 calls/member/day, well under intervals.icu's 100
    "backfill_days": 730,             # how far back the first sync reads
    "db": "higround.db",
    "demo": False,
}


def load_config(path=CONFIG_PATH):
    cfg = dict(DEFAULT_CONFIG)
    if Path(path).exists():
        try:
            cfg.update(json.loads(Path(path).read_text()))
        except ValueError as e:
            print("config.json ignored: %s" % e, file=sys.stderr)
    cfg["intervals_client_secret"] = os.environ.get("HG_INTERVALS_SECRET", cfg.get("intervals_client_secret") or "")
    cfg["public_url"] = (cfg.get("public_url") or "").rstrip("/")
    return cfg


def db_path(cfg):
    p = Path(cfg["db"])
    return p if p.is_absolute() else HERE / p


def redirect_uri(cfg):
    return (cfg["public_url"] or "http://127.0.0.1:%d" % cfg["port"]) + "/auth/callback"


# ── sync ─────────────────────────────────────────────────────────────────────────

class Syncer:
    def __init__(self, club, cfg):
        self.club, self.cfg = club, cfg
        self.lock = threading.Lock()
        self.running = False
        self.last_manual = {}          # member id -> time of their last "sync now"

    def sync_member(self, m, full=False):
        """Pull one member's runs. Re-reads a few days back so edits and deletions show up."""
        if not m.get("token"):
            return {"error": "belum connect intervals.icu"}
        today = date.today()
        last = None if full else self.club.last_day(m["id"])
        oldest = (date.fromisoformat(last) - timedelta(days=7)) if last else \
            today - timedelta(days=int(self.cfg["backfill_days"]))
        try:
            raw = intervals.list_activities(m["token"], oldest.isoformat(), today.isoformat())
        except intervals.AuthError as e:
            self.club.drop_token(m["id"], "Akses intervals.icu dicabut. Connect ulang.")
            return {"error": str(e)}
        except intervals.ApiError as e:
            self.club.sync_done(m["id"], error=e)
            return {"error": str(e)}
        rows = [r for r in (intervals.normalize(a) for a in raw) if r]
        new = self.club.save_activities(m["id"], rows)
        gone = self.club.forget_missing(m["id"], oldest.isoformat(), today.isoformat(), {r["id"] for r in rows})
        self.club.sync_done(m["id"])
        return {"new": new, "seen": len(rows), "removed": gone}

    def sync_all(self):
        if not self.lock.acquire(blocking=False):
            return
        self.running = True
        try:
            for m in self.club.connected():
                try:
                    self.sync_member(m)
                except Exception:  # noqa: BLE001 - one bad member must not stop the rest
                    traceback.print_exc()
                time.sleep(0.5)    # stay far below intervals.icu's ~10 req/s
        finally:
            self.running = False
            self.lock.release()

    def loop(self):
        while True:
            self.sync_all()
            time.sleep(max(10, int(self.cfg["sync_every_minutes"])) * 60)


# ── HTTP ─────────────────────────────────────────────────────────────────────────

def make_handler(club, syncer, cfg):
    secure = cfg["public_url"].startswith("https://")
    admins = {str(a) for a in cfg.get("admins") or []}

    class Handler(BaseHTTPRequestHandler):
        server_version = "HiGround/1"

        def log_message(self, fmt, *args):
            pass

        # plumbing

        def _send(self, code, body, ctype="application/json", extra=None):
            if not isinstance(body, bytes):
                body = json.dumps(body, separators=(",", ":")).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; img-src 'self' data:; script-src 'self';"
                             " style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com;"
                             " frame-ancestors 'none'")
            for k, v in (extra or []):
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _redirect(self, to, cookie=None):
            extra = [("Location", to)]
            if cookie is not None:
                extra.append(("Set-Cookie", cookie))
            self._send(302, b"", "text/plain", extra)

        def _cookie(self, token, max_age):
            return "%s=%s; Path=/; Max-Age=%d; HttpOnly; SameSite=Lax%s" % (
                COOKIE, token, max_age, "; Secure" if secure else "")

        def _token(self):
            for part in self.headers.get("Cookie", "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == COOKIE:
                    return v
            return None

        def _me(self):
            return club.session_member(self._token())

        def _wrap(self, fn):
            try:
                fn()
            except BrokenPipeError:
                pass
            except store.Invalid as e:
                self._send(400, {"error": str(e)})
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                self._send(500, {"error": "server error"})

        def do_GET(self):
            self._wrap(self._get)

        def do_HEAD(self):
            self._wrap(self._get)

        def do_POST(self):
            self._wrap(self._post)

        # GET

        def _get(self):
            u = urlsplit(self.path)
            q = parse_qs(u.query)
            arg = lambda k, d=None: (q.get(k) or [d])[0]  # noqa: E731
            p = u.path

            if p == "/auth/login":
                if not cfg["intervals_client_id"]:
                    return self._send(503, b"intervals.icu belum disetel (config.json)", "text/plain; charset=utf-8")
                verifier, challenge = intervals.pkce_pair()
                state = club.new_state(verifier)
                return self._redirect(intervals.authorize_url(cfg["intervals_client_id"], redirect_uri(cfg),
                                                              state, challenge))
            if p == "/auth/callback":
                return self._callback(q)
            if p == "/auth/demo" and cfg["demo"]:
                m = club.member(int(arg("member", "0") or 0)) or (club.members() or [None])[0]
                if not m:
                    return self._redirect("/")
                return self._redirect("/#/me", self._cookie(club.new_session(m["id"]), store.SESSION_DAYS * 86400))

            if p == "/api/me":
                me = self._me()
                return self._send(200, {
                    "club": cfg["club_name"], "demo": bool(cfg["demo"]),
                    "can_login": bool(cfg["intervals_client_id"]) or bool(cfg["demo"]),
                    "me": public_me(me) if me else None,
                })
            if p == "/api/leaderboard":
                period = arg("period", "month")
                if period not in stats.PERIODS:
                    period = "month"
                gender = arg("gender", "all")
                members = club.members()
                acts = club.activities(statuses=store.COUNTED)
                board = stats.leaderboard(members, acts, period, gender, arg("metric", "distance"))
                me = self._me()
                board["me"] = me["id"] if me else None
                return self._send(200, board)

            me = self._me()
            if p.startswith("/api/") and not me:
                return self._send(401, {"error": "login dulu"})
            if p == "/api/dashboard":
                who = int(arg("member", me["id"]) or me["id"])
                target = club.member(who)
                if not target or (target["id"] != me["id"] and not target["public"] and not me["is_admin"]):
                    return self._send(404, {"error": "runner nggak ketemu"})
                own = target["id"] == me["id"]
                acts = club.activities(target["id"])
                if not own:
                    acts = [a for a in acts if a["status"] in store.COUNTED]
                d = stats.dashboard(target, acts, club.activities(statuses=store.COUNTED),
                                    arg("period", "month") if arg("period") in stats.PERIODS else "month")
                d["member"] = {"id": target["id"], "name": target["name"], "joined": target["joined"],
                               "own": own}
                d["garmin"] = any(intervals.is_garmin(a.get("device")) for a in d["recent"])
                return self._send(200, d)
            if p == "/api/admin":
                if not me["is_admin"]:
                    return self._send(403, {"error": "khusus admin"})
                return self._send(200, {
                    "members": [{k: m[k] for k in ("id", "athlete_id", "name", "gender", "public", "joined",
                                                    "last_sync", "sync_error", "is_admin")}
                                | {"connected": bool(m["token"])} for m in club.members()],
                    "flagged": club.flagged(),
                    "syncing": syncer.running,
                })
            if p.startswith("/api/"):
                return self._send(404, {"error": "not found"})
            return self._static(p)

        def _callback(self, q):
            arg = lambda k: (q.get(k) or [""])[0]  # noqa: E731
            if arg("error"):
                return self._redirect("/#/?login=denied")
            verifier = club.take_state(arg("state"))
            if not verifier or not arg("code"):
                return self._redirect("/#/?login=expired")
            try:
                tok = intervals.exchange_code(cfg["intervals_client_id"], cfg["intervals_client_secret"],
                                              arg("code"), verifier)
            except (intervals.ApiError, intervals.AuthError) as e:
                print("token exchange failed: %s" % e, file=sys.stderr)
                return self._redirect("/#/?login=failed")
            athlete = tok.get("athlete") or {}
            aid = str(athlete.get("id") or "")
            if not aid:
                return self._redirect("/#/?login=failed")
            mid = club.upsert_member(aid, athlete.get("name"), tok["access_token"], tok.get("scope", ""),
                                     admin=aid in admins)
            m = club.member(mid)
            threading.Thread(target=syncer.sync_member, args=(m,), daemon=True).start()
            return self._redirect("/#/me?welcome=1",
                                  self._cookie(club.new_session(mid), store.SESSION_DAYS * 86400))

        def _static(self, p):
            name = "index.html" if p in ("/", "") else p.lstrip("/")
            f = (STATIC / name).resolve()
            if STATIC.resolve() not in f.parents or not f.is_file():
                return self._send(404, {"error": "not found"})
            ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript") or ctype.endswith("svg+xml"):
                ctype += "; charset=utf-8"
            return self._send(200, f.read_bytes(), ctype)

        # POST

        def _post(self):
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                return self._send(415, {"error": "send JSON"})  # plain forms from other sites can't
            origin = self.headers.get("Origin")
            if origin and cfg["public_url"] and origin not in (cfg["public_url"], "http://127.0.0.1:%d" % cfg["port"],
                                                               "http://localhost:%d" % cfg["port"]):
                return self._send(403, {"error": "origin"})
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                return self._send(413, {"error": "too large"})
            try:
                d = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._send(400, {"error": "bad JSON"})
            if not isinstance(d, dict):
                return self._send(400, {"error": "send a JSON object"})
            p = urlsplit(self.path).path

            if p == "/auth/logout":
                club.end_session(self._token())
                return self._send(200, {"ok": True}, extra=[("Set-Cookie", self._cookie("", 0))])

            me = self._me()
            if not me:
                return self._send(401, {"error": "login dulu"})
            if p == "/api/profile":
                club.update_profile(me["id"], d)
                return self._send(200, {"me": public_me(club.member(me["id"]))})
            if p == "/api/sync":
                last = syncer.last_manual.get(me["id"], 0)
                if time.time() - last < 120:
                    return self._send(429, {"error": "Barusan sync. Coba lagi 2 menit lagi."})
                syncer.last_manual[me["id"]] = time.time()
                if cfg["demo"]:
                    return self._send(200, {"new": 0, "seen": 0, "removed": 0, "demo": True})
                return self._send(200, syncer.sync_member(club.member(me["id"])))
            if p == "/api/disconnect":
                if me.get("token") and not cfg["demo"]:
                    intervals.disconnect(me["token"])
                club.delete_member(me["id"])
                return self._send(200, {"ok": True}, extra=[("Set-Cookie", self._cookie("", 0))])

            if not me["is_admin"]:
                return self._send(403, {"error": "khusus admin"})
            if p == "/api/admin/review":
                club.review(str(d.get("id") or ""), d.get("decision"))
                return self._send(200, {"ok": True})
            if p == "/api/admin/member":
                club.set_admin_flags(int(d.get("id") or 0), public=d.get("public"))
                return self._send(200, {"ok": True})
            if p == "/api/admin/sync-all":
                if not cfg["demo"]:
                    threading.Thread(target=syncer.sync_all, daemon=True).start()
                return self._send(200, {"ok": True})
            return self._send(404, {"error": "not found"})

    return Handler


def public_me(m):
    return {"id": m["id"], "name": m["name"], "gender": m["gender"], "public": bool(m["public"]),
            "is_admin": bool(m["is_admin"]), "connected": bool(m["token"]), "joined": m["joined"],
            "last_sync": m["last_sync"], "sync_error": m["sync_error"]}


def serve_on(addr, port, handler):
    httpd = ThreadingHTTPServer((addr, port), handler)
    httpd.daemon_threads = True
    print("listening on http://%s:%d" % (addr, port), file=sys.stderr)
    httpd.serve_forever()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int)
    ap.add_argument("--bind", action="append", help="address to listen on (repeatable)")
    ap.add_argument("--demo", action="store_true", help="fake club in demo.db, no intervals.icu needed")
    args = ap.parse_args(argv)
    cfg = load_config()
    if args.port:
        cfg["port"] = args.port
    if args.bind:
        cfg["bind"] = args.bind
    if args.demo:
        cfg.update(demo=True, db="demo.db")

    club = store.Club(db_path(cfg))
    if cfg["demo"]:
        import demo
        demo.seed(club)
    syncer = Syncer(club, cfg)
    if not cfg["demo"] and cfg["intervals_client_id"]:
        threading.Thread(target=syncer.loop, daemon=True).start()
    elif not cfg["demo"]:
        print("intervals_client_id kosong: login & sync mati sampai config.json diisi", file=sys.stderr)
    handler = make_handler(club, syncer, cfg)
    threads = [threading.Thread(target=serve_on, args=(a, cfg["port"], handler), daemon=True) for a in cfg["bind"]]
    for t in threads:
        t.start()
    if cfg["demo"]:
        print("demo: buka http://127.0.0.1:%d" % cfg["port"], file=sys.stderr)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
