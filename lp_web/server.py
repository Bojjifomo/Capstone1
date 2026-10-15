#!/usr/bin/env python3
"""LP Web: run the RH Scriptable scripts (RH Pool, RH Portfolio, ...) in a Mac browser.

The scripts themselves are not copied into this repo. They are read straight
from the Scriptable folder in iCloud Drive, so whatever build is on the phone is
what runs here, and RH Secrets is read from the same place. Nothing the scripts
need ever goes into git.

This server only listens on 127.0.0.1. It does three things:
  * serves the page and the Scriptable compatibility layer (static/),
  * hands the page a script's source, and files for FileManager,
  * forwards the scripts' HTTP requests, because a browser page cannot call
    DexScreener, GeckoTerminal, Blockscout or the RPC nodes directly (CORS).

Every /api call needs the per-run token embedded in the page, and the Host
header must be localhost, so another website open in the same browser cannot
read the secrets file or use the forwarder.

Standard library only, Python 3.8+. Run:  python3 server.py
"""

import argparse
import json
import os
import secrets
import shutil
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
LOCAL = os.path.join(HERE, "local")

# Where Scriptable keeps its scripts when iCloud Drive is on, as seen from a Mac.
ICLOUD_SCRIPTABLE = os.path.expanduser(
    "~/Library/Mobile Documents/iCloud~dk~simonbs~Scriptable/Documents")

TOKEN = secrets.token_urlsafe(24)
MAX_BODY = 32 * 1024 * 1024
USER_AGENT = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
              "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Scriptable")
SECRET_NAMES = {"rh secrets", "rhsecrets"}


def scripts_dir():
    env = os.environ.get("LP_SCRIPTS_DIR")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    if os.path.isdir(ICLOUD_SCRIPTABLE):
        return ICLOUD_SCRIPTABLE
    return os.path.join(LOCAL, "scripts")


# Virtual roots the page's FileManager sees. FileManager.iCloud() is the
# Scriptable iCloud folder (so .env and the shared realised snapshot work the
# same as on the phone); FileManager.local() gets folders of its own under
# local/, which is git-ignored.
def roots():
    return {
        "icloud": scripts_dir(),
        "local": os.path.join(LOCAL, "files"),
    }


def resolve(vpath):
    """'/icloud/a/b.json' -> real path, refusing anything outside its root."""
    parts = [p for p in str(vpath).split("/") if p]
    if not parts or parts[0] not in roots():
        raise ValueError("unknown path: %s" % vpath)
    base = os.path.realpath(roots()[parts[0]])
    real = os.path.realpath(os.path.join(base, *parts[1:]))
    if real != base and not real.startswith(base + os.sep):
        raise ValueError("path escapes its folder: %s" % vpath)
    return real


def _fetch_from_icloud(path, wait=20.0):
    """Ask macOS to download a script iCloud has evicted to a placeholder.

    With "Optimise Mac Storage" on, iCloud Drive keeps only a stub named
    '.RH Pool.js.icloud' until something asks for the file.
    """
    d, f = os.path.split(path)
    stub = os.path.join(d, "." + f + ".icloud")
    if os.path.isfile(path) or not os.path.isfile(stub):
        return
    try:
        import subprocess
        subprocess.run(["brctl", "download", path], timeout=10,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:  # noqa: BLE001 - not on a Mac, or brctl missing
        return
    end = time.time() + wait
    while time.time() < end and not os.path.isfile(path):
        time.sleep(0.5)


def find_script(name):
    """A script by its Scriptable name ('RH Pool'), case-insensitive."""
    name = str(name or "").strip()
    if name.lower().endswith(".js"):
        name = name[:-3]
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None
    d = scripts_dir()
    _fetch_from_icloud(os.path.join(d, name + ".js"))
    for cand in (os.path.join(d, name + ".js"), os.path.join(LOCAL, "scripts", name + ".js")):
        if os.path.isfile(cand):
            return cand
    for base in (d, os.path.join(LOCAL, "scripts")):
        try:
            for f in os.listdir(base):
                if f.lower() == name.lower() + ".js":
                    return os.path.join(base, f)
        except OSError:
            pass
    return None


def list_scripts():
    seen, out = set(), []
    for base in (scripts_dir(), os.path.join(LOCAL, "scripts")):
        try:
            names = sorted(os.listdir(base))
        except OSError:
            continue
        for f in names:
            if f.startswith(".") and f.endswith(".js.icloud"):
                f = f[1:-len(".icloud")]   # evicted by iCloud; fetched on open
            if not f.endswith(".js") or f.startswith("."):
                continue
            n = f[:-3]
            if n.lower() in seen or n.lower() in SECRET_NAMES:
                continue
            seen.add(n.lower())
            out.append(n)
    return out


def forward(req):
    """Perform one HTTP request on the page's behalf."""
    url = str(req.get("url") or "")
    scheme = urllib.parse.urlsplit(url).scheme
    if scheme not in ("https", "http"):
        return {"error": "unsupported URL: %s" % url[:80]}
    method = str(req.get("method") or "GET").upper()
    headers = {}
    for k, v in (req.get("headers") or {}).items():
        if str(k).lower() in ("host", "content-length", "accept-encoding", "connection"):
            continue
        headers[str(k)] = str(v)
    if not any(k.lower() == "user-agent" for k in headers):
        headers["User-Agent"] = USER_AGENT
    body = req.get("body")
    data = body.encode("utf-8") if isinstance(body, str) else None
    timeout = float(req.get("timeout") or 60)
    timeout = max(1.0, min(timeout, 120.0))
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(r, timeout=timeout)
        status = resp.status
    except urllib.error.HTTPError as e:
        resp, status = e, e.code
    except (socket.timeout, TimeoutError):
        return {"error": "The request timed out.", "timeout": True}
    except urllib.error.URLError as e:
        if isinstance(e.reason, (socket.timeout, TimeoutError)):
            return {"error": "The request timed out.", "timeout": True}
        return {"error": "Could not connect to the server. (%s)" % e.reason}
    except Exception as e:  # noqa: BLE001 - report anything to the page
        return {"error": str(e)}
    try:
        raw = resp.read(MAX_BODY + 1)
    except (socket.timeout, TimeoutError):
        return {"error": "The request timed out.", "timeout": True}
    finally:
        try:
            resp.close()
        except Exception:  # noqa: BLE001
            pass
    if len(raw) > MAX_BODY:
        return {"error": "response too large"}
    charset = "utf-8"
    try:
        charset = resp.headers.get_content_charset() or "utf-8"
    except Exception:  # noqa: BLE001
        pass
    return {
        "status": status,
        "url": getattr(resp, "url", url),
        "headers": {k: v for k, v in resp.headers.items()},
        "body": raw.decode(charset, errors="replace"),
    }


def fs_op(op, args):
    if op == "read":
        p = resolve(args["path"])
        if not os.path.isfile(p):
            return {"exists": os.path.isdir(p), "dir": os.path.isdir(p), "content": None}
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            return {"exists": True, "dir": False, "content": f.read(),
                    "mtime": os.path.getmtime(p) * 1000, "size": os.path.getsize(p)}
    if op == "stat":
        p = resolve(args["path"])
        if not os.path.exists(p):
            return {"exists": False}
        return {"exists": True, "dir": os.path.isdir(p),
                "mtime": os.path.getmtime(p) * 1000, "size": os.path.getsize(p)}
    if op == "list":
        p = resolve(args["path"])
        return {"names": sorted(os.listdir(p)) if os.path.isdir(p) else []}
    if op == "write":
        p = resolve(args["path"])
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp-lpweb"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(args.get("content") or "")
        os.replace(tmp, p)
        return {"ok": True}
    if op == "remove":
        p = resolve(args["path"])
        if os.path.isdir(p):
            shutil.rmtree(p)
        elif os.path.exists(p):
            os.remove(p)
        return {"ok": True}
    if op == "mkdir":
        os.makedirs(resolve(args["path"]), exist_ok=True)
        return {"ok": True}
    if op in ("copy", "move"):
        src, dst = resolve(args["src"]), resolve(args["dst"])
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if op == "copy":
            (shutil.copytree if os.path.isdir(src) else shutil.copy2)(src, dst)
        else:
            shutil.move(src, dst)
        return {"ok": True}
    raise ValueError("unknown fs op: %s" % op)


class Handler(BaseHTTPRequestHandler):
    server_version = "LPWeb/1"
    port = 8787

    def log_message(self, fmt, *a):  # quieter than the default
        if os.environ.get("LP_VERBOSE"):
            sys.stderr.write("%s\n" % (fmt % a))

    # -- guards -------------------------------------------------------------
    def _host_ok(self):
        host = (self.headers.get("Host") or "").lower()
        return host in ("127.0.0.1:%d" % self.port, "localhost:%d" % self.port)

    def _api_ok(self):
        return self._host_ok() and secrets.compare_digest(
            self.headers.get("X-LP-Token") or "", TOKEN)

    # -- responses ----------------------------------------------------------
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _page(self, name):
        with open(os.path.join(STATIC, name), "r", encoding="utf-8") as f:
            html = f.read().replace("__LP_TOKEN__", TOKEN)
        self._send(200, html, "text/html; charset=utf-8")

    def _static(self, rel):
        p = os.path.realpath(os.path.join(STATIC, rel))
        if not p.startswith(os.path.realpath(STATIC) + os.sep) or not os.path.isfile(p):
            return self._send(404, {"error": "not found"})
        ctype = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
                 ".svg": "image/svg+xml", ".png": "image/png"}.get(os.path.splitext(p)[1],
                                                                   "application/octet-stream")
        with open(p, "rb") as f:
            self._send(200, f.read(), ctype)

    # -- routes -------------------------------------------------------------
    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"error": "open this page at http://127.0.0.1:%d" % self.port})
        u = urllib.parse.urlsplit(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        if u.path in ("/", "/index.html"):
            return self._page("index.html")
        if u.path.startswith("/run/"):
            return self._page("run.html")
        if u.path.startswith("/static/"):
            return self._static(u.path[len("/static/"):])
        if u.path == "/favicon.ico":
            return self._send(204, b"", "image/x-icon")
        if not u.path.startswith("/api/"):
            return self._send(404, {"error": "not found"})
        if not self._api_ok():
            return self._send(403, {"error": "forbidden"})
        try:
            if u.path == "/api/scripts":
                return self._send(200, {"dir": scripts_dir(), "scripts": list_scripts(),
                                        "hasSecrets": bool(find_script("RH Secrets") or find_script("RHSecrets"))})
            if u.path == "/api/script":
                p = find_script(q.get("name"))
                if not p:
                    return self._send(404, {"error": "no script named %r in %s" % (q.get("name"), scripts_dir())})
                with open(p, "r", encoding="utf-8") as f:
                    return self._send(200, {"name": os.path.basename(p)[:-3], "source": f.read()})
            if u.path == "/api/fs":
                return self._send(200, fs_op(q.get("op"), q))
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"error": str(e)})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._api_ok():
            return self._send(403, {"error": "forbidden"})
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            return self._send(413, {"error": "too large"})
        try:
            payload = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._send(400, {"error": "bad json"})
        u = urllib.parse.urlsplit(self.path)
        try:
            if u.path == "/api/proxy":
                return self._send(200, forward(payload))
            if u.path == "/api/fs":
                return self._send(200, fs_op(payload.get("op"), payload))
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"error": str(e)})
        return self._send(404, {"error": "not found"})


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=int(os.environ.get("LP_PORT", 8787)))
    ap.add_argument("--no-open", action="store_true", help="do not open the browser")
    a = ap.parse_args()

    os.makedirs(os.path.join(LOCAL, "files"), exist_ok=True)
    os.makedirs(os.path.join(LOCAL, "scripts"), exist_ok=True)
    # Another app (or an LP Web left running) may hold the port already, so
    # walk up to the next free one rather than stopping with a traceback.
    srv = None
    for port in range(a.port, a.port + 20):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    if srv is None:
        sys.exit("No free port between %d and %d. Try --port with another number."
                 % (a.port, a.port + 19))
    port = srv.server_address[1]
    Handler.port = port
    srv.daemon_threads = True
    url = "http://127.0.0.1:%d/" % port
    if port != a.port:
        print("Port %d is in use, using %d instead." % (a.port, port))
    print("LP Web running at %s" % url)
    print("Scripts folder: %s" % scripts_dir())
    found = list_scripts()
    print("Scripts found: %s" % (", ".join(found) if found else "none (see README)"))
    print("Press Ctrl+C to stop.")
    if not a.no_open:
        threading.Thread(target=lambda: (time.sleep(0.6), webbrowser.open(url)), daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
