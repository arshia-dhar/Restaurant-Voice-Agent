"""Offline tests for the voice layer: endpointing, text cleaning and the call loop. No microphone, speaker or
network needed.   Run:  python test_voice.py"""
import io
import wave

import numpy as np

import audio_io
import speech
from voice_chat import VoiceSession

SR, BLK = audio_io.SAMPLE_RATE, audio_io.BLOCK
rng = np.random.default_rng(0)


def noise(seconds, amp=60):
    return [rng.normal(0, amp, BLK).astype(np.int16) for _ in range(int(seconds * SR / BLK))]


def tone(seconds, amp=3000):
    n = int(seconds * SR / BLK)
    t = np.arange(n * BLK) / SR
    s = (amp * np.sin(2 * np.pi * 220 * t)).astype(np.int16)
    return [s[i * BLK:(i + 1) * BLK] for i in range(n)]


def run(blocks, **kw):
    ep = audio_io.Endpointer(threshold=400, **kw)
    events = []
    for b in blocks:
        e = ep.feed(b)
        if e:
            events.append(e)
            if e in ("done", "timeout"):
                break
    return events, ep


# ---------- endpointing ----------
def test_endpointer_captures_one_utterance():
    events, ep = run(noise(0.5) + tone(1.0) + noise(2.0))
    secs = len(ep.audio()) / SR
    assert events == ["done"] and 1.5 < secs < 2.6, (events, secs)   # speech + preroll + trailing silence


def test_endpointer_times_out_when_nobody_speaks():
    events, _ = run(noise(10))
    assert events == ["timeout"]


def test_short_blip_is_ignored_then_real_speech_works():
    events, ep = run(noise(0.3) + tone(0.1) + noise(1.5) + tone(1.0) + noise(1.5))
    assert events == ["done"] and len(ep.audio()) / SR > 1.0


def test_max_length_cuts_off_endless_talking():
    events, ep = run(tone(25), max_s=5.0)
    assert events == ["done"] and len(ep.audio()) / SR <= 5.1


def test_threshold_estimate_and_wav():
    assert audio_io.estimate_threshold([100.0] * 10) == audio_io.MIN_THRESHOLD       # quiet room: floor
    assert audio_io.estimate_threshold([300.0] * 10) == 900.0                          # noisy room: 3x
    w = wave.open(io.BytesIO(audio_io.to_wav(np.zeros(1600, dtype=np.int16))))
    assert (w.getnchannels(), w.getframerate(), w.getnframes()) == (1, 16000, 1600)


# ---------- text helpers ----------
def test_clean_for_speech():
    out = speech.clean_for_speech("Phone is nine‑eight‑seven, **great** — see you")
    assert "‑" not in out and "*" not in out and "nine eight seven" in out


def test_noise_text_filter():
    assert speech.is_noise_text("") and speech.is_noise_text(" You. ") and speech.is_noise_text("Thanks for watching!")
    assert not speech.is_noise_text("Table for two please") and not speech.is_noise_text("thank you")


# ---------- call loop (fake audio, fake agent) ----------
class FakeAgent:
    def __init__(self):
        self.messages, self.trace, self.heard = [], [], []

    def respond(self, text):
        self.heard.append(text)
        self.trace.append({"steps": [], "llm_ms": 10, "tool_ms": 1, "handoff": text == "a person please"})
        return f"reply:{text}"


def session(audio, texts, **kw):
    """audio: items returned by listen(); texts: what transcribe returns for each non-None item."""
    spoken, ag = [], FakeAgent()
    audio, texts = list(audio), list(texts)

    def stt(_a):
        t = texts.pop(0)
        if isinstance(t, Exception):
            raise t
        return t
    s = VoiceSession(ag, listen=lambda: audio.pop(0), transcribe=stt, speak=spoken.append, log=lambda *_: None, **kw)
    return s, ag, spoken


def test_normal_call_ends_on_goodbye():
    s, ag, spoken = session([b"1", b"2", b"3"], ["hello", "table for two", "ok goodbye"])
    assert s.run() == "caller_ended"
    assert spoken[0].startswith("Thank you for calling") and spoken[1:] == ["reply:hello", "reply:table for two", "reply:ok goodbye"]
    assert ag.messages[0]["role"] == "assistant"             # the model knows the greeting was said
    assert len(s.metrics) == 3 and {"stt_ms", "agent_ms", "time_to_speech_ms"} <= set(s.metrics[0])


def test_silence_prompts_then_ends_call():
    s, ag, spoken = session([None, None], [])
    assert s.run() == "silence" and "Are you still there?" in spoken and ag.heard == []


def test_silence_counter_resets_after_speech():
    s, ag, spoken = session([None, b"1", None, b"2"], ["hi", "bye"])
    assert s.run() == "caller_ended" and spoken.count("Are you still there?") == 2


def test_speech_to_text_failure_asks_to_repeat_and_continues():
    s, ag, spoken = session([b"1", b"2"], [RuntimeError("boom"), "goodbye"])
    assert s.run() == "caller_ended" and ag.heard == ["goodbye"]
    assert any("didn't catch" in x for x in spoken)


def test_blank_transcript_is_ignored_silently():
    s, ag, spoken = session([b"1", b"2"], ["", "goodbye"])
    assert s.run() == "caller_ended" and ag.heard == ["goodbye"] and not any("catch" in x for x in spoken)


def test_handoff_ends_call_after_speaking():
    s, ag, spoken = session([b"1"], ["a person please"])
    assert s.run() == "handoff" and spoken[-1] == "reply:a person please"


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
