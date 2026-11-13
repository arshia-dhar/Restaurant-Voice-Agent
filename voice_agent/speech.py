"""Speech-to-text (Groq Whisper, same API key as the LLM) and text-to-speech (Windows voices via pyttsx3).

TTS is deliberately behind one function, speak(text), so a better voice (edge-tts, a cloud API) can be swapped in
later without touching anything else.
"""
import os
import re

STT_MODEL = os.environ.get("STT_MODEL", "whisper-large-v3-turbo")
STT_PROMPT = ("A phone call to Trattoria Bellavista, an Italian restaurant. Table reservations, party sizes, dates, "
              "times, phone numbers, allergies, nuts, pesto, gnocchi.")
# Whisper sometimes invents these when it is fed near-silence.
_HALLUCINATIONS = {"you", "thanks for watching", "thank you for watching", "please subscribe", "bye bye"}


def is_noise_text(text):
    t = re.sub(r"[^a-z ]", "", (text or "").lower()).strip()
    return not t or t in _HALLUCINATIONS


def transcribe(client, wav_bytes):
    """Returns the caller's words as text ('' if nothing usable)."""
    r = client.audio.transcriptions.create(
        model=os.environ.get("STT_MODEL", STT_MODEL), file=("speech.wav", wav_bytes, "audio/wav"),
        language="en", temperature=0, prompt=STT_PROMPT)
    text = (getattr(r, "text", None) or "").strip()
    return "" if is_noise_text(text) else text


def clean_for_speech(text):
    """Strip things a TTS engine reads badly: markdown marks and the fancy hyphens some models emit."""
    text = re.sub(r"[‐‑‒–—―]", " ", text)   # nine‑eight‑seven -> nine eight seven
    text = re.sub(r"[*_#`~]", "", text)
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip()


def speak(text, rate=175):
    """Speak text out loud and wait until finished (no interruption yet)."""
    try:
        import pyttsx3
    except ImportError as e:
        raise RuntimeError("Text-to-speech needs:  pip install pyttsx3") from e
    engine = pyttsx3.init()          # fresh engine per call avoids a known Windows hang on reuse
    engine.setProperty("rate", rate)
    engine.say(clean_for_speech(text))
    engine.runAndWait()
    engine.stop()
