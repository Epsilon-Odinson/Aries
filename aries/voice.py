"""Voice for the Aries desktop app: talk and listen, like the web app.

Speech-to-text: SpeechRecognition + the microphone (PyAudio) with
Google Speech Recognition — free, no key, works over the internet.
The microphone NEVER opens in this process: capture runs in a
helper child process (aries/mic_worker.py, re-invoking the app with
a hidden --mic-* flag) that writes a WAV file, and this process
only transcribes that file. A bad audio driver used to segfault the
whole app the moment the mic was listed or opened — now it can
only kill the child, and the app reports "the mic isn't available"
in plain words and stays open. (Local Whisper would need a ~150 MB
model download plus torch — heavier, slower to start, and no better
for short commands — so it is not used.)

Talk-back: the backend /api/tts (ElevenLabs turbo model, real
narrator voice, MP3) played with pygame — CHUNKED with prefetch,
the voice-delay fix: the reply is split into sentence chunks, the
first chunk plays as soon as it is ready, and the next is prefetched
while it plays, so there is no gap and no wait for the whole reply.
When the backend voice is not available, the fallback is the
device's own voices via pyttsx3 (Windows SAPI) — the desktop
equivalent of the web app's browser-voice fallback.

Wake phrase: "Hey Aries" is listened for by a helper child process
(aries/mic_worker.py --mic-wake) that prints WAKE each time it
hears the phrase; this process only reads the child's stdout. It
only runs while the Wake phrase toggle is on.

Nothing here touches a widget. The UI runs the blocking calls
(listen_once, speak) from QThreads and marshals results back with
signals. When a dependency is missing (no PyAudio, no mic, no
pygame) the methods raise VoiceError with a plain-words message
instead of crashing — the UI then says so and offers typing.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

from .api_client import BackendClient, VoiceNotConfigured

# How long a successful mic probe stays trusted (the probe spawns a
# child process, so re-probing on every tap would feel slow).
_PROBE_CACHE_SECONDS = 60.0
# Hard cap for the probe child (Python start + PortAudio init is
# normally 1-2 s; a bad driver can hang, so never wait forever).
_PROBE_TIMEOUT = 25.0


def _mic_cmd(*args: str) -> list[str]:
    """Command that runs this app's mic helper in a child process.

    In dev it is ``python -m aries.main <flag>``; in the frozen .exe
    the exe re-invokes itself with the flag. Either way the child is
    the same code (aries/mic_worker.py) — the parent never touches
    PortAudio, so a native audio crash can only kill the child.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return [sys.executable, "-m", "aries.main", *args]


def _run_mic_probe() -> tuple[bool, str]:
    """Probe the mic in a child process. (ok, reason).

    The child lists microphones and opens the default one. If it
    crashes (a native PortAudio fault) or times out, the mic is
    reported unavailable — the main app never sees the crash.
    """
    try:
        proc = subprocess.run(
            _mic_cmd("--mic-probe"),
            capture_output=True, text=True, timeout=_PROBE_TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, (
            "The microphone did not answer in time — the audio driver "
            "may be stuck. Try again, or type your message.")
    except OSError as exc:
        return False, f"Could not start the microphone helper: {exc}"
    out = (proc.stdout or "").strip()
    try:
        data = json.loads(out.splitlines()[-1]) if out else {}
    except (ValueError, IndexError):
        data = {}
    if proc.returncode == 0 and data.get("ok"):
        return True, ""
    why = str(data.get("why") or "").strip()
    if not why:
        why = (
            "The microphone could not be opened on this PC. Check it "
            "is plugged in and no other app is using it.")
    return False, why


# Last good probe, so tapping the mic twice does not re-spawn.
_probe_cache: dict = {"at": 0.0, "ok": False, "why": ""}


def _mic_available() -> tuple[bool, str]:
    """True when a microphone can be used (probed in a child process).

    The main process never imports PyAudio or opens a mic — that is
    the whole point: a bad audio driver can no longer kill the app.
    """
    now = time.monotonic()
    if now - _probe_cache.get("at", 0.0) < _PROBE_CACHE_SECONDS:
        return _probe_cache["ok"], _probe_cache["why"]
    ok, why = _run_mic_probe()
    # Only cache a success (a failure may clear up — a plugged-in
    # mic, a freed device — so the next tap re-probes).
    if ok:
        _probe_cache.update({"at": now, "ok": True, "why": ""})
    return ok, why


class VoiceError(RuntimeError):
    """A plain-words voice failure (mic missing, no internet, ...)."""


def split_sentences(text: str, max_chars: int = 400) -> list[str]:
    """Split a reply into sentence chunks for chunked talk-back.

    Splits on sentence-ending punctuation (. ! ?) and then splits any
    chunk still over max_chars at a comma/semicolon/colon. Short
    chunks mean the first audio starts fast (the turbo model is quick
    per chunk) and the next chunk can be prefetched while the current
    one plays — the same approach as the web app. Never returns an
    empty list for non-empty text."""
    import re

    text = (text or "").strip()
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+", text)
    out: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        while len(part) > max_chars:
            cut = max(
                part.rfind(",", 0, max_chars),
                part.rfind(";", 0, max_chars),
                part.rfind(":", 0, max_chars),
            )
            if cut <= 0:
                cut = max_chars
            out.append(part[: cut + 1].strip())
            part = part[cut + 1:].strip()
        if part:
            out.append(part)
    return out or [text]


class VoiceEngine:
    """Speech in, speech out. Blocking calls — run them off the UI thread.

    The microphone NEVER opens in this process: capture runs in a
    helper child process (aries/mic_worker.py) so a bad audio driver
    cannot kill the app. Transcription (Google, from a WAV file) and
    talk-back (pygame / pyttsx3) are safe in-process.
    """

    def __init__(self, client: BackendClient | None = None) -> None:
        self.client = client
        self._pygame_ready = False
        self._pygame_failed = False
        self._speaking = False
        self._speak_lock = threading.Lock()
        self._stop_flag = threading.Event()
        self._wake_proc = None  # subprocess.Popen for --mic-wake
        self._wake_reader = None  # thread reading the wake child's stdout
        self._pyttsx3_engine = None
        self._device_voice_id: str | None = None

    # -- availability -----------------------------------------------------
    def mic_available(self) -> tuple[bool, str]:
        return _mic_available()

    # -- speech-to-text -----------------------------------------------------
    def listen_once(self, timeout: float = 12.0,
                    phrase_time_limit: float = 20.0) -> str | None:
        """Listen once and return the transcribed text.

        Capture runs in a helper child process (mic_worker
        --mic-record) that writes a WAV file; this process only
        transcribes that file with Google — no PortAudio here, so a
        bad audio driver can never kill the app. Returns None when
        nothing was heard / nothing was understood (the caller says
        "didn't catch that"). Raises VoiceError when the mic or the
        network is the problem.
        """
        ok, why = self.mic_available()
        if not ok:
            raise VoiceError(why)
        import speech_recognition as sr

        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        wav_path = tmp.name
        tmp.close()
        try:
            record_timeout = timeout + phrase_time_limit + 20.0
            try:
                proc = subprocess.run(
                    _mic_cmd("--mic-record", wav_path,
                             str(timeout), str(phrase_time_limit)),
                    capture_output=True, text=True,
                    timeout=record_timeout)
            except subprocess.TimeoutExpired:
                raise VoiceError(
                    "Listening took too long — the microphone did not "
                    "answer. Try again, or type your message.") from None
            except OSError as exc:
                raise VoiceError(
                    f"Could not start the microphone helper: {exc}") from exc
            out = (proc.stdout or "").strip()
            try:
                data = json.loads(out.splitlines()[-1]) if out else {}
            except (ValueError, IndexError):
                data = {}
            if proc.returncode != 0 or not data.get("ok"):
                raise VoiceError(
                    str(data.get("why") or
                        "The microphone could not be opened on this PC. "
                        "Check it is plugged in and no other app is "
                        "using it."))
            if not data.get("heard"):
                return None
            recognizer = sr.Recognizer()
            try:
                with sr.AudioFile(wav_path) as source:
                    audio = recognizer.record(source)
            except Exception as exc:
                raise VoiceError(
                    f"Could not read the recording: {exc}") from exc
            return self._transcribe(recognizer, audio)
        finally:
            try:
                os.unlink(wav_path)
            except OSError:
                pass

    def _transcribe(self, recognizer, audio) -> str | None:
        """Google Speech Recognition on captured audio."""
        import speech_recognition as sr

        try:
            text = recognizer.recognize_google(audio, language="en-US")
        except sr.UnknownValueError:
            return None  # Heard something, could not make out words.
        except sr.RequestError as exc:
            raise VoiceError(
                "Speech recognition needs the internet and could not "
                f"reach Google: {exc}. Check the connection and try again."
            ) from exc
        return (text or "").strip() or None

    def transcribe_audio_data(self, audio) -> str | None:
        """Transcribe an sr.AudioData object (used by the wake listener
        and by tests, which feed a recorded file instead of a mic)."""
        import speech_recognition as sr

        return self._transcribe(sr.Recognizer(), audio)

    # -- wake phrase ---------------------------------------------------------
    def start_wake(self, on_wake) -> None:
        """Listen in the background for "Hey Aries". on_wake() is called
        (from the reader thread) each time the phrase is heard. Call
        stop_wake() to end it. Raises VoiceError when the mic is not
        usable.

        The listener is a helper child process (--mic-wake) that
        prints WAKE per hearing; this process only reads its stdout.
        No PortAudio runs here, so a bad driver cannot kill the app.
        """
        if self._wake_proc is not None:
            return  # Already listening.
        ok, why = self.mic_available()
        if not ok:
            raise VoiceError(why)
        try:
            proc = subprocess.Popen(
                _mic_cmd("--mic-wake"),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1)
        except OSError as exc:
            raise VoiceError(
                f"Could not start the microphone helper: {exc}") from exc
        self._wake_proc = proc

        def _reader():
            try:
                for line in proc.stdout:
                    if line.strip() == "WAKE":
                        try:
                            on_wake()
                        except Exception:
                            pass
            except Exception:
                pass

        thread = threading.Thread(target=_reader, daemon=True)
        thread.start()
        self._wake_reader = thread

    def stop_wake(self) -> None:
        if self._wake_proc is None:
            return
        proc = self._wake_proc
        self._wake_proc = None
        self._wake_reader = None
        try:
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
        except Exception:
            pass

    def wake_active(self) -> bool:
        return self._wake_proc is not None

    # -- talk-back -------------------------------------------------------------
    def _ensure_pygame(self) -> bool:
        """Initialise pygame.mixer once. False when it cannot start."""
        if self._pygame_ready:
            return True
        if self._pygame_failed:
            return False
        try:
            import pygame
            pygame.mixer.init()
            self._pygame_ready = True
            return True
        except Exception:
            self._pygame_failed = True
            return False

    def speak(self, text: str, voice_id: str | None = None,
              on_done=None) -> bool:
        """Speak text, non-blocking, CHUNKED with prefetch (the voice-
        delay fix, same as the web app).

        The reply is split into sentence chunks. The first chunk is
        synthesised (backend /api/tts, ElevenLabs turbo model) and
        played as soon as it is ready — the whole reply is NEVER
        synthesised before anything plays. While one chunk plays, the
        next is prefetched in the background, so there is no gap
        between chunks. Falls back to the device voice (pyttsx3 /
        Windows SAPI, also chunked) when the backend voice is not
        available. on_done() is called (from the speaker thread) when
        the whole reply has been spoken or was stopped. Returns True
        when something started speaking, False when nothing could.
        Never raises."""
        text = (text or "").strip()
        if not text:
            return False
        self._stop_flag.clear()
        chunks = split_sentences(text)
        # Backend ElevenLabs first (turbo model, chunked + prefetch).
        if self.client is not None:
            if self._speak_chunked_backend(chunks, voice_id, on_done):
                return True
        # Fallback: the device's own voice, also chunked.
        return self._speak_device_chunks(chunks, on_done)

    def _speak_chunked_backend(self, chunks: list[str],
                               voice_id: str | None,
                               on_done) -> bool:
        """Chunked ElevenLabs pipeline: a producer thread fetches each
        chunk's MP3 into a small queue while the speaker thread plays
        them in order. The first chunk plays as soon as it arrives;
        the next is prefetched while it plays. True when the pipeline
        started (at least the first fetch was attempted and pygame is
        up)."""
        if not self._ensure_pygame():
            return False
        import queue as _queue

        try:
            import pygame
        except ImportError:
            return False

        # Fetch the FIRST chunk synchronously: if the backend voice is
        # down this fails fast and the caller falls back to the device
        # voice instead of starting a pipeline that speaks nothing.
        try:
            first = self.client.tts(chunks[0], voice_id)
        except Exception:
            return False
        if not first:
            return False

        ready = _queue.Queue(maxsize=2)
        ready.put((0, first))

        def _producer():
            for i in range(1, len(chunks)):
                if self._stop_flag.is_set():
                    break
                try:
                    mp3 = self.client.tts(chunks[i], voice_id)
                except Exception:
                    mp3 = None
                ready.put((i, mp3))
            ready.put((len(chunks), None))  # end marker

        threading.Thread(target=_producer, daemon=True).start()

        def _run():
            with self._speak_lock:
                self._speaking = True
            try:
                # Chunk 0 is already fetched; the rest arrive via the
                # queue while each chunk plays.
                pending = {0: first}
                for i in range(len(chunks)):
                    if self._stop_flag.is_set():
                        break
                    if i not in pending:
                        # Wait for the prefetched chunk (it is usually
                        # already there — it was fetched while the
                        # previous chunk played).
                        while True:
                            idx, mp3 = ready.get()
                            if idx == len(chunks):
                                mp3 = None
                                break
                            pending[idx] = mp3
                            if idx == i:
                                break
                    mp3 = pending.pop(i, None)
                    if mp3:
                        self._play_mp3_blocking(mp3)
            finally:
                with self._speak_lock:
                    self._speaking = False
                if on_done is not None:
                    try:
                        on_done()
                    except Exception:
                        pass

        threading.Thread(target=_run, daemon=True).start()
        return True

    def _play_mp3_blocking(self, mp3: bytes) -> None:
        """Play one MP3 chunk with pygame and wait until it ends (or
        the stop flag is set). Called from the speaker thread."""
        import pygame

        try:
            tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            tmp.write(mp3)
            tmp.close()
            path = tmp.name
        except OSError:
            return
        try:
            pygame.mixer.music.load(path)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                if self._stop_flag.is_set():
                    pygame.mixer.music.stop()
                    break
                pygame.time.wait(80)
        except Exception:
            pass
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def _play_mp3(self, mp3: bytes) -> bool:
        """Play a single MP3 (used by voice previews). True when
        playback started."""
        if not self._ensure_pygame():
            return False
        try:
            import pygame  # noqa: F401
        except ImportError:
            return False
        self._stop_flag.clear()

        def _run():
            with self._speak_lock:
                self._speaking = True
            try:
                self._play_mp3_blocking(mp3)
            finally:
                with self._speak_lock:
                    self._speaking = False

        threading.Thread(target=_run, daemon=True).start()
        return True

    def _speak_device(self, text: str) -> bool:
        """Speak with the device's built-in voice (pyttsx3 / SAPI),
        chunked the same way. Runs in a daemon thread. True when it
        started."""
        return self._speak_device_chunks(split_sentences(text), None)

    def _speak_device_chunks(self, chunks: list[str], on_done) -> bool:
        """Device-voice talk-back, chunked: each sentence is queued
        with say() and one runAndWait() speaks them in order, so the
        reply flows without gaps. True when it started."""
        try:
            import pyttsx3
        except ImportError:
            return False
        self._stop_flag.clear()

        def _run():
            with self._speak_lock:
                self._speaking = True
            try:
                engine = pyttsx3.init()
                if self._device_voice_id:
                    try:
                        engine.setProperty("voice", self._device_voice_id)
                    except Exception:
                        pass
                engine.setProperty("rate", 175)
                for chunk in chunks:
                    if self._stop_flag.is_set():
                        break
                    engine.say(chunk)
                if not self._stop_flag.is_set():
                    engine.runAndWait()
                else:
                    try:
                        engine.stop()
                    except Exception:
                        pass
            except Exception:
                pass
            finally:
                with self._speak_lock:
                    self._speaking = False
                if on_done is not None:
                    try:
                        on_done()
                    except Exception:
                        pass

        threading.Thread(target=_run, daemon=True).start()
        return True

    def is_speaking(self) -> bool:
        with self._speak_lock:
            return self._speaking

    def stop_speaking(self) -> None:
        """Stop any talk-back in progress."""
        self._stop_flag.set()
        try:
            import pygame
            if self._pygame_ready:
                pygame.mixer.music.stop()
        except Exception:
            pass

    # -- device voices (for the voice picker) --------------------------------
    def device_voices(self) -> list[dict]:
        """Voices on this device, via pyttsx3 (Windows SAPI). Returns
        [{id, name}] — empty when pyttsx3 is missing or has none."""
        try:
            import pyttsx3
        except ImportError:
            return []
        try:
            engine = pyttsx3.init()
            out = []
            for v in engine.getProperty("voices") or []:
                out.append({"id": v.id, "name": v.name})
            try:
                engine.stop()
            except Exception:
                pass
            return out
        except Exception:
            return []

    def set_device_voice(self, voice_id: str | None) -> None:
        """Pick a device voice by id (None = the device default)."""
        self._device_voice_id = voice_id or None

    def preview_device_voice(self, voice_id: str, text: str) -> bool:
        """Speak one phrase with a chosen device voice (for Hear)."""
        old = self._device_voice_id
        self._device_voice_id = voice_id
        ok = self._speak_device(text)
        self._device_voice_id = old
        return ok

    def preview_eleven_voice(self, voice_id: str, text: str) -> bool:
        """Speak one phrase with a chosen ElevenLabs voice (for Hear)."""
        if self.client is None:
            return False
        try:
            mp3 = self.client.tts(text, voice_id)
        except Exception:
            return False
        return self._play_mp3(mp3)

    # -- helpers ----------------------------------------------------------------
    def mp3_bytes(self, text: str, voice_id: str | None = None) -> bytes | None:
        """Fetch TTS audio without playing it (used by tests)."""
        if self.client is None:
            return None
        try:
            return self.client.tts(text, voice_id)
        except Exception:
            return None
