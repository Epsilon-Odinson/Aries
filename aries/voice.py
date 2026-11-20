"""Voice for the Aries desktop app: talk and listen, like the web app.

Speech-to-text: SpeechRecognition + the microphone (PyAudio) with
Google Speech Recognition — free, no key, works over the internet.
That is the fewest-moving-parts choice that actually works on
Windows: PyAudio ships prebuilt wheels for Python 3.11, and the
Google recogniser needs no account. (Local Whisper would need a
~150 MB model download plus torch — heavier, slower to start, and
no better for short commands — so it is not used.)

Talk-back: the backend /api/tts (ElevenLabs turbo model, real
narrator voice, MP3) played with pygame — CHUNKED with prefetch,
the voice-delay fix: the reply is split into sentence chunks, the
first chunk plays as soon as it is ready, and the next is prefetched
while it plays, so there is no gap and no wait for the whole reply.
When the backend voice is not available, the fallback is the
device's own voices via pyttsx3 (Windows SAPI) — the desktop
equivalent of the web app's browser-voice fallback.

Wake phrase: "Hey Aries" is listened for in the background with
speech_recognition's listen_in_background. It only runs while the
Wake phrase toggle is on.

Nothing here touches a widget. The UI runs the blocking calls
(listen_once, speak) from QThreads and marshals results back with
signals. When a dependency is missing (no PyAudio, no mic, no
pygame) the methods raise VoiceError with a plain-words message
instead of crashing — the UI then says so and offers typing.
"""

from __future__ import annotations

import io
import os
import tempfile
import threading

from .api_client import BackendClient, VoiceNotConfigured


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


def _mic_available() -> tuple[bool, str]:
    """True when SpeechRecognition + a microphone can be used."""
    try:
        import speech_recognition as sr
    except ImportError:
        return False, "SpeechRecognition is not installed."
    try:
        import pyaudio  # noqa: F401  (sr.Microphone needs it)
    except ImportError:
        return False, (
            "PyAudio is not installed, so the microphone cannot be used. "
            "Install it with: pip install -r requirements.txt"
        )
    try:
        names = sr.Microphone.list_microphone_names()
    except Exception as exc:
        return False, f"Could not list microphones: {exc}"
    if not names:
        return False, "No microphone was found on this PC."
    return True, ""


class VoiceEngine:
    """Speech in, speech out. Blocking calls — run them off the UI thread."""

    def __init__(self, client: BackendClient | None = None) -> None:
        self.client = client
        self._pygame_ready = False
        self._pygame_failed = False
        self._speaking = False
        self._speak_lock = threading.Lock()
        self._stop_flag = threading.Event()
        self._wake_stop = None  # stop function from listen_in_background
        self._wake_recognizer = None
        self._wake_mic = None
        self._pyttsx3_engine = None
        self._device_voice_id: str | None = None

    # -- availability -----------------------------------------------------
    def mic_available(self) -> tuple[bool, str]:
        return _mic_available()

    # -- speech-to-text -----------------------------------------------------
    def listen_once(self, timeout: float = 12.0,
                    phrase_time_limit: float = 20.0) -> str | None:
        """Listen once and return the transcribed text.

        Returns None when nothing was heard / nothing was understood
        (the caller says "didn't catch that"). Raises VoiceError when
        the mic or the network is the problem.
        """
        ok, why = self.mic_available()
        if not ok:
            raise VoiceError(why)
        import speech_recognition as sr

        recognizer = sr.Recognizer()
        # Shorter pauses: the user talks in short bursts.
        recognizer.pause_threshold = 0.8
        recognizer.non_speaking_duration = 0.5
        try:
            with sr.Microphone() as source:
                recognizer.adjust_for_ambient_noise(source, duration=0.5)
                audio = recognizer.listen(
                    source, timeout=timeout,
                    phrase_time_limit=phrase_time_limit)
        except sr.WaitTimeoutError:
            return None
        except OSError as exc:
            raise VoiceError(
                f"Could not open the microphone: {exc}. Check the mic "
                "is plugged in and no other app is using it."
            ) from exc
        return self._transcribe(recognizer, audio)

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
        (from the recogniser thread) each time the phrase is heard.
        Call stop_wake() to end it. Raises VoiceError when the mic is
        not usable."""
        if self._wake_stop is not None:
            return  # Already listening.
        ok, why = self.mic_available()
        if not ok:
            raise VoiceError(why)
        import speech_recognition as sr

        recognizer = sr.Recognizer()
        recognizer.pause_threshold = 0.8
        mic = sr.Microphone()
        self._wake_recognizer = recognizer
        self._wake_mic = mic

        def _callback(_rec, audio):
            try:
                text = self._transcribe(recognizer, audio)
            except VoiceError:
                return
            if text and "hey aries" in text.lower():
                try:
                    on_wake()
                except Exception:
                    pass

        # listen_in_background spawns its own thread and returns a
        # stop function. It keeps listening until that is called.
        self._wake_stop = recognizer.listen_in_background(mic, _callback)

    def stop_wake(self) -> None:
        if self._wake_stop is not None:
            try:
                self._wake_stop(wait_for_stop=False)
            except Exception:
                pass
            self._wake_stop = None
        self._wake_recognizer = None
        self._wake_mic = None

    def wake_active(self) -> bool:
        return self._wake_stop is not None

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
