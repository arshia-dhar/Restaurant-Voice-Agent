"""JSON-schema tool definitions to hand to your LLM (Anthropic format; for OpenAI wrap each as
{"type": "function", "function": {"name", "description", "parameters": <input_schema>}})."""

TOOL_SCHEMAS = [
    {"name": "check_availability",
     "description": "Check whether a table is free. Returns alternative times if not. Always call before promising a table.",
     "input_schema": {"type": "object", "properties": {
         "date": {"type": "string", "description": "DD-MM-YYYY, e.g. 05-11-2026"},
         "time": {"type": "string", "description": "24h HH:MM, e.g. 19:30"},
         "party_size": {"type": "integer"}},
         "required": ["date", "time", "party_size"]}},
    {"name": "propose_booking",
     "description": ("Step before booking: checks everything and registers the details, but books NOTHING. Call it once you "
                     "have date, time, party size, name and phone, then read the booking back to the caller. Call it again "
                     "whenever any detail changes."),
     "input_schema": {"type": "object", "properties": {
         "date": {"type": "string", "description": "DD-MM-YYYY"}, "time": {"type": "string", "description": "24h HH:MM"},
         "party_size": {"type": "integer"}, "guest_name": {"type": "string"}, "phone": {"type": "string"}},
         "required": ["date", "time", "party_size", "guest_name", "phone"]}},
    {"name": "create_reservation",
     "description": ("Book the table. Only works with exactly the details last passed to propose_booking, and only on a "
                     "later turn, after the caller clearly said yes to the read-back."),
     "input_schema": {"type": "object", "properties": {
         "date": {"type": "string", "description": "DD-MM-YYYY"}, "time": {"type": "string", "description": "24h HH:MM"}, "party_size": {"type": "integer"},
         "guest_name": {"type": "string"}, "phone": {"type": "string"}},
         "required": ["date", "time", "party_size", "guest_name", "phone"]}},
    {"name": "lookup_reservation",
     "description": "Find a confirmed reservation by guest name and date.",
     "input_schema": {"type": "object", "properties": {
         "guest_name": {"type": "string"}, "date": {"type": "string", "description": "DD-MM-YYYY"}},
         "required": ["guest_name", "date"]}},
    {"name": "cancel_reservation",
     "description": "Cancel a reservation. Requires the phone number on the booking as verification.",
     "input_schema": {"type": "object", "properties": {
         "reservation_id": {"type": "string"}, "phone": {"type": "string"}},
         "required": ["reservation_id", "phone"]}},
    {"name": "search_menu_allergens",
     "description": ("The ONLY source for allergen answers. Pass item_name for a dish, allergen for 'what is safe', or both. "
                     "If a result says verified=false, not_found or unknown_allergen, you must NOT answer from your own "
                     "knowledge: say you can't confirm and offer a staff transfer. Always mention cross_contact items."),
     "input_schema": {"type": "object", "properties": {
         "item_name": {"type": "string"},
         "allergen": {"type": "string", "description": "e.g. nuts, peanuts, tree nuts, dairy, gluten, shellfish"}}}},
    {"name": "search_menu",
     "description": ("Menu dishes with prices (US dollars) and descriptions. Filter by category (starter, main, dessert, drink), "
                     "by a word in the dish name or description, or by max_price. Use for 'what do you have', 'how much is X', "
                     "'anything under 20'. NOT for allergens or ingredients: use search_menu_allergens for those."),
     "input_schema": {"type": "object", "properties": {
         "category": {"type": "string", "description": "starter, main, dessert or drink"},
         "name": {"type": "string", "description": "word to look for in dish name or description"},
         "max_price": {"type": "number"}}}},
    {"name": "handoff_to_human",
     "description": "Transfer to staff: unverified allergen data, tool failures after one retry, parties over 8, anything out of scope you can't resolve.",
     "input_schema": {"type": "object", "properties": {
         "reason": {"type": "string"}, "context": {"type": "string"}},
         "required": ["reason"]}},
]