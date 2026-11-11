"""Regenerate the reservations table relative to "today", so the data never goes stale.

    python seed_db.py                       # reseed restaurant.db from today's date
    python seed_db.py --csv reservations.csv  # also export the rows as CSV

Window: 7 days back (completed / no_show / cancelled) and 14 days ahead (confirmed / a few cancelled).
Two scenario days are planted relative to today (see scenario_days):
  DAY1 = first open day on/after today: exactly one table (T8) is free for a party of 4 at 19:30
  DAY2 = the next open day after DAY1:  every table seating 4+ is booked at 19:30 (alternatives needed)
All dates are DD-MM-YYYY. Menu, tables and opening hours are left untouched.
"""
import argparse
import csv
import os
import random
import sqlite3
from datetime import date, datetime, timedelta

DATE_FMT = "%d-%m-%Y"
SITTING = 90
HERE = os.path.dirname(os.path.abspath(__file__))
FIRST = ["Aarav", "Maya", "Liam", "Sofia", "Noah", "Priya", "Ethan", "Chloe", "Lucas", "Zoe", "Omar", "Hannah",
         "Diego", "Mei", "Jack", "Isla", "Ravi", "Emma", "Leo", "Nina"]
LAST = ["Patel", "Nguyen", "Smith", "Garcia", "Kim", "Rossi", "Brown", "Singh", "Lopez", "Chen", "Khan", "Miller",
        "Davis", "Costa", "Ali"]
HEADER = ["reservation_id", "table_id", "date", "start_time", "end_time", "party_size", "guest_name", "phone", "status"]


def _mins(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def _fmt(m):
    return f"{m // 60:02d}:{m % 60:02d}"


def load_hours(con):
    """{weekday: (open, close, kitchen_close)}, 0 = Monday."""
    return {r[0]: (r[1], r[2], r[3]) for r in con.execute("SELECT day_of_week, open, close, kitchen_close FROM opening_hours")}


def scenario_days(today, hours):
    day1 = today
    while hours[day1.weekday()][0] == "CLOSED":
        day1 += timedelta(days=1)
    day2 = day1 + timedelta(days=1)
    while hours[day2.weekday()][0] == "CLOSED":
        day2 += timedelta(days=1)
    return day1, day2


def seed(db_path, today=None, days_back=7, days_ahead=14, csv_path=None, rng_seed=7):
    today = today or date.today()
    rng = random.Random(rng_seed + today.toordinal())
    con = sqlite3.connect(db_path)
    hours = load_hours(con)
    tables = [(r[0], r[1]) for r in con.execute("SELECT table_id, seats FROM tables ORDER BY table_id")]
    cap = dict(tables)
    day1, day2 = scenario_days(today, hours)

    booked, res = {}, []

    def free(t, d, s):
        return all(s >= e or s + SITTING <= b for b, e in booked.get((t, d), []))

    def book(t, d, s, party, locked=False):
        booked.setdefault((t, d), []).append((s, s + SITTING))
        res.append({"table": t, "date": d, "start": s, "party": party, "locked": locked,
                    "name": f"{rng.choice(FIRST)} {rng.choice(LAST)}", "phone": f"+1-202-555-{rng.randint(100, 199):04d}"})

    # --- planted scenarios ---
    for t in ["T5", "T6", "T7", "T9", "T10"]:
        book(t, day1, _mins("19:00"), 4, locked=True)
    for t in ["T11", "T12", "T13"]:
        book(t, day1, _mins("19:30"), rng.randint(5, cap[t]), locked=True)
    if _mins("21:00") <= _mins(hours[day1.weekday()][2]):
        book("T8", day1, _mins("21:00"), 4, locked=True)
    for t in ["T5", "T6", "T7", "T8", "T9", "T10", "T11", "T12", "T13"]:
        book(t, day2, _mins("19:00"), rng.randint(3, cap[t]), locked=True)

    # --- background bookings ---
    for off in range(-days_back, days_ahead + 1):
        d = today + timedelta(days=off)
        o, _close, k = hours[d.weekday()]
        if o == "CLOSED":
            continue
        base = 0.55 if d.weekday() >= 4 else 0.35
        for s in range(_mins(o), _mins(k) + 1, 30):
            peak = 1.6 if 18 * 60 + 30 <= s <= 20 * 60 else 1.0
            for t, seats in tables:
                if d == day1 and t == "T8":
                    continue  # keep T8 clear around 19:30 on DAY1
                if rng.random() < base * peak * 0.45 and free(t, d, s):
                    book(t, d, s, rng.randint(max(1, seats - 2), seats))

    # --- statuses ---
    for r in res:
        if r["date"] < today:
            x = rng.random()
            r["status"] = "completed" if x < 0.92 else "no_show" if x < 0.96 else "cancelled"
        else:
            r["status"] = "confirmed"
    future_free = [r for r in res if r["date"] >= today and not r["locked"]]
    for r in rng.sample(future_free, min(12, len(future_free))):
        r["status"] = "cancelled"

    res.sort(key=lambda r: (r["date"], r["start"], r["table"]))
    rows = [[f"RES{i:04d}", r["table"], r["date"].strftime(DATE_FMT), _fmt(r["start"]), _fmt(r["start"] + SITTING),
             r["party"], r["name"], r["phone"], r["status"]] for i, r in enumerate(res, 1)]

    con.execute("DELETE FROM reservations")
    con.executemany("INSERT INTO reservations VALUES (?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    if csv_path:
        with open(csv_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(HEADER)
            w.writerows(rows)
    return {"rows": len(rows), "day1": day1, "day2": day2}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(HERE, "restaurant.db"))
    ap.add_argument("--today", help="DD-MM-YYYY (default: real today)")
    ap.add_argument("--csv", help="also write reservations to this CSV path")
    a = ap.parse_args()
    today = datetime.strptime(a.today, DATE_FMT).date() if a.today else None
    info = seed(a.db, today=today, csv_path=a.csv)
    print(f"Seeded {info['rows']} reservations.")
    print(f"DAY1 (one table free for 4 at 19:30): {info['day1'].strftime('%A %d-%m-%Y')}")
    print(f"DAY2 (fully booked for 4 at 19:30):   {info['day2'].strftime('%A %d-%m-%Y')}")
