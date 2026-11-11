"""Run:  python test_tools.py
Works on a temp copy of the DB, reseeded relative to today's date, with the clock pinned to noon today.
So it passes on any day and your restaurant.db is untouched."""
import os
import re
import shutil
import sqlite3
import tempfile
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
tmp = os.path.join(tempfile.mkdtemp(), "restaurant.db")
shutil.copy(os.path.join(HERE, "restaurant.db"), tmp)
os.environ["DB_PATH"] = tmp

import seed_db  # noqa: E402
import tools as t  # noqa: E402

TODAY = date.today()
seed_db.seed(tmp, today=TODAY)
t._now = lambda: datetime.combine(TODAY, datetime.min.time()).replace(hour=12)

_hours = seed_db.load_hours(sqlite3.connect(tmp))
D1, D2 = seed_db.scenario_days(TODAY, _hours)
f = lambda d: d.strftime("%d-%m-%Y")  # noqa: E731


def test_happy_path():
    r = t.check_availability(f(D1), "19:30", 4)
    assert r["status"] == "available" and r["table_id"] == "T8", r
    b = t.create_reservation(f(D1), "19:30", 4, "Arshia", "+91 98765 43210")
    assert b["status"] == "confirmed" and b["date"] == f(D1), b
    assert t.check_availability(f(D1), "19:30", 4)["status"] == "unavailable"   # no double booking


def test_unavailable_gives_alternatives():
    r = t.check_availability(f(D2), "19:30", 4)
    assert r["status"] == "unavailable" and r["alternative_times"], r


def test_date_format_and_normalising():
    assert t.check_availability(D1.strftime("%Y-%m-%d"), "19:30", 4)["status"] == "invalid_input"  # ISO rejected
    assert t.check_availability("bad", "19:30", 4)["status"] == "invalid_input"
    short = f"{D2.day}-{D2.month}-{D2.year}"                       # e.g. 6-11-2026
    r = t.check_availability(short, "9:00", 2)
    assert r.get("date", f(D2)) == f(D2), r


def test_hours_closed_past_and_capacity():
    monday = TODAY + timedelta(days=1)
    while monday.weekday() != 0:
        monday += timedelta(days=1)
    assert t.check_availability(f(monday), "19:00", 2)["status"] == "closed"
    assert t.check_availability(f(D1), "22:45", 2)["status"] == "outside_hours"
    assert t.check_availability(f(D1), "10:00", 2)["status"] == "outside_hours"
    assert t.check_availability(f(D2), "19:00", 12)["status"] == "party_too_large"
    assert t.check_availability(f(TODAY - timedelta(days=1)), "19:00", 2)["status"] == "in_the_past"


def test_allergen_verified():
    item = t.search_menu_allergens("Spaghetti al Pesto", "nuts")["items"][0]
    assert item["verified"] and "tree_nuts" in item["contains"], item


def test_allergen_ambiguous_pesto_reports_both():
    r = t.search_menu_allergens("pesto", "nuts")
    assert r["status"] == "multiple_matches"
    by = {i["item"]: i for i in r["items"]}
    assert by["Spaghetti al Pesto"]["verified"] is True
    assert by["Seasonal Pesto Gnocchi"]["verified"] is False


def test_guardrails():
    assert t.search_menu_allergens("Chef's Daily Special", "nuts")["items"][0]["verified"] is False
    assert t.search_menu_allergens("truffle risotto xyz", "nuts")["status"] == "not_found"
    assert t.search_menu_allergens("Tiramisu", "kryptonite")["status"] == "unknown_allergen"


def test_cross_contact():
    r = t.search_menu_allergens("Fettuccine Alfredo", "tree nuts")["items"][0]
    assert "tree_nuts" in r["may_contain_cross_contact"] and not r["contains"], r


def test_safe_list_excludes_unverified_and_cross_contact():
    r = t.search_menu_allergens(allergen="peanuts")
    assert "Chicken Satay Skewers" not in r["verified_free"]
    assert "Seasonal Pesto Gnocchi" in r["not_verified_do_not_recommend"]
    n = t.search_menu_allergens(allergen="nuts")
    assert "Spaghetti al Pesto" not in n["verified_free"] and "Fettuccine Alfredo" not in n["verified_free"]


def test_cancel_needs_matching_phone():
    o, _c, k = _hours[D2.weekday()]
    b = None
    for s in range(seed_db._mins(o), seed_db._mins(k) + 1, 30):   # first free slot for 1 person
        b = t.create_reservation(f(D2), seed_db._fmt(s), 1, "Test Caller", "202-555-0123")
        if b["status"] == "confirmed":
            break
    assert b["status"] == "confirmed", b
    assert t.lookup_reservation("Test Caller", f(D2))["status"] == "found"
    assert t.cancel_reservation(b["reservation_id"], "000-000-0000")["status"] == "verification_failed"
    assert t.cancel_reservation(b["reservation_id"], "(202) 555-0123")["status"] == "cancelled"
    assert t.lookup_reservation("Test Caller", f(D2))["status"] == "not_found"


def test_search_menu():
    r = t.search_menu(category="desserts")
    assert r["status"] == "ok" and {i["category"] for i in r["items"]} == {"dessert"}, r
    assert all("ingredients" not in i and "allergens" not in i for i in r["items"])   # no allergen leakage
    cheap = t.search_menu(max_price=10)
    assert cheap["items"] and all(i["price_usd"] <= 10 for i in cheap["items"])
    assert any(i["name"] == "Tiramisu" for i in t.search_menu(name="tiramisu")["items"])
    assert t.search_menu(name="sushi platter")["status"] == "not_found"
    assert t.search_menu(category="pizzas")["status"] == "invalid_input"


def test_propose_books_nothing():
    n = lambda: sqlite3.connect(tmp).execute("SELECT COUNT(*) FROM reservations").fetchone()[0]  # noqa: E731
    before = n()
    r = t.propose_booking(f(D2), "17:30", 1, "Proposer", "202-555-0199")
    assert r["status"] == "proposed" and "NOTHING IS BOOKED" in r["instruction"], r
    assert n() == before
    assert t.propose_booking(f(D2), "17:30", 1, "", "202-555-0199")["status"] == "invalid_input"
    assert t.propose_booking(f(D2), "19:30", 4, "X Y", "202-555-0199")["status"] == "unavailable"


def test_handoff_logged():
    assert t.handoff_to_human("unverified allergen", "seasonal pesto")["status"] == "handoff_logged"


def test_seed_is_relative_and_dd_mm_yyyy():
    con = sqlite3.connect(tmp)
    dates = [r[0] for r in con.execute("SELECT DISTINCT date FROM reservations")]
    assert all(re.fullmatch(r"\d{2}-\d{2}-\d{4}", x) for x in dates), dates[:3]
    parsed = [datetime.strptime(x, "%d-%m-%Y").date() for x in dates]
    assert min(parsed) >= TODAY - timedelta(days=7) and max(parsed) <= TODAY + timedelta(days=14)
    # reseeding a far-future "today" must still plant both scenarios
    other = os.path.join(tempfile.mkdtemp(), "x.db")
    shutil.copy(os.path.join(HERE, "restaurant.db"), other)
    far = date(2031, 3, 15)
    seed_db.seed(other, today=far)
    c2 = sqlite3.connect(other)
    d1, _d2 = seed_db.scenario_days(far, seed_db.load_hours(c2))
    n = c2.execute("SELECT COUNT(*) FROM reservations WHERE date=? AND status='confirmed'", (f(d1),)).fetchone()[0]
    assert n >= 8, n


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print("PASS", name)
            except AssertionError as e:
                fails += 1
                print("FAIL", name, e)
    raise SystemExit(fails)