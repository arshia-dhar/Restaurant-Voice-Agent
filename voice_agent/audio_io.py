"""Microphone input: record one caller utterance and stop when they stop talking.

Simple energy-based endpointing (no ML model, no build tools needed on Windows):
  1. calibrate() measures room noise once at startup and sets a loudness threshold.
  2. Endpointer listens block by block: waits for speech, then ends the utterance after ~0.9 s of silence.
The Endpointer is pure logic (numpy only) so it is unit-tested without a microphone.
"""
import io
import wave
from collections import deque

import numpy as np

SAMPLE_RATE = 16000
BLOCK = 480            # 30 ms of audio per block
MIN_THRESHOLD = 350.0  # floor for the loudness threshold (int16 RMS); override with --threshold


def rms(block):
    return float(np.sqrt(np.mean(np.square(block.astype(np.float32)))))


def estimate_threshold(noise_levels):
    """Speech must be about 3x louder than the room's background noise."""
    base = float(np.percentile(noise_levels, 50)) if len(noise_levels) else 0.0
    return max(base * 3.0, MIN_THRESHOLD)


def to_wav(samples, sample_rate=SAMPLE_RATE):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(samples.astype(np.int16).tobytes())
    return buf.getvalue()


class Endpointer:
    """Feed 30 ms blocks; returns None while listening, 'done' when an utterance is complete, 'timeout' if
    nobody spoke. Short blips (a cough, a door) shorter than min_speech_s are ignored."""

    def __init__(self, threshold, sample_rate=SAMPLE_RATE, block=BLOCK, silence_s=0.9, min_speech_s=0.25,
                 max_s=20.0, start_timeout_s=8.0, preroll_s=0.3):
        self.thr = threshold
        bs = block / sample_rate
        self.silence_blocks = int(silence_s / bs)
        self.min_voiced = int(min_speech_s / bs)
        self.max_blocks = int(max_s / bs)
        self.timeout_blocks = int(start_timeout_s / bs)
        self.preroll = deque(maxlen=max(1, int(preroll_s / bs)))   # keeps the start of the first word
        self.reset()

    def reset(self):
        self.state, self.waited, self.loud_run = "waiting", 0, 0
        self.chunks, self.voiced, self.silent = [], 0, 0

    def feed(self, block):
        loud = rms(block) > self.thr
        if self.state == "waiting":
            self.preroll.append(block)
            self.waited += 1
            self.loud_run = self.loud_run + 1 if loud else 0
            if self.loud_run >= 2:                       # two loud blocks in a row = speech started
                self.state, self.chunks = "speaking", list(self.preroll)
                self.voiced, self.silent = self.loud_run, 0
            elif self.waited >= self.timeout_blocks:
                return "timeout"
            return None
        self.chunks.append(block)
        if loud:
            self.voiced, self.silent = self.voiced + 1, 0
        else:
            self.silent += 1
        if len(self.chunks) >= self.max_blocks:
            return "done"
        if self.silent >= self.silence_blocks:
            if self.voiced >= self.min_voiced:
                return "done"
            self.reset()                                  # just a blip, keep waiting
        return None

    def audio(self):
        return np.concatenate(self.chunks) if self.chunks else np.zeros(0, dtype=np.int16)


# ---------- real microphone (needs: pip install sounddevice) ----------
def _sd():
    try:
        import sounddevice as sd
    except ImportError as e:
        raise RuntimeError("Microphone support needs:  pip install sounddevice numpy") from e
    return sd


def list_devices():
    print(_sd().query_devices())


def calibrate(device=None, seconds=0.8):
    """Measure background noise (stay quiet) and return a speech threshold."""
    sd = _sd()
    levels = []
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=BLOCK, device=device) as s:
        for _ in range(int(seconds * SAMPLE_RATE / BLOCK)):
            data, _ = s.read(BLOCK)
            levels.append(rms(data[:, 0]))
    return estimate_threshold(levels)


def record_utterance(threshold, device=None, **kw):
    """Block until the caller has spoken a sentence. Returns WAV bytes, or None if nobody spoke."""
    sd = _sd()
    ep = Endpointer(threshold, **kw)
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=BLOCK, device=device) as s:
        while True:
            data, _ = s.read(BLOCK)
            event = ep.feed(data[:, 0])
            if event == "done":
                return to_wav(ep.audio())
            if event == "timeout":
                return None


def mic_test(device=None, threshold=None, seconds=10):
    """Print a live level meter so you can see where your voice sits relative to the threshold."""
    sd = _sd()
    thr = threshold or calibrate(device)
    print(f"Speech threshold: {thr:.0f}. Talk normally for {seconds} s; '#' bars should pass the '|' marker when you speak.")
    mark = int(thr / 100)
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=BLOCK, device=device) as s:
        for i in range(int(seconds * SAMPLE_RATE / BLOCK)):
            data, _ = s.read(BLOCK)
            level = rms(data[:, 0])
            if i % 3 == 0:
                bar = ("#" * min(60, int(level / 100))).ljust(mark)
                print(f"{bar[:mark]}|{bar[mark:]}  {level:6.0f}")
