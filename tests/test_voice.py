"""Unit tests for the voice pipeline (aries/voice.py).

No microphone and no real audio device are needed: the chunked
talk-back pipeline is tested with a fake backend client and a
patched playback step, and STT is tested with a mocked
SpeechRecognition layer. The real end-to-end paths (a real MP3
from /api/tts played through pygame, and a real Google Speech
Recognition round-trip) live in test_live_backend.py (opt-in,
ARIES_RUN_LIVE=1).

Run:  python -m pytest tests/ -x -q
  or: python -m unittest discover -s tests -v
"""

import json
import os
import threading
import unittest
from unittest import mock

# Headless audio: pygame.mixer must initialise without a sound card.
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from aries.voice import VoiceEngine, VoiceError, split_sentences


class FakeBackendClient:
    """A backend client whose tts() returns canned MP3 bytes."""

    def __init__(self, chunks=None, fail_first=False):
        self.calls = []
        self.chunks = chunks or {}
        self.fail_first = fail_first
        self._failed_once = False

    def tts(self, text, voice_id=None):
        self.calls.append(text)
        if self.fail_first and not self._failed_once:
            self._failed_once = True
            raise RuntimeError("backend voice is down")
        return self.chunks.get(text, b"ID3fake-mp3:" + text.encode())


class SplitSentencesTestCase(unittest.TestCase):
    def test_simple_sentences(self):
        self.assertEqual(
            split_sentences("Hello world. How are you? I am fine!"),
            ["Hello world.", "How are you?", "I am fine!"])

    def test_single_sentence_no_period(self):
        self.assertEqual(split_sentences("Hello there"),
                         ["Hello there"])

    def test_empty_text(self):
        self.assertEqual(split_sentences(""), [])
        self.assertEqual(split_sentences("   "), [])

    def test_long_chunk_splits_on_comma(self):
        # Commas all through the text, so every split lands on a word
        # boundary (right after a comma) — nothing is lost or broken.
        long_text = ("First part, " + ", ".join(["word"] * 120)
                     + ", end of it.")
        chunks = split_sentences(long_text, max_chars=100)
        self.assertTrue(len(chunks) > 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 101)
        # Nothing is lost: the chunks rejoin to the original words.
        rejoined = " ".join(word for c in chunks for word in c.split())
        self.assertEqual(rejoined, " ".join(long_text.split()))

    def test_newlines_and_multiple_spaces(self):
        chunks = split_sentences("One.\n\nTwo.  Three.")
        self.assertEqual(chunks, ["One.", "Two.", "Three."])


class ChunkedTalkBackTestCase(unittest.TestCase):
    """The voice-delay fix: the first chunk plays before the whole
    reply is fetched, and the next chunk is prefetched while the
    current one plays."""

    def setUp(self):
        self.engine = VoiceEngine(client=None)
        self.played = []          # chunk bytes, in play order
        self.played_events = []   # threading.Event per chunk index
        self.done = threading.Event()

    def _patch_playback(self, engine, chunk_count):
        """Replace an engine's _play_mp3_blocking with a recorder.

        The patch stays active until the test's done event fires (the
        speaker thread runs after speak() returns, so a `with` block
        around speak() alone would unpatch mid-playback).
        """
        self.played_events = [threading.Event() for _ in range(chunk_count)]

        def _fake_play(mp3):
            idx = len(self.played)
            self.played.append(mp3)
            if idx < len(self.played_events):
                self.played_events[idx].set()

        patcher = mock.patch.object(
            engine, "_play_mp3_blocking", side_effect=_fake_play)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_first_chunk_plays_before_whole_reply_is_fetched(self):
        reply = "First sentence here. Second sentence here. Third one."
        chunks = split_sentences(reply)
        self.assertEqual(len(chunks), 3)
        first_played = threading.Event()

        class GatedClient(FakeBackendClient):
            def tts(self, text, voice_id=None):
                self.calls.append(text)
                if text == chunks[1]:
                    # The second chunk's fetch must not start until the
                    # first chunk is already playing. If the pipeline
                    # fetched everything up front, this waits forever.
                    if not first_played.wait(timeout=5):
                        raise AssertionError(
                            "chunk 1 was fetched before chunk 0 played — "
                            "the reply was synthesised whole, not chunked")
                return b"ID3fake-mp3:" + text.encode()

        client = GatedClient()
        engine = VoiceEngine(client=client)
        played = []

        def _fake_play(mp3):
            played.append(mp3)
            if len(played) == 1:
                first_played.set()

        with mock.patch.object(engine, "_play_mp3_blocking",
                               side_effect=_fake_play):
            started = engine.speak(reply, on_done=self.done.set)
            self.assertTrue(started, "speak() should have started")
            self.assertTrue(self.done.wait(timeout=10),
                            "talk-back did not finish in time")
        self.assertEqual(len(played), 3)
        self.assertEqual(played[0], b"ID3fake-mp3:" + chunks[0].encode())
        self.assertEqual(played[1], b"ID3fake-mp3:" + chunks[1].encode())
        self.assertEqual(played[2], b"ID3fake-mp3:" + chunks[2].encode())
        # Every chunk was fetched exactly once, in order.
        self.assertEqual(client.calls, chunks)

    def test_chunks_play_in_order_with_prefetch(self):
        reply = "One. Two. Three. Four."
        chunks = split_sentences(reply)
        client = FakeBackendClient()
        engine = VoiceEngine(client=client)
        self._patch_playback(engine, len(chunks))
        started = engine.speak(reply, on_done=self.done.set)
        self.assertTrue(started)
        self.assertTrue(self.done.wait(timeout=10))
        expected = [b"ID3fake-mp3:" + c.encode() for c in chunks]
        self.assertEqual(self.played, expected)

    def test_backend_down_falls_back_to_device_voice(self):
        reply = "Hello there. How are you?"
        client = FakeBackendClient(fail_first=True)
        engine = VoiceEngine(client=client)
        fallback = mock.Mock(return_value=True)
        with mock.patch.object(engine, "_speak_device_chunks",
                               side_effect=fallback):
            started = engine.speak(reply)
        self.assertTrue(started)
        fallback.assert_called_once()
        chunks = fallback.call_args[0][0]
        self.assertEqual(chunks, split_sentences(reply))

    def test_speak_empty_text_does_nothing(self):
        engine = VoiceEngine(client=FakeBackendClient())
        self.assertFalse(engine.speak(""))
        self.assertFalse(engine.speak("   "))

    def test_stop_speaking_sets_flag(self):
        engine = VoiceEngine(client=FakeBackendClient())
        engine.speak("One. Two. Three.")
        engine.stop_speaking()
        self.assertTrue(engine._stop_flag.is_set())


class MicAvailableTestCase(unittest.TestCase):
    def test_pyaudio_missing_reports_plain_reason(self):
        # This VM has no PyAudio: mic_available must say so plainly,
        # not crash. (On Windows, where PyAudio is installed, the
        # same function returns True when a mic exists.)
        engine = VoiceEngine(client=None)
        ok, why = engine.mic_available()
        try:
            import pyaudio  # noqa: F401
            self.skipTest("PyAudio is installed here — "
                          "this branch is the no-PyAudio one")
        except ImportError:
            pass
        self.assertFalse(ok)
        self.assertIn("PyAudio", why)


class SttWiringTestCase(unittest.TestCase):
    """STT wiring with a mocked record subprocess + recogniser
    (no mic here). 0.6.0: capture runs in a helper child process
    (--mic-record writes a WAV); this process only transcribes it."""

    def _fake_record(self, heard=True, ok=True):
        """A subprocess.run stand-in returning the record child's JSON."""
        import subprocess

        payload = {"ok": ok, "heard": heard}
        if not ok:
            payload["why"] = "Could not open the microphone: boom"
        return subprocess.CompletedProcess(
            args=["mic-record"], returncode=0 if ok else 1,
            stdout=json.dumps(payload) + "\n", stderr="")

    def test_listen_once_returns_transcribed_text(self):
        engine = VoiceEngine(client=None)
        fake_audio = object()
        with mock.patch.object(engine, "mic_available",
                               return_value=(True, "")), \
                mock.patch("aries.voice.subprocess.run",
                           return_value=self._fake_record(heard=True)), \
                mock.patch("speech_recognition.AudioFile") as audio_cls, \
                mock.patch("speech_recognition.Recognizer") as rec_cls:
            recognizer = rec_cls.return_value
            recognizer.record.return_value = fake_audio
            recognizer.recognize_google.return_value = "open notepad"
            audio_cls.return_value.__enter__.return_value = object()
            text = engine.listen_once()
        self.assertEqual(text, "open notepad")
        recognizer.record.assert_called_once()
        recognizer.recognize_google.assert_called_once_with(
            fake_audio, language="en-US")

    def test_listen_once_nothing_heard_returns_none(self):
        # The record child heard nothing (timeout in the child) — the
        # caller says "didn't catch that", it is not an error.
        engine = VoiceEngine(client=None)
        with mock.patch.object(engine, "mic_available",
                               return_value=(True, "")), \
                mock.patch("aries.voice.subprocess.run",
                           return_value=self._fake_record(heard=False)):
            text = engine.listen_once()
        self.assertIsNone(text)

    def test_unintelligible_audio_returns_none(self):
        import speech_recognition as sr

        engine = VoiceEngine(client=None)
        with mock.patch.object(engine, "mic_available",
                               return_value=(True, "")), \
                mock.patch("aries.voice.subprocess.run",
                           return_value=self._fake_record(heard=True)), \
                mock.patch("speech_recognition.AudioFile") as audio_cls, \
                mock.patch("speech_recognition.Recognizer") as rec_cls:
            recognizer = rec_cls.return_value
            recognizer.record.return_value = object()
            recognizer.recognize_google.side_effect = sr.UnknownValueError()
            audio_cls.return_value.__enter__.return_value = object()
            text = engine.listen_once()
        self.assertIsNone(text)

    def test_record_child_failure_raises_voice_error(self):
        engine = VoiceEngine(client=None)
        with mock.patch.object(engine, "mic_available",
                               return_value=(True, "")), \
                mock.patch("aries.voice.subprocess.run",
                           return_value=self._fake_record(ok=False)):
            with self.assertRaises(VoiceError) as ctx:
                engine.listen_once()
        self.assertIn("microphone", str(ctx.exception).lower())

    def test_mic_unavailable_raises_voice_error(self):
        engine = VoiceEngine(client=None)
        with mock.patch.object(engine, "mic_available",
                               return_value=(False, "No mic here")):
            with self.assertRaises(VoiceError) as ctx:
                engine.listen_once()
        self.assertIn("No mic here", str(ctx.exception))

    def test_transcribe_audio_data(self):
        engine = VoiceEngine(client=None)
        with mock.patch("speech_recognition.Recognizer") as rec_cls:
            rec_cls.return_value.recognize_google.return_value = \
                "hello aries"
            out = engine.transcribe_audio_data(object())
        self.assertEqual(out, "hello aries")


if __name__ == "__main__":
    unittest.main()
