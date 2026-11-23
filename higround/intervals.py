"""intervals.icu: OAuth login, listing a member's runs, and the club's rules for what counts.

Standard library only (urllib). intervals.icu syncs the member's watch (Garmin, COROS, Polar,
Suunto, Wahoo, ...); we read their activities with the token they granted us (scope ACTIVITY:READ).
Runs that reached intervals.icu from Strava are not readable over the API (Strava's terms), so
those are skipped with a note telling the member to connect their watch directly.
"""
import base64
import hashlib
import json
import secrets
import urllib.error
import urllib.parse
import urllib.request

AUTHORIZE_URL = "https://intervals.icu/oauth/authorize"
TOKEN_URL = "https://intervals.icu/api/oauth/token"
API = "https://intervals.icu/api/v1"
SCOPE = "ACTIVITY:READ"
# Cloudflare in front of intervals.icu blocks some library user agents.
UA = "Mozilla/5.0 (compatible; HiGroundRunClub/1.0)"

RUN_TYPES = {"Run", "TrailRun", "VirtualRun"}
MIN_DISTANCE_M = 500           # shorter than this is a GPS blip / warm-up, not counted
FASTEST_PACE_S_PER_KM = 170    # faster than 2:50/km on average: flag for review
MAX_DISTANCE_M = 120_000       # longer than 120 km in one activity: flag for review
SUSPECT_SOURCES = {"MANUAL"}   # typed in by hand: flag for review


class AuthError(Exception):
    """The member's token no longer works (revoked or expired): they must connect again."""


class ApiError(Exception):
    pass


def pkce_pair():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorize_url(client_id, redirect_uri, state, challenge=None):
    q = {"client_id": client_id, "redirect_uri": redirect_uri, "scope": SCOPE, "state": state}
    if challenge:
        q.update(code_challenge=challenge, code_challenge_method="S256")
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode(q)


def _request(url, data=None, token=None, method=None, timeout=30):
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        detail = e.read()[:300].decode("utf-8", "replace")
        if e.code in (401, 403):
            raise AuthError("intervals.icu menolak token (HTTP %d)" % e.code) from None
        raise ApiError("intervals.icu HTTP %d: %s" % (e.code, detail)) from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ApiError("intervals.icu nggak bisa dihubungi: %s" % e) from None
    return json.loads(raw) if raw else None


def exchange_code(client_id, client_secret, code, verifier=None):
    """-> {"access_token", "scope", "athlete": {"id", "name"}}"""
    data = {"client_id": client_id, "client_secret": client_secret, "code": code}
    if verifier:
        data["code_verifier"] = verifier
    out = _request(TOKEN_URL, data=data)
    if not isinstance(out, dict) or not out.get("access_token"):
        raise ApiError("Jawaban token intervals.icu nggak lengkap")
    return out


def list_activities(token, oldest, newest=None):
    """Raw activities between two local dates (inclusive), newest first."""
    q = {"oldest": oldest}
    if newest:
        q["newest"] = newest + "T23:59:59"
    out = _request(API + "/athlete/0/activities?" + urllib.parse.urlencode(q), token=token)
    return out if isinstance(out, list) else []


def disconnect(token):
    """Revoke our access on intervals.icu (best effort)."""
    try:
        _request(API + "/disconnect-app", token=token, method="DELETE")
    except (AuthError, ApiError):
        pass


def _num(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def normalize(raw):
    """One intervals.icu activity -> our row with status/reason, or None if it isn't a run at all."""
    if not isinstance(raw, dict) or raw.get("id") is None:
        return None
    source = (raw.get("source") or "").upper()
    typ = raw.get("type") or ""
    start = raw.get("start_date_local") or ""
    dist = _num(raw.get("distance"))
    if source == "STRAVA" and not dist:
        # Strava's terms keep these out of the API: a stub without numbers.
        if not start:
            return None
        return {"id": str(raw["id"]), "start_local": start[:19], "type": typ or "Run", "name": None,
                "distance_m": 0, "moving_s": 0, "elapsed_s": 0, "elev_m": 0, "source": source,
                "device": None, "status": "skipped",
                "reason": "Dari Strava: nggak bisa dibaca. Connect jam lu langsung ke intervals.icu"}
    if typ not in RUN_TYPES or len(start) < 10:
        return None
    row = {
        "id": str(raw["id"]),
        "start_local": start[:19],
        "type": typ,
        "name": (raw.get("name") or "")[:120] or None,
        "distance_m": dist or 0.0,
        "moving_s": _num(raw.get("moving_time")) or 0.0,
        "elapsed_s": _num(raw.get("elapsed_time")) or 0.0,
        "elev_m": _num(raw.get("total_elevation_gain")) or 0.0,
        "avg_hr": _num(raw.get("average_heartrate")),
        "max_hr": _num(raw.get("max_heartrate")),
        "source": source or None,
        "device": (raw.get("device_name") or "")[:80] or None,
    }
    row["status"], row["reason"] = judge(row)
    return row


def judge(a):
    """The club's anti-cheat rules -> (status, reason). Flagged runs wait for an admin."""
    d, t = a["distance_m"], a["moving_s"] or a["elapsed_s"]
    if d < MIN_DISTANCE_M:
        return "skipped", "Kurang dari %d m" % MIN_DISTANCE_M
    if (a.get("source") or "") in SUSPECT_SOURCES:
        return "flagged", "Diinput manual"
    if not t:
        return "flagged", "Nggak ada durasi"
    pace = t / (d / 1000)
    if pace < FASTEST_PACE_S_PER_KM:
        return "flagged", "Pace rata-rata %d:%02d/km, terlalu cepat" % (pace // 60, pace % 60)
    if d > MAX_DISTANCE_M:
        return "flagged", "Lebih dari %d km sekali lari" % (MAX_DISTANCE_M // 1000)
    return "ok", None


def is_garmin(device):
    """Garmin's terms ask for attribution wherever Garmin-sourced data is shown."""
    return "garmin" in (device or "").lower()
