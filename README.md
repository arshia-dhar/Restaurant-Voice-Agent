# Trattoria Bellavista voice-agent tools

- Dates are **DD-MM-YYYY** everywhere (tools, database, CSVs). Times are 24h HH:MM.
- Reservations are generated relative to today. To refresh them, run `python seed_db.py`.
  It keeps 7 days of history and 14 days ahead, and prints two scenario days:
  DAY1: one table free for 4 at 19:30. DAY2: fully booked for 4 at 19:30. Test scripts say DAY1/DAY2.
- `python test_tools.py` reseeds a temp copy of the DB, so it passes on any date.
- Run the API: `pip install fastapi uvicorn` then `uvicorn main:app --reload` (docs at /docs).

## Text agent (step 1 of the voice project)
- Put `GROQ_API_KEY=...` in a `.env` file next to `agent.py` (see `.env.example`). Never share or commit it.
- `pip install openai`, then `python chat.py` to talk to the agent. Tool calls print in [brackets].
- If the default model is rejected, run `python chat.py --list-models` and set `LLM_MODEL=...` in `.env`.
- `python test_agent.py` checks the loop offline with a scripted fake model (no key needed).

## Voice (microphone in, speaker out)
- `pip install sounddevice numpy pyttsx3` (plus `openai`, already needed). Use headphones.
- `python voice_chat.py --mic-test` shows a live level meter; `--list-devices` / `--device N` pick a microphone.
- `python voice_chat.py` starts a call. Say "goodbye" to end it, or press Ctrl+C.
- Speech-to-text: Groq Whisper (same key, `STT_MODEL` in `.env` to change). Text-to-speech: Windows voices (pyttsx3).
- `python test_voice.py` tests the voice layer offline (no mic or network needed).
