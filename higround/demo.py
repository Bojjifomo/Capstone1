"""Fake club for `server.py --demo`: made-up runners with two years of made-up runs.

Deterministic (fixed seed), written once into demo.db. Nothing here is real data.
"""
import random
from datetime import date, timedelta

import intervals

RUNNERS = [
    # name, gender, runs/week, base km, pace s/km
    ("Demo Admin", "M", 4, 8, 330),
    ("Bima Arya", "M", 5, 11, 300),
    ("Citra Lestari", "F", 4, 9, 345),
    ("Dimas Pratama", "M", 3, 7, 360),
    ("Eka Saputri", "F", 5, 10, 320),
    ("Fajar Nugroho", "M", 6, 13, 285),
    ("Gita Maharani", "F", 3, 6, 390),
    ("Hendra Wijaya", "M", 2, 6, 400),
    ("Intan Permata", "F", 4, 8, 355),
    ("Joko Susilo", "M", 3, 10, 335),
    ("Kirana Putri", "F", 2, 5, 420),
    ("Lukman Hakim", "M", 4, 12, 310),
    ("Maya Anggraini", "F", 3, 7, 375),
    ("Nanda Kusuma", "M", 5, 9, 325),
    ("Oktavia Rahma", "F", 2, 5, 430),
    ("Putra Ramadhan", "M", 1, 5, 410),
    ("Rizky Firmansyah", "M", 0, 0, 0),
    ("Salsa Nabila", "F", 0, 0, 0),
    ("Teguh Santoso", "M", 3, 8, 350),
    ("Vina Oktaviani", "", 3, 7, 365),
]
DEVICES = ["Garmin Forerunner 265", "COROS PACE 3", "Garmin Forerunner 965", "Polar Pacer Pro",
           "COROS APEX 2", "Suunto Race", "Garmin fēnix 7"]
SOURCES = {"Garmin": "GARMIN_CONNECT", "COROS": "COROS", "Polar": "POLAR", "Suunto": "SUUNTO"}
NAMES = ["Morning Run", "Easy Run", "Long Run", "Tempo", "Interval GBK", "Recovery", "Sunday Long Run",
         "Lari Pagi", "Night Run", "Progression Run"]


def seed(club, days=730, today=None):
    if club.members():
        return
    rnd = random.Random(20261123)
    today = today or date.today()
    for i, (name, gender, per_week, base, pace) in enumerate(RUNNERS):
        mid = club.upsert_member("demo%d" % i, name, "demo-token", "ACTIVITY:READ", admin=(i == 0))
        club.update_profile(mid, {"gender": gender})
        if not per_week:
            continue
        device = DEVICES[i % len(DEVICES)]
        source = next(v for k, v in SOURCES.items() if device.startswith(k))
        joined_ago = rnd.randint(120, days)
        rows = []
        for back in range(joined_ago, -1, -1):
            d = today - timedelta(days=back)
            fitness = 1 + 0.25 * (1 - back / days)                 # everyone gets a bit fitter
            if rnd.random() > per_week / 7 * (0.85 if d.month in (1, 2) else 1):
                continue
            long_run = d.weekday() == 6 and rnd.random() < 0.7
            km = max(2.5, rnd.gauss(base * 0.75 * (1.8 if long_run else 1) * fitness, base * 0.2))
            p = max(200, rnd.gauss(pace / fitness ** 0.3 + (25 if long_run else 0), 18))
            hour = 5 if rnd.random() < 0.7 else 18
            raw = {
                "id": "demo%d-%s" % (i, d.isoformat()),
                "start_date_local": "%sT%02d:%02d:00" % (d.isoformat(), hour, rnd.randint(0, 59)),
                "type": "TrailRun" if rnd.random() < 0.05 else "Run",
                "name": "Sunday Long Run" if long_run else rnd.choice(NAMES),
                "distance": km * 1000,
                "moving_time": km * p,
                "elapsed_time": km * p * 1.06,
                "total_elevation_gain": rnd.uniform(5, 25) * km / 5,
                "average_heartrate": rnd.randint(138, 162),
                "source": source,
                "device_name": device,
            }
            rows.append(intervals.normalize(raw))
        if i in (5, 11):   # a couple of suspicious ones for the admin queue
            d = today - timedelta(days=2 + i % 3)
            rows.append(intervals.normalize({
                "id": "demo%d-sus" % i, "start_date_local": d.isoformat() + "T07:10:00", "type": "Run",
                "name": "Lari (kayaknya naik motor)", "distance": 21000, "moving_time": 21 * 140,
                "elapsed_time": 21 * 150, "source": source, "device_name": device}))
        if i == 0:
            d = today - timedelta(days=1)
            rows.append(intervals.normalize({
                "id": "demo0-strava", "start_date_local": d.isoformat() + "T06:00:00", "type": "Run",
                "source": "STRAVA"}))
        club.save_activities(mid, [r for r in rows if r])
        club.sync_done(mid)
