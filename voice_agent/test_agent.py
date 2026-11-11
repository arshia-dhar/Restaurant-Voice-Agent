"""Offline tests for the loop's plumbing, using a scripted fake model (no API key, no network).
These check the loop, NOT how smart the real model is.   Run:  python test_agent.py"""
import json
import os
import shutil
import sqlite3
import tempfile
from datetime import date, datetime
from types import SimpleNamespace as NS

HERE = os.path.dirname(os.path.abspath(__file__))
tmp = os.path.join(tempfile.mkdtemp(), "restaurant.db")
shutil.copy(os.path.join(HERE, "restaurant.db"), tmp)
os.environ["DB_PATH"] = tmp

import agent  # noqa: E402
import seed_db  # noqa: E402
import tools  # noqa: E402

TODAY = date.today()
seed_db.seed(tmp, today=TODAY)
tools._now = lambda: datetime.combine(TODAY, datetime.min.time()).replace(hour=12)
D1, D2 = seed_db.scenario_days(TODAY, seed_db.load_hours(sqlite3.connect(tmp)))
f = lambda d: d.strftime("%d-%m-%Y")  # noqa: E731


def tc(i, name, args):
    return NS(id=i, type="function", function=NS(name=name, arguments=args if isinstance(args, str) else json.dumps(args)))


def msg(content=None, calls=None):
    return NS(content=content, tool_calls=calls)


class FakeClient:
    def __init__(self, script):
        self.script, self.calls = list(script), 0
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kw):
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return NS(choices=[NS(message=item)])


def make(script, **kw):
    return agent.Agent(FakeClient(script), model="fake", retry_delay=0, **kw)


def tool_results(a):
    return [json.loads(m["content"]) for m in a.messages if m["role"] == "tool"]


def test_system_prompt_has_date_calendar_hours():
    p = agent.system_prompt()
    assert f(TODAY) in p and TODAY.strftime("%A") in p
    assert "(today)" in p and "(tomorrow)" in p
    assert "Monday: closed" in p and "last seating" in p
    assert "DD-MM-YYYY" in p


def test_tool_roundtrip():
    a = make([msg(calls=[tc("1", "check_availability", {"date": f(D1), "time": "19:30", "party_size": 4})]),
              msg("Yes, we have a table for four.")])
    out = a.respond("Table for four at seven thirty?")
    assert out == "Yes, we have a table for four."
    r = tool_results(a)[0]
    assert r["status"] == "available" and r["table_id"] == "T8", r
    assert a.trace[0]["steps"][0]["tool"] == "check_availability"


def test_party_size_string_is_coerced():
    a = make([msg(calls=[tc("1", "check_availability", {"date": f(D1), "time": "19:30", "party_size": "4"})]),
              msg("ok")])
    a.respond("x")
    assert tool_results(a)[0]["status"] == "available"


def test_bad_json_and_unknown_tool_and_bad_args():
    a = make([msg(calls=[tc("1", "check_availability", "{not json"), tc("2", "make_coffee", {}),
                         tc("3", "check_availability", {"date": f(D1)})]),
              msg("Sorry, could you repeat that?")])
    out = a.respond("x")
    s = [r["status"] for r in tool_results(a)]
    assert s == ["invalid_input", "error", "invalid_input"], s
    assert out.startswith("Sorry")


def test_api_error_retries_once_then_works():
    a = make([Exception("boom"), msg("hello")])
    assert a.respond("hi") == "hello" and a.trace[0]["errors"]


def test_api_error_twice_falls_back_and_logs_handoff():
    con = sqlite3.connect(tmp)
    con.execute("CREATE TABLE IF NOT EXISTS handoffs(id INTEGER PRIMARY KEY, created TEXT, reason TEXT, context TEXT)")
    before = con.execute("SELECT COUNT(*) FROM handoffs").fetchone()[0]
    con.commit()
    a = make([Exception("boom"), Exception("boom")])
    out = a.respond("hi")
    assert out == agent.FALLBACK_TEXT and a.trace[0]["handoff"]
    assert sqlite3.connect(tmp).execute("SELECT COUNT(*) FROM handoffs").fetchone()[0] == before + 1


def test_step_limit_stops_runaway_tool_loop():
    loop = [msg(calls=[tc(str(i), "check_availability", {"date": f(D1), "time": "19:30", "party_size": 2})])
            for i in range(10)]
    client_calls = make(loop, max_steps=3)
    out = client_calls.respond("x")
    assert out == agent.FALLBACK_TEXT and client_calls.client.calls == 3


def test_conversation_memory_and_handoff_flag():
    a = make([msg("Hello!"), msg(calls=[tc("1", "handoff_to_human", {"reason": "asked for a person"})]),
              msg("Transferring you now.")])
    a.respond("hi")
    a.respond("let me talk to a person")
    assert [m["role"] for m in a.messages] == ["system", "user", "assistant", "user", "assistant", "tool", "assistant"]
    assert a.trace[1]["handoff"] is True


def _rows(name):
    return sqlite3.connect(tmp).execute("SELECT COUNT(*) FROM reservations WHERE guest_name=?", (name,)).fetchone()[0]


def _args(name, **kw):
    return dict({"date": f(D1), "time": "19:30", "party_size": 4, "guest_name": name, "phone": "9818620000"}, **kw)


def test_propose_then_caller_yes_then_book():
    args = _args("Gate Tester")
    a = make([msg(calls=[tc("1", "propose_booking", args)]), msg("Is that correct?"),
              msg(calls=[tc("2", "create_reservation", args)]), msg("All booked.")])
    a.respond("my number is 9818620000")
    assert tool_results(a)[0]["status"] == "proposed" and _rows("Gate Tester") == 0
    a.respond("yes")
    assert tool_results(a)[1]["status"] == "confirmed" and _rows("Gate Tester") == 1


def test_create_without_proposal_is_refused():
    a = make([msg(calls=[tc("1", "create_reservation", _args("No Proposal", party_size=2, time="18:00", date=f(D2)))]),
              msg("ok")])
    a.respond("x")
    assert tool_results(a)[0]["status"] == "not_proposed" and _rows("No Proposal") == 0


def test_cannot_book_in_the_same_turn_as_the_proposal():
    args = _args("Same Turn", party_size=2, time="18:00", date=f(D2))
    a = make([msg(calls=[tc("1", "propose_booking", args), tc("2", "create_reservation", args)]), msg("ok")])
    a.respond("x")
    assert [r["status"] for r in tool_results(a)] == ["proposed", "awaiting_caller_confirmation"]
    assert _rows("Same Turn") == 0


def test_changed_details_need_a_new_proposal():
    base = _args("Changer", party_size=2, time="18:00", date=f(D2))
    changed = dict(base, party_size=3)
    a = make([msg(calls=[tc("1", "propose_booking", base)]), msg("Is that right?"),
              msg(calls=[tc("2", "create_reservation", changed)]), msg("Is that right?")])
    a.respond("x")
    a.respond("actually three of us, yes")        # caller changed the size but the agent never re-proposed
    assert [r["status"] for r in tool_results(a)] == ["proposed", "not_proposed"] and _rows("Changer") == 0


def test_equivalent_formatting_still_matches():
    prop = _args("Formatter", party_size=2, time="18:00", date=f(D2))
    book = dict(prop, time="18:00", date=f(D2).lstrip("0"), phone="98186 20000")   # e.g. 6-11-2026, spaced phone
    a = make([msg(calls=[tc("1", "propose_booking", prop)]), msg("Correct?"),
              msg(calls=[tc("2", "create_reservation", book)]), msg("Done")])
    a.respond("x")
    a.respond("yes")
    assert tool_results(a)[1]["status"] == "confirmed"


def test_proposal_that_fails_checks_registers_nothing():
    closed = TODAY + __import__("datetime").timedelta(days=1)
    while closed.weekday() != 0:
        closed += __import__("datetime").timedelta(days=1)
    bad = _args("Closed Day", date=f(closed))
    a = make([msg(calls=[tc("1", "propose_booking", bad)]), msg("We're closed."),
              msg(calls=[tc("2", "create_reservation", bad)]), msg("x")])
    a.respond("x")
    a.respond("yes")
    assert [r["status"] for r in tool_results(a)] == ["closed", "not_proposed"]


def test_openai_tool_format():
    assert len(agent.OPENAI_TOOLS) == 8
    for t in agent.OPENAI_TOOLS:
        assert t["type"] == "function" and t["function"]["parameters"]["type"] == "object"


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