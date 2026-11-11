# Trattoria Bellavista voice-agent tools

- Dates are **DD-MM-YYYY** everywhere (tools, database, CSVs). Times are 24h HH:MM.
- Reservations are generated relative to today. To refresh them, run `python seed_db.py`.
  It keeps 7 days of history and 14 days ahead, and prints two scenario days:
  DAY1: one table free for 4 at 19:30. DAY2: fully booked for 4 at 19:30. Test scripts say DAY1/DAY2.
- `python test_tools.py` reseeds a temp copy of the DB, so it passes on any date.
- Run the API: `pip install fastapi uvicorn` then `uvicorn main:app --reload` (docs at /docs).
