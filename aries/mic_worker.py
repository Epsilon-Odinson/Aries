"""Microphone work for the Aries desktop app — runs ONLY in a helper
subprocess, never in the main app process.

Why: on some Windows laptops a bad audio driver makes PortAudio
crash the WHOLE process (a native segfault — no Python exception,
no error message, the app just vanishes) the moment the mic is
listed or opened. That is what killed the app when the user tapped
the mic button (and when he pressed the mini face, which also
listened). The fix is isolation: the main app never imports PyAudio
or opens a microphone. All of that happens in a short-lived child
process — the frozen .exe re-invoking itself with a hidden flag, or
``python -m aries.main`` in dev. If the child crashes, the parent
sees a non-zero exit and reports "the mic isn't available" in plain
words, and the app stays open.

Three modes (one flag each, read by aries.main):
- ``--mic-probe``: list microphones and open the default one
  briefly. Prints one JSON line: {"ok": true, "mics": [...]} or
  {"ok": false, "why": "..."}. Exit 0 when a mic is usable.
- ``--mic-record <wav> [timeout] [phrase_time_limit]``: listen once
  and write the captured audio as a WAV file. Prints {"ok": true,
  "heard": true/false}. Exit 0 when audio was captured (or nothing
  was heard — that is not an error), 1 on failure.
- ``--mic-wake``: loop forever listening for "Hey Aries"; prints
  "READY" once up, then "WAKE" (one line, flushed) each time the
  phrase is heard. The parent reads stdout and terminates the
  process to stop it.

Nothing here touches a widget. All output is plain JSON/lines on
stdout so the parent can read it without guessing.
"""

from __future__ import annotations

import json
import sys


def _print_json(obj: dict) -> None:
    print(json.dumps(obj), flush=True)


def mic_probe() -> int:
    """List microphones and open the default one briefly.

    Returns 0 when a mic is usable, 1 otherwise (with the reason
    printed as JSON). A native crash here kills only this child.
    """
    try:
        import speech_recognition as sr
    except ImportError as exc:
        _print_json({"ok": False, "why": f"SpeechRecognition is not installed ({exc})."})
        return 1
    try:
        import pyaudio  # noqa: F401  (sr.Microphone needs it)
    except ImportError as exc:
        _print_json({"ok": False, "why": f"PyAudio is not installed ({exc})."})
        return 1
    try:
        names = sr.Microphone.list_microphone_names()
    except Exception as exc:
        _print_json({"ok": False, "why": f"Could not list microphones: {exc}"})
        return 1
    if not names:
        _print_json({"ok": False, "why": "No microphone was found on this PC."})
        return 1
    # Prove the default mic actually opens (a device can be listed
    # yet fail to open — that is the crash this isolation exists for).
    try:
        import speech_recognition as sr

        with sr.Microphone():
            pass
    except Exception as exc:
        _print_json({"ok": False, "why": f"Could not open the microphone: {exc}"})
        return 1
    _print_json({"ok": True, "mics": list(names)})
    return 0


def mic_record(wav_path: str, timeout: float = 12.0,
               phrase_time_limit: float = 20.0) -> int:
    """Listen once and write the audio to a WAV file.

    Returns 0 when audio was captured, or when nothing was heard
    (heard=false — the caller says "didn't catch that"). Returns 1
    on failure, with the reason as JSON.
    """
    try:
        import speech_recognition as sr
    except ImportError as exc:
        _print_json({"ok": False, "why": f"SpeechRecognition is not installed ({exc})."})
        return 1
    recognizer = sr.Recognizer()
    recognizer.pause_threshold = 0.8
    recognizer.non_speaking_duration = 0.5
    try:
        with sr.Microphone() as source:
            recognizer.adjust_for_ambient_noise(source, duration=0.5)
            audio = recognizer.listen(
                source, timeout=timeout,
                phrase_time_limit=phrase_time_limit)
    except sr.WaitTimeoutError:
        _print_json({"ok": True, "heard": False})
        return 0
    except Exception as exc:
        _print_json({"ok": False, "why": f"Could not open the microphone: {exc}"})
        return 1
    try:
        with open(wav_path, "wb") as fh:
            fh.write(audio.get_wav_data())
    except OSError as exc:
        _print_json({"ok": False, "why": f"Could not save the recording: {exc}"})
        return 1
    _print_json({"ok": True, "heard": True})
    return 0


def mic_wake() -> int:
    """Listen forever for "Hey Aries", printing WAKE per hearing.

    Prints READY once the mic is open, then one "WAKE" line (flushed)
    each time the phrase is heard. Runs until killed by the parent.
    Returns 1 only when the mic cannot be used at all.
    """
    try:
        import speech_recognition as sr
    except ImportError as exc:
        _print_json({"ok": False, "why": f"SpeechRecognition is not installed ({exc})."})
        return 1
    recognizer = sr.Recognizer()
    recognizer.pause_threshold = 0.8
    recognizer.non_speaking_duration = 0.5
    try:
        mic = sr.Microphone()
    except Exception as exc:
        _print_json({"ok": False, "why": f"Could not open the microphone: {exc}"})
        return 1
    print("READY", flush=True)
    try:
        with mic as source:
            recognizer.adjust_for_ambient_noise(source, duration=0.5)
            while True:
                try:
                    audio = recognizer.listen(
                        source, timeout=1.0, phrase_time_limit=4.0)
                except sr.WaitTimeoutError:
                    continue
                except Exception:
                    # A transient capture error: keep listening.
                    continue
                try:
                    text = recognizer.recognize_google(
                        audio, language="en-US")
                except Exception:
                    continue
                if text and "hey aries" in text.lower():
                    print("WAKE", flush=True)
    except Exception as exc:
        _print_json({"ok": False, "why": f"Wake listener stopped: {exc}"})
        return 1
    return 0


def main(argv: list[str]) -> int:
    """Dispatch a --mic-* flag. argv is sys.argv[1:]."""
    if not argv:
        return 2
    flag = argv[0]
    if flag == "--mic-probe":
        return mic_probe()
    if flag == "--mic-record":
        if len(argv) < 2:
            _print_json({"ok": False, "why": "mic-record needs a WAV path."})
            return 2
        wav_path = argv[1]
        timeout = float(argv[2]) if len(argv) > 2 else 12.0
        phrase_limit = float(argv[3]) if len(argv) > 3 else 20.0
        return mic_record(wav_path, timeout, phrase_limit)
    if flag == "--mic-wake":
        return mic_wake()
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
