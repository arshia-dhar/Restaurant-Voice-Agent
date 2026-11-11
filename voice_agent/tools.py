"""Core tools for the restaurant voice agent. Pure Python + sqlite3 (no web framework needed).

Every tool returns a plain dict with a "status" field so the LLM can branch on it.
"""
import difflib
import os
import sqlite3
from datetime import datetime, timedelta

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "restaurant.db"))
SITTING_MIN = 90      # how long a reservation blocks a table
MAX_PARTY = 8         # largest table in the restaurant
SLOT_STEP = 30        # minutes between offered alternative times
DATE_FMT = "%d-%m-%Y"  # every date in and out of the tools and DB is DD-MM-YYYY


def _now():
    """Restaurant-local 'now'. Separate function so tests can pin the clock."""
    return datetime.now()

ALLERGENS = ["tree_nuts", "peanuts", "dairy", "gluten", "eggs", "shellfish", "fish", "soy", "sesame"]
# Callers say things like "nuts" - for safety, ambiguous words map to ALL plausible allergens.
ALLERGEN_ALIASES = {
    "nuts": ["tree_nuts", "peanuts"], "nut": ["tree_nuts", "peanuts"],
    "tree nut": ["tree_nuts"], "tree nuts": ["tree_nuts"], "tree_nuts": ["tree_nuts"],
    "peanut": ["peanuts"], "peanuts": ["peanuts"],
    "dairy": ["dairy"], "milk": ["dairy"], "lactose": ["dairy"],
    "gluten": ["gluten"], "wheat": ["gluten"],
    "egg": ["eggs"], "eggs": ["eggs"],
    "shellfish": ["shellfish"], "shrimp": ["shellfish"], "prawn": ["shellfish"],
    "fish": ["fish"], "seafood": ["fish", "shellfish"],
    "soy": ["soy"], "soya": ["soy"], "sesame": ["sesame"],
}


def _conn():
    con = sqlite3.connect(DB_PATH, isolation_level=None)  # we manage transactions explicitly
    con.row_factory = sqlite3.Row
    return con


def _fmt(m):
    return f"{m // 60:02d}:{m % 60:02d}"


def _mins(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def _parse(date, time):
    try:
        d = datetime.strptime(date, DATE_FMT).date()
        t = datetime.strptime(time, "%H:%M")
    except (ValueError, TypeError):
        return None, None
    return d, t.hour * 60 + t.minute


def _hours(con, d):
    return con.execute("SELECT * FROM opening_hours WHERE day_of_week=?", (d.weekday(),)).fetchone()


def _free_tables(con, date, start, party):
    """Smallest-first list of tables that fit `party` and have no confirmed booking overlapping the sitting."""
    end = _fmt(start + SITTING_MIN)
    return [r["table_id"] for r in con.execute(
        """SELECT table_id FROM tables WHERE seats >= ? AND table_id NOT IN (
               SELECT table_id FROM reservations
               WHERE date=? AND status='confirmed' AND start_time < ? AND end_time > ?)
           ORDER BY seats, table_id""",
        (party, date, end, _fmt(start)))]


def _next_open_day(con, d):
    for i in range(1, 8):
        nd = d + timedelta(days=i)
        h = _hours(con, nd)
        if h["open"] != "CLOSED":
            return nd.strftime(DATE_FMT), h["open"], h["kitchen_close"]
    return None, None, None


def _check(con, date, time, party_size):
    """Shared by check_availability and create_reservation. Returns (result_dict, table_or_None)."""
    d, start = _parse(date, time)
    if d is None:
        return {"status": "invalid_input", "message": "Date must be DD-MM-YYYY and time HH:MM (24h)."}, None
    date, time = d.strftime(DATE_FMT), _fmt(start)  # normalise e.g. 5-11-2026 -> 05-11-2026, 7:30 -> 07:30
    if not isinstance(party_size, int) or party_size < 1:
        return {"status": "invalid_input", "message": "Party size must be a positive whole number."}, None
    if datetime.combine(d, datetime.min.time()) + timedelta(minutes=start) < _now():
        return {"status": "in_the_past", "message": "That date and time has already passed."}, None
    if party_size > MAX_PARTY:
        return {"status": "party_too_large", "max_party": MAX_PARTY, "handoff_recommended": True,
                "message": f"Largest table seats {MAX_PARTY}. Large parties need a staff member."}, None
    h = _hours(con, d)
    if h["open"] == "CLOSED":
        nd, o, k = _next_open_day(con, d)
        return {"status": "closed", "date": date, "next_open_date": nd, "opens": o,
                "message": "The restaurant is closed that day."}, None
    if start < _mins(h["open"]) or start > _mins(h["kitchen_close"]):
        return {"status": "outside_hours", "opens": h["open"], "last_seating": h["kitchen_close"],
                "message": "That time is outside seating hours."}, None
    free = _free_tables(con, date, start, party_size)
    if free:
        return {"status": "available", "date": date, "time": time, "party_size": party_size,
                "table_id": free[0]}, free[0]
    # nothing free: offer nearest alternatives the same day
    alts = []
    for off in sorted(range(-120, 121, SLOT_STEP), key=abs):
        if off == 0:
            continue
        s = start + off
        if _mins(h["open"]) <= s <= _mins(h["kitchen_close"]) and _free_tables(con, date, s, party_size):
            alts.append(_fmt(s))
        if len(alts) == 3:
            break
    return {"status": "unavailable", "date": date, "time": time, "party_size": party_size,
            "alternative_times": sorted(alts),
            "message": "No table at that time." + ("" if alts else " Nothing nearby that day; try another date.")}, None


def check_availability(date, time, party_size):
    con = _conn()
    try:
        result, _ = _check(con, date, time, party_size)
        return result
    finally:
        con.close()


def create_reservation(date, time, party_size, guest_name, phone):
    guest_name = (guest_name or "").strip()
    digits = "".join(c for c in str(phone or "") if c.isdigit())
    if not guest_name or len(digits) < 7:
        return {"status": "invalid_input", "message": "Need the guest's name and a phone number (at least 7 digits)."}
    con = _conn()
    try:
        con.execute("BEGIN IMMEDIATE")  # lock so two calls can't grab the same table
        result, table = _check(con, date, time, party_size)
        if table is None:
            con.execute("ROLLBACK")
            return result
        n = con.execute("SELECT COUNT(*) FROM reservations").fetchone()[0] + 1
        rid = f"RES{n:04d}"
        date, time = result["date"], result["time"]  # normalised by _check
        start = _mins(time)
        con.execute("INSERT INTO reservations VALUES (?,?,?,?,?,?,?,?,?)",
                    (rid, table, date, time, _fmt(start + SITTING_MIN), party_size, guest_name, phone, "confirmed"))
        con.execute("COMMIT")
        return {"status": "confirmed", "reservation_id": rid, "table_id": table, "date": date, "time": time,
                "party_size": party_size, "guest_name": guest_name}
    except Exception as e:  # noqa: BLE001
        try:
            con.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        return {"status": "error", "message": str(e), "handoff_recommended": True}
    finally:
        con.close()


def propose_booking(date, time, party_size, guest_name, phone):
    """Dry run of create_reservation: runs every check, books nothing. The agent registers the proposal,
    reads it back to the caller, and only books after a clear yes on a later turn."""
    guest_name = (guest_name or "").strip()
    digits = "".join(c for c in str(phone or "") if c.isdigit())
    if not guest_name or len(digits) < 7:
        return {"status": "invalid_input", "message": "Need the guest's name and a phone number (at least 7 digits)."}
    con = _conn()
    try:
        result, table = _check(con, date, time, party_size)
    finally:
        con.close()
    if table is None:
        return result
    return {"status": "proposed", "date": result["date"], "time": result["time"], "party_size": party_size,
            "guest_name": guest_name, "phone": str(phone),
            "instruction": ("NOTHING IS BOOKED YET. Read the booking back to the caller (day, time, party size, name, "
                            "phone number digit by digit) and ask them to confirm. Only after a clear yes, call "
                            "create_reservation with exactly these details.")}


def lookup_reservation(guest_name, date):
    try:
        date = datetime.strptime(date, DATE_FMT).strftime(DATE_FMT)
    except (ValueError, TypeError):
        return {"status": "invalid_input", "message": "Date must be DD-MM-YYYY."}
    con = _conn()
    try:
        rows = con.execute(
            "SELECT reservation_id, date, start_time, party_size, guest_name FROM reservations "
            "WHERE LOWER(guest_name) LIKE ? AND date=? AND status='confirmed'",
            (f"%{guest_name.strip().lower()}%", date)).fetchall()
        return {"status": "found" if rows else "not_found", "reservations": [dict(r) for r in rows]}
    finally:
        con.close()


def cancel_reservation(reservation_id, phone):
    """Phone must match the booking - this is the identity check."""
    con = _conn()
    try:
        row = con.execute("SELECT phone, status FROM reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
        norm = lambda p: "".join(c for c in str(p) if c.isdigit())[-10:]
        if not row or norm(row["phone"]) != norm(phone):
            return {"status": "verification_failed", "message": "Could not verify that booking."}
        if row["status"] == "cancelled":
            return {"status": "already_cancelled", "reservation_id": reservation_id}
        con.execute("UPDATE reservations SET status='cancelled' WHERE reservation_id=?", (reservation_id,))
        return {"status": "cancelled", "reservation_id": reservation_id}
    finally:
        con.close()


# ---------- allergen guardrail ----------
def _normalise_allergens(allergen):
    if not allergen:
        return None, None
    key = allergen.strip().lower().replace("-", " ")
    if key in ALLERGEN_ALIASES:
        return ALLERGEN_ALIASES[key], None
    return None, {"status": "unknown_allergen", "handoff_recommended": True,
                  "message": f"'{allergen}' is not an allergen we track. Do not answer; transfer to staff."}


def _item_report(con, item, wanted):
    if not item["allergen_info_verified"]:
        return {"item": item["name"], "verified": False,
                "instruction": "Allergen data is NOT verified. Do not guess. Say you cannot confirm and offer to transfer to staff."}
    rows = {r["allergen"]: r["status"] for r in
            con.execute("SELECT allergen, status FROM menu_item_allergens WHERE item_id=?", (item["item_id"],))}
    if not rows:  # verified flag set but no data: treat as unverified, never as "free"
        return {"item": item["name"], "verified": False,
                "instruction": "No allergen records found. Do not guess; offer to transfer to staff."}
    shown = wanted or ALLERGENS
    return {"item": item["name"], "verified": True,
            "contains": [a for a in shown if rows.get(a) == "contains"],
            "may_contain_cross_contact": [a for a in shown if rows.get(a) == "may_contain"],
            "free_of": [a for a in shown if rows.get(a) == "free"]}


def search_menu_allergens(item_name=None, allergen=None):
    """Answer allergen questions ONLY from verified data.

    - item_name given: report on that item (several reports if the name is ambiguous, e.g. "pesto").
    - allergen only: list items verified free of it, and items with cross-contact risk.
    """
    wanted, err = _normalise_allergens(allergen)
    if err:
        return err
    con = _conn()
    try:
        items = con.execute("SELECT * FROM menu_items").fetchall()
        if item_name:
            q = item_name.strip().lower()
            matches = [i for i in items if q in i["name"].lower()]
            if not matches:
                close = difflib.get_close_matches(q, [i["name"].lower() for i in items], n=3, cutoff=0.6)
                matches = [i for i in items if i["name"].lower() in close]
            if not matches:
                return {"status": "not_found", "handoff_recommended": True,
                        "message": "No menu item matches. Do not guess what is in it."}
            reports = [_item_report(con, i, wanted) for i in matches]
            return {"status": "ok" if len(reports) == 1 else "multiple_matches", "items": reports}
        if not wanted:
            return {"status": "invalid_input", "message": "Provide an item_name, an allergen, or both."}
        safe, cross, unverified = [], [], []
        for i in items:
            if not i["available"]:
                continue  # only recommend dishes currently being served
            r = _item_report(con, i, wanted)
            if not r["verified"]:
                unverified.append(i["name"])
            elif r["contains"]:
                continue
            elif r["may_contain_cross_contact"]:
                cross.append(i["name"])
            else:
                safe.append(i["name"])
        return {"status": "ok", "allergens_checked": wanted, "verified_free": safe,
                "cross_contact_risk": cross, "not_verified_do_not_recommend": unverified}
    finally:
        con.close()


_CATEGORY_ALIASES = {"starter": "starter", "appetizer": "starter", "app": "starter", "main": "main", "entree": "main",
                     "dessert": "dessert", "sweet": "dessert", "drink": "drink", "beverage": "drink"}


def search_menu(category=None, name=None, max_price=None):
    """Menu names, prices and descriptions. Deliberately returns NO ingredients or allergen claims:
    allergen questions must go through search_menu_allergens (verified data only)."""
    cat = None
    if category:
        key = category.strip().lower().rstrip("s")
        cat = _CATEGORY_ALIASES.get(key)
        if cat is None:
            return {"status": "invalid_input", "message": "Category must be starter, main, dessert or drink."}
    try:
        cap = float(max_price) if max_price not in (None, "") else None
    except (TypeError, ValueError):
        return {"status": "invalid_input", "message": "max_price must be a number."}
    con = _conn()
    try:
        rows = con.execute("SELECT name, category, price_usd, description FROM menu_items WHERE available=1 "
                           "ORDER BY CASE category WHEN 'starter' THEN 1 WHEN 'main' THEN 2 WHEN 'dessert' THEN 3 ELSE 4 END, "
                           "price_usd").fetchall()
    finally:
        con.close()
    q = (name or "").strip().lower()
    items = [{"name": r["name"], "category": r["category"], "price_usd": r["price_usd"], "description": r["description"]}
             for r in rows
             if (cat is None or r["category"] == cat)
             and (cap is None or r["price_usd"] <= cap)
             and (not q or q in r["name"].lower() or q in (r["description"] or "").lower())]
    if not items:
        return {"status": "not_found", "message": "Nothing on the menu matches. Do not invent dishes."}
    return {"status": "ok", "items": items[:12], "truncated": len(items) > 12,
            "note": "For allergens or ingredients, use search_menu_allergens; never infer them from descriptions."}


def handoff_to_human(reason, context=""):
    con = _conn()
    try:
        con.execute("CREATE TABLE IF NOT EXISTS handoffs(id INTEGER PRIMARY KEY, created TEXT, reason TEXT, context TEXT)")
        con.execute("INSERT INTO handoffs(created, reason, context) VALUES (?,?,?)",
                    (datetime.now().isoformat(timespec="seconds"), reason, context))
        return {"status": "handoff_logged", "message": "Tell the caller you are transferring them to a team member."}
    finally:
        con.close()


TOOLS = {
    "check_availability": check_availability,
    "propose_booking": propose_booking,
    "create_reservation": create_reservation,
    "lookup_reservation": lookup_reservation,
    "cancel_reservation": cancel_reservation,
    "search_menu_allergens": search_menu_allergens,
    "search_menu": search_menu,
    "handoff_to_human": handoff_to_human,
}