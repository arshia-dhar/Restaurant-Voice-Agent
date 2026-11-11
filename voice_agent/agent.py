"""Text agent loop: an LLM that talks to the caller and calls the tools in tools.py.

Provider-neutral: works with any OpenAI-compatible endpoint (Groq by default; Gemini, Ollama etc. by changing
LLM_BASE_URL / LLM_MODEL / the API key in .env).  The speech layer will later wrap respond(): text in, text out.
"""
import json
import os
import time
from datetime import datetime

import tools
from tool_schemas import TOOL_SCHEMAS

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "openai/gpt-oss-120b"   # Groq retires models often; override with LLM_MODEL in .env
FALLBACK_TEXT = ("I'm sorry, I'm having trouble on my end. Let me put you through to a member of our team.")

# Our schemas are written in Anthropic style; OpenAI-compatible APIs want this wrapper.
OPENAI_TOOLS = [{"type": "function",
                 "function": {"name": s["name"], "description": s["description"], "parameters": s["input_schema"]}}
                for s in TOOL_SCHEMAS]


# ---------- config ----------
def load_env():
    """Tiny .env reader (no extra dependency). Real environment variables win over the file."""
    for folder in {HERE, os.getcwd()}:
        path = os.path.join(folder, ".env")
        if not os.path.exists(path):
            continue
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def get_client():
    load_env()
    key = os.environ.get("GROQ_API_KEY") or os.environ.get("LLM_API_KEY")
    if not key:
        raise RuntimeError("No API key found. Put GROQ_API_KEY=... in the .env file next to agent.py.")
    from openai import OpenAI  # imported here so tests don't need the package
    return OpenAI(api_key=key, base_url=os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL), timeout=30)


# ---------- system prompt ----------
def _hours_text():
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    con = tools._conn()
    try:
        rows = {r["day_of_week"]: r for r in con.execute("SELECT * FROM opening_hours")}
    finally:
        con.close()
    out = []
    for i, name in enumerate(days):
        r = rows[i]
        out.append(f"{name}: closed" if r["open"] == "CLOSED"
                   else f"{name}: {r['open']} to {r['close']} (last seating {r['kitchen_close']})")
    return "\n".join(out)


def _calendar_text(now):
    lines = []
    for i in range(14):
        d = now.date() + __import__("datetime").timedelta(days=i)
        tag = " (today)" if i == 0 else " (tomorrow)" if i == 1 else ""
        lines.append(f"{d.strftime('%A')} {d.strftime(tools.DATE_FMT)}{tag}")
    return "\n".join(lines)


def system_prompt(now=None):
    now = now or tools._now()
    return f"""You are the phone receptionist for Trattoria Bellavista, an Italian restaurant. The caller is on the phone, so:
- Reply in one to three short, natural sentences. No lists, no markdown, no emojis. Ask one question at a time.
- Say dates and times the way people speak ("Tuesday the tenth at seven thirty in the evening"), never as 10-11-2026 or 19:30.

Right now it is {now.strftime('%A')} {now.strftime(tools.DATE_FMT)}, {now.strftime('%H:%M')} (restaurant local time).
Calendar for the next 14 days:
{_calendar_text(now)}

Opening hours (24h):
{_hours_text()}

DATES AND TIMES
- Tools take dates as DD-MM-YYYY and times as 24-hour HH:MM.
- Convert "tonight", "tomorrow", "this Friday", "next Tuesday" using the calendar above. Never work out dates yourself.
- If the date is ambiguous (for example "the 14th" with no month, or "next week" with no day), ask. A time with no am or pm: assume evening and confirm it, unless it clearly sounds like lunch.
- If the caller changes their mind mid-sentence, use the latest version. If a transcript looks garbled (for example a party size that doesn't make sense), politely ask them to repeat it.

BOOKING A TABLE
1. Get date, time and party size. 
2. Call check_availability. 
3. If unavailable, offer only the alternative_times the tool returns; never invent times.
4. Get the caller's name and phone number.
5. Call propose_booking with all five details. It books nothing. Then read the whole booking back in spoken form (day, time, party size, name, phone number digit by digit) and ask the caller to confirm.
6. Only after the caller clearly says yes, call create_reservation with exactly the same details.
7. Tell the caller it is confirmed and say the reservation_id from the result.
If the caller changes any detail at any point, call propose_booking again with the new details and read it back again.
Never say a booking is made unless create_reservation returned status confirmed.

CANCELLING
Use lookup_reservation with name and date, then ask for the phone number on the booking. Call cancel_reservation only with that number.

ALLERGENS (safety-critical)
- Answer ONLY from search_menu_allergens results, never from your own knowledge of recipes.
- If a result says verified is false, not_found, unknown_allergen, or includes an instruction, do NOT guess. Say you cannot confirm it and offer to transfer them to the team (handoff_to_human).
- Always mention items listed under may_contain_cross_contact, because the kitchen shares equipment. Never call a dish completely safe; say what the verified records show and that people with severe allergies should confirm with the team.
- If a name matches several dishes (for example "pesto"), report on each one.
- Use allergen "nuts" when the caller says nuts.

MENU AND PRICES
Use search_menu for dishes, prices and descriptions; say prices in dollars. Mention at most three or four dishes at a time, then offer more. Never describe a dish or price that the tool did not return. Never state ingredients or allergens from the menu description; for those use search_menu_allergens.

OTHER QUESTIONS
You do not have special offers, deals or restaurant policies (parking, dress code, and so on). Say so honestly and offer a transfer; never make them up.

TRANSFERS
Call handoff_to_human for: unverified allergen information, parties of more than 8, a tool error that persists after one retry, complaints, or if the caller asks for a person. Then tell them you are transferring them.
Politely steer back if asked about anything unrelated to the restaurant."""


# ---------- the loop ----------
class Agent:
    def __init__(self, client, model=None, max_steps=6, retry_delay=0.5):
        load_env()
        self.client = client
        self.model = model or os.environ.get("LLM_MODEL", DEFAULT_MODEL)
        self.max_steps = max_steps
        self.retry_delay = retry_delay
        self.messages = [{"role": "system", "content": system_prompt()}]
        self.trace = []   # one dict per caller turn: tool calls + timings (for the evaluation metrics later)
        self.turn_no = 0
        self._pending = None   # booking awaiting the caller's "yes": (key, turn number it was proposed in)

    def respond(self, user_text):
        self.turn_no += 1
        self.messages.append({"role": "user", "content": user_text})
        turn = {"user": user_text, "steps": [], "llm_ms": 0, "tool_ms": 0, "handoff": False}
        self.trace.append(turn)

        for _ in range(self.max_steps):
            msg = self._llm_call(turn)
            if msg is None:
                return self._fallback(turn, "LLM call failed twice")
            calls = getattr(msg, "tool_calls", None) or []
            if not calls:
                text = (msg.content or "").strip()
                self.messages.append({"role": "assistant", "content": text})
                return text
            self.messages.append({
                "role": "assistant", "content": msg.content or "",
                "tool_calls": [{"id": c.id, "type": "function",
                                "function": {"name": c.function.name, "arguments": c.function.arguments or "{}"}}
                               for c in calls]})
            for c in calls:
                t0 = time.perf_counter()
                result = self._run_tool(c.function.name, c.function.arguments)
                ms = (time.perf_counter() - t0) * 1000
                turn["tool_ms"] += ms
                turn["steps"].append({"tool": c.function.name, "args": c.function.arguments,
                                      "status": result.get("status"), "ms": round(ms, 1)})
                if c.function.name == "handoff_to_human" or result.get("handoff_recommended"):
                    turn["handoff"] = turn["handoff"] or c.function.name == "handoff_to_human"
                self.messages.append({"role": "tool", "tool_call_id": c.id, "content": json.dumps(result)})
        return self._fallback(turn, "tool-call limit reached")

    # --- internals ---
    def _llm_call(self, turn):
        for attempt in (1, 2):
            t0 = time.perf_counter()
            try:
                r = self.client.chat.completions.create(
                    model=self.model, messages=self.messages, tools=OPENAI_TOOLS,
                    tool_choice="auto", temperature=0.2, max_tokens=1024, extra_body={"reasoning_effort": "low"})
                turn["llm_ms"] += (time.perf_counter() - t0) * 1000
                turn["llm_calls"] = turn.get("llm_calls", 0) + 1
                return r.choices[0].message
            except Exception as e:  # noqa: BLE001  (network errors, malformed tool calls rejected by the API, ...)
                turn["llm_ms"] += (time.perf_counter() - t0) * 1000
                turn.setdefault("errors", []).append(str(e)[:600])
                if attempt == 1 and self.retry_delay:
                    time.sleep(self.retry_delay)
        return None

    @staticmethod
    def _booking_key(args):
        """The booking details in a comparable form, or None if they are malformed (the tool will report that)."""
        try:
            date, tm = str(args.get("date")), str(args.get("time"))
            try:   # so "5-11-2026" / "7:30" match "05-11-2026" / "07:30"
                date = datetime.strptime(date, tools.DATE_FMT).strftime(tools.DATE_FMT)
                tm = datetime.strptime(tm, "%H:%M").strftime("%H:%M")
            except ValueError:
                pass
            return (date, tm, int(args.get("party_size")), str(args.get("guest_name", "")).strip().lower(),
                    "".join(c for c in str(args.get("phone", "")) if c.isdigit()))
        except (TypeError, ValueError):
            return None

    def _create_gate(self, args):
        """Code-level guarantee: create_reservation only books details that were proposed with propose_booking
        on an EARLIER caller turn (so the caller has had a chance to answer the read-back). Any change of
        detail means the old proposal no longer matches and propose_booking must be called again."""
        key = self._booking_key(args)
        if key is None:
            return None
        p = self._pending
        if p and p[0] == key:
            if self.turn_no > p[1]:
                self._pending = None
                return None
            return {"status": "awaiting_caller_confirmation",
                    "instruction": ("You proposed this booking in the current turn. Do NOT book yet: read it back to the "
                                    "caller and wait for their reply. Book on a later turn, once they clearly say yes.")}
        return {"status": "not_proposed",
                "instruction": ("These exact details were not proposed. Call propose_booking with them first, read the "
                                "booking back to the caller, and wait for their yes.")}

    def _run_tool(self, name, raw_args):
        fn = tools.TOOLS.get(name)
        if fn is None:
            return {"status": "error", "message": f"Unknown tool '{name}'."}
        try:
            args = json.loads(raw_args) if raw_args else {}
            if not isinstance(args, dict):
                raise ValueError
        except ValueError:
            return {"status": "invalid_input", "message": "Tool arguments were not valid JSON. Try again."}
        if "party_size" in args:
            try:
                args["party_size"] = int(args["party_size"])   # models sometimes send "4"
            except (TypeError, ValueError):
                pass
        if name == "create_reservation":
            gate = self._create_gate(args)
            if gate:
                return gate
        try:
            result = fn(**args)
            if name == "propose_booking" and result.get("status") == "proposed":
                key = self._booking_key(args)
                if key:
                    self._pending = (key, self.turn_no)   # remember what the caller is being asked to confirm
            return result
        except TypeError as e:
            return {"status": "invalid_input", "message": f"Bad arguments: {e}"}
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "message": str(e), "handoff_recommended": True}

    def _fallback(self, turn, reason):
        tools.handoff_to_human(reason, f"caller said: {turn['user']}")
        turn["handoff"] = True
        self.messages.append({"role": "assistant", "content": FALLBACK_TEXT})
        return FALLBACK_TEXT