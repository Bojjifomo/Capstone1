"""HiGround: the numbers. Periods, leaderboard, XP/level and a runner's dashboard.

Pure functions over rows from store.Club, so they are easy to test. Dates are the runner's
local dates as intervals.icu reports them (the club runs in WIB, so that's what members see).
"""
from datetime import date, datetime, timedelta

PERIODS = ("week", "month", "all")
METRICS = ("distance", "days", "elevation")
MONTHS_ID = ("Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus",
             "September", "Oktober", "November", "Desember")

# XP rewards showing up, not only distance (so nobody is pushed into one long, risky run).
XP_PER_ACTIVE_DAY = 30
XP_PER_KM = 3
XP_STRONG_WEEK = 100       # a week with at least 3 run days
STRONG_WEEK_DAYS = 3


def today():
    return date.today()


def period_range(period, ref=None):
    """-> (start, end, label) as ISO dates, end exclusive. 'all' -> (None, None, label)."""
    ref = ref or today()
    if period == "week":
        start = ref - timedelta(days=ref.weekday())
        end = start + timedelta(days=7)
        return start.isoformat(), end.isoformat(), "%d %s – %d %s" % (
            start.day, MONTHS_ID[start.month - 1][:3], (end - timedelta(days=1)).day,
            MONTHS_ID[(end - timedelta(days=1)).month - 1][:3])
    if period == "month":
        start = ref.replace(day=1)
        end = (start + timedelta(days=32)).replace(day=1)
        return start.isoformat(), end.isoformat(), "%s %d" % (MONTHS_ID[start.month - 1], start.year)
    return None, None, "Sepanjang waktu"


def in_range(day, start, end):
    return (start is None or day >= start) and (end is None or day < end)


def totals(acts):
    km = sum(a["distance_m"] for a in acts) / 1000
    secs = sum(a["moving_s"] or a["elapsed_s"] for a in acts)
    days = len({a["day"] for a in acts})
    return {
        "km": round(km, 1),
        "runs": len(acts),
        "days": days,
        "hours": round(secs / 3600, 1),
        "elevation": round(sum(a["elev_m"] for a in acts)),
        "pace": pace_of(sum(a["distance_m"] for a in acts), secs),
    }


def pace_of(meters, seconds):
    """Seconds per km, or None."""
    if not meters or not seconds:
        return None
    return round(seconds / (meters / 1000))


def xp(acts):
    """-> (xp, level, xp_into_level, xp_for_next) over a member's counted runs."""
    days = {a["day"] for a in acts}
    km = sum(a["distance_m"] for a in acts) / 1000
    weeks = {}
    for d in days:
        y, w, _ = date.fromisoformat(d).isocalendar()
        weeks[(y, w)] = weeks.get((y, w), 0) + 1
    strong = sum(1 for n in weeks.values() if n >= STRONG_WEEK_DAYS)
    total = int(len(days) * XP_PER_ACTIVE_DAY + km * XP_PER_KM + strong * XP_STRONG_WEEK)
    level, need, left = 1, 200, total
    while left >= need:          # each level asks 15% more than the last
        left -= need
        level += 1
        need = int(need * 1.15)
    return {"xp": total, "level": level, "into": left, "next": need}


def leaderboard(members, acts, period="month", gender="all", metric="distance", ref=None):
    """members: dicts from store; acts: counted activities (any period). Hides members with
    nothing in the period and reports how many sat it out."""
    start, end, label = period_range(period, ref)
    by_member = {}
    for a in acts:
        by_member.setdefault(a["member_id"], []).append(a)
    rows, idle = [], 0
    for m in members:
        if not m.get("public", 1):
            continue
        if gender in ("F", "M") and m.get("gender") != gender:
            continue
        mine = by_member.get(m["id"], [])
        inside = [a for a in mine if in_range(a["day"], start, end)]
        if not inside:
            idle += 1
            continue
        t = totals(inside)
        lv = xp(mine)
        rows.append({"member_id": m["id"], "name": m["name"], "gender": m.get("gender") or "",
                     "level": lv["level"], "xp": lv["xp"], **t})
    key = {"distance": lambda r: (r["km"], r["days"]),
           "days": lambda r: (r["days"], r["km"]),
           "elevation": lambda r: (r["elevation"], r["km"])}[metric if metric in METRICS else "distance"]
    rows.sort(key=key, reverse=True)
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    club = totals([a for a in acts if in_range(a["day"], start, end)
                   and any(m["id"] == a["member_id"] for m in members)])
    return {"period": period, "label": label, "start": start, "end": end, "gender": gender,
            "metric": metric, "rows": rows, "idle": idle, "club": club}


def week_start(d):
    return d - timedelta(days=d.weekday())


def dashboard(member, acts, all_counted=None, period="month", ref=None, weeks=16):
    """A member's own page. acts: this member's activities (all statuses)."""
    ref = ref or today()
    counted = [a for a in acts if a["status"] in ("ok", "approved")]
    start, end, label = period_range(period, ref)
    now_t = totals([a for a in counted if in_range(a["day"], start, end)])

    # last N weeks, oldest first: km per week and average pace
    first = week_start(ref) - timedelta(weeks=weeks - 1)
    series = []
    for i in range(weeks):
        ws = first + timedelta(weeks=i)
        we = ws + timedelta(days=7)
        wk = [a for a in counted if ws.isoformat() <= a["day"] < we.isoformat()]
        dist = sum(a["distance_m"] for a in wk)
        secs = sum(a["moving_s"] or a["elapsed_s"] for a in wk)
        series.append({"week": ws.isoformat(), "km": round(dist / 1000, 1), "runs": len(wk),
                       "pace": pace_of(dist, secs)})

    # calendar: km per day over the same window
    cal = {}
    for a in counted:
        if a["day"] >= first.isoformat():
            cal[a["day"]] = round(cal.get(a["day"], 0) + a["distance_m"] / 1000, 1)

    # load: last 7 days vs the average week of the last 28 (acute:chronic)
    d7 = (ref - timedelta(days=6)).isoformat()
    d28 = (ref - timedelta(days=27)).isoformat()
    acute = sum(a["distance_m"] for a in counted if a["day"] >= d7) / 1000
    chronic = sum(a["distance_m"] for a in counted if a["day"] >= d28) / 1000 / 4
    ratio = round(acute / chronic, 2) if chronic >= 5 else None
    if ratio is None:
        load = {"state": "unknown", "text": "Butuh beberapa minggu data dulu buat baca beban latihan."}
    elif ratio > 1.5:
        load = {"state": "high", "text": "Volume minggu ini %.0f%% di atas rata-rata lu. Rawan cedera, "
                                         "selipin hari easy atau rest." % ((ratio - 1) * 100)}
    elif ratio < 0.8:
        load = {"state": "low", "text": "Minggu ini lebih santai dari biasanya. Pas buat recovery, "
                                        "atau saatnya naik lagi pelan-pelan."}
    else:
        load = {"state": "good", "text": "Beban latihan stabil. Naikin volume maksimal ±10% per minggu."}

    # streak: consecutive weeks (ending this or last week) with at least one run
    run_weeks = {week_start(date.fromisoformat(a["day"])) for a in counted}
    streak, w = 0, week_start(ref)
    if w not in run_weeks:
        w -= timedelta(weeks=1)
    while w in run_weeks:
        streak += 1
        w -= timedelta(weeks=1)

    # bests
    longest = max(counted, key=lambda a: a["distance_m"], default=None)
    five = [a for a in counted if a["distance_m"] >= 5000 and (a["moving_s"] or a["elapsed_s"])]
    fastest = min(five, key=lambda a: (a["moving_s"] or a["elapsed_s"]) / a["distance_m"], default=None)

    rank = None
    if all_counted is not None and member.get("public", 1):
        mstart, mend, _ = period_range("month", ref)
        per = {}
        for a in all_counted:
            if in_range(a["day"], mstart, mend):
                per[a["member_id"]] = per.get(a["member_id"], 0) + a["distance_m"]
        if member["id"] in per:
            order = sorted(per.values(), reverse=True)
            rank = {"rank": order.index(per[member["id"]]) + 1, "of": len(order)}

    def brief(a):
        if not a:
            return None
        return {"id": a["id"], "day": a["day"], "km": round(a["distance_m"] / 1000, 2),
                "pace": pace_of(a["distance_m"], a["moving_s"] or a["elapsed_s"]), "name": a.get("name")}

    recent = [{
        "id": a["id"], "start": a["start_local"], "day": a["day"], "name": a.get("name"), "type": a["type"],
        "km": round(a["distance_m"] / 1000, 2), "secs": a["moving_s"] or a["elapsed_s"],
        "pace": pace_of(a["distance_m"], a["moving_s"] or a["elapsed_s"]), "elev": round(a["elev_m"]),
        "hr": round(a["avg_hr"]) if a.get("avg_hr") else None, "device": a.get("device"),
        "status": a["status"], "reason": a.get("reason"),
    } for a in sorted(acts, key=lambda a: a["start_local"], reverse=True)[:25]]

    return {
        "period": period, "label": label, "totals": now_t, "weeks": series, "calendar": cal,
        "calendar_start": first.isoformat(), "load": {"acute": round(acute, 1), "chronic": round(chronic, 1),
                                                       "ratio": ratio, **load},
        "streak": streak, "longest": brief(longest), "fastest5k": brief(fastest),
        "level": xp(counted), "rank": rank, "recent": recent,
        "all_time": totals(counted),
    }


def fmt_pace(sec):
    return "%d:%02d" % (sec // 60, sec % 60) if sec else "–"


def parse_day(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None
