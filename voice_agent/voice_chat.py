"""Talk to the restaurant agent out loud (microphone in, speaker out).

    python voice_chat.py                  # start a call
    python voice_chat.py --mic-test       # live level meter, to check your mic and threshold
    python voice_chat.py --list-devices   # show microphones (then use --device N)

Use HEADPHONES: with speakers the microphone hears the agent and answers itself.
Stop any time with Ctrl+C. Each turn prints how long the caller waited before the agent started speaking.
"""
import argparse
import re
import time

import agent as agent_mod

GREETING = "Thank you for calling Trattoria Bellavista. How can I help you today?"
END_WORDS = re.compile(r"\b(goodbye|bye|that's all|that is all)\b", re.I)


class VoiceSession:
    """The call loop. listen / transcribe / speak are injected, so the loop is testable without audio."""

    def __init__(self, agent, listen, transcribe, speak, max_silent_turns=2, log=print):
        self.agent, self.listen, self.transcribe, self.speak = agent, listen, transcribe, speak
        self.max_silent_turns, self.log = max_silent_turns, log
        self.metrics = []   # per turn: stt_ms, agent_ms, time_to_speech_ms  (for the response-time metric)

    def _say(self, text):
        self.log(f"Agent: {text}")
        self.speak(text)

    def run(self):
        self._say(GREETING)
        self.agent.messages.append({"role": "assistant", "content": GREETING})   # so the model knows it greeted
        silent = 0
        while True:
            audio = self.listen()
            if audio is None:
                silent += 1
                if silent >= self.max_silent_turns:
                    self._say("I can't hear anything, so I'll end the call. Goodbye.")
                    return "silence"
                self._say("Are you still there?")
                continue
            silent = 0
            t_heard = time.perf_counter()
            try:
                text = self.transcribe(audio)
            except Exception as e:  # noqa: BLE001
                self.log(f"   [SPEECH-TO-TEXT ERROR] {str(e)[:300]}")
                self._say("Sorry, I didn't catch that. Could you say it again?")
                continue
            stt_ms = (time.perf_counter() - t_heard) * 1000
            if not text:
                continue                      # just noise: keep listening quietly
            self.log(f"You: {text}")
            reply = self.agent.respond(text)
            tr = self.agent.trace[-1]
            for e in tr.get("errors", []):
                self.log(f"   [MODEL ERROR] {e}")
            for s in tr["steps"]:
                self.log(f"   [{s['tool']} -> {s['status']}]")
            to_speech = (time.perf_counter() - t_heard) * 1000
            self.metrics.append({"stt_ms": round(stt_ms), "agent_ms": round(tr["llm_ms"] + tr["tool_ms"]),
                                 "time_to_speech_ms": round(to_speech)})
            self.log(f"   (waited {to_speech / 1000:.1f} s: speech-to-text {stt_ms / 1000:.1f} s, "
                     f"agent {(tr['llm_ms'] + tr['tool_ms']) / 1000:.1f} s)")
            self._say(reply)
            if tr["handoff"]:
                self.log("   [handoff: a real call would transfer to a staff phone line here]")
                return "handoff"
            if END_WORDS.search(text):
                return "caller_ended"
            time.sleep(0.3)                   # let the speaker's tail die away before listening again


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mic-test", action="store_true")
    ap.add_argument("--list-devices", action="store_true")
    ap.add_argument("--device", type=int, default=None)
    ap.add_argument("--threshold", type=float, default=None, help="speech loudness threshold (default: auto)")
    ap.add_argument("--silence", type=float, default=0.9, help="seconds of quiet that end a sentence")
    a = ap.parse_args()

    import audio_io
    import speech
    if a.list_devices:
        return audio_io.list_devices()
    if a.mic_test:
        return audio_io.mic_test(a.device, a.threshold)

    client = agent_mod.get_client()
    print("Calibrating microphone: stay quiet for a second...")
    thr = a.threshold or audio_io.calibrate(a.device)
    print(f"Ready (speech threshold {thr:.0f}). Model: {agent_mod.os.environ.get('LLM_MODEL', agent_mod.DEFAULT_MODEL)}\n")
    session = VoiceSession(
        agent_mod.Agent(client),
        listen=lambda: audio_io.record_utterance(thr, a.device, silence_s=a.silence),
        transcribe=lambda wav: speech.transcribe(client, wav),
        speak=speech.speak)
    try:
        print("Call ended:", session.run())
    except KeyboardInterrupt:
        print("\nCall ended by Ctrl+C")
    if session.metrics:
        avg = sum(m["time_to_speech_ms"] for m in session.metrics) / len(session.metrics)
        print(f"Average wait before the agent spoke: {avg / 1000:.1f} s over {len(session.metrics)} turns")


if __name__ == "__main__":
    main()
