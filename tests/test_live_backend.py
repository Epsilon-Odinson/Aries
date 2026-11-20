"""Live tests against the real Render backend — OPT-IN ONLY.

These hit https://aries-ahvo.onrender.com and Google Speech
Recognition, so they need the internet and they spend a few
ElevenLabs characters (a short sentence). They are skipped unless
ARIES_RUN_LIVE=1 is set.

Covers what mocks cannot: a real MP3 comes back from /api/tts and
plays through pygame, and a real Google Speech Recognition
round-trip transcribes that same audio.

Run:  ARIES_RUN_LIVE=1 python -m pytest tests/test_live_backend.py -q
"""

import os
import subprocess
import tempfile
import unittest

os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

RUN_LIVE = os.getenv("ARIES_RUN_LIVE") == "1"


@unittest.skipUnless(RUN_LIVE, "live tests need ARIES_RUN_LIVE=1")
class LiveBackendTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from aries.api_client import BackendClient
        cls.client = BackendClient()
        cls.voice = None

    def test_health_and_state_shape(self):
        health = self.client.health()
        self.assertIn("key_configured", health)
        state = self.client.state()
        for key in ("profile", "personality", "memories", "tasks",
                    "permissions", "face", "messages", "memory_file"):
            self.assertIn(key, state, f"/api/state is missing {key}")

    def test_tts_returns_playable_mp3(self):
        """A real MP3 from /api/tts, played through pygame."""
        from aries.voice import VoiceEngine

        mp3 = self.client.tts("Hello, this is Aries.", voice_id=None)
        self.assertTrue(len(mp3) > 1000, "MP3 suspiciously small")
        self.assertTrue(mp3[:3] == b"ID3" or mp3[:2] == b"\xff\xfb",
                        "response does not look like MP3 audio")
        engine = VoiceEngine(client=self.client)
        # Play it for real (dummy audio driver — no sound card needed).
        # _play_mp3_blocking returns only when playback ends.
        engine._play_mp3_blocking(mp3)
        self.assertTrue(engine._ensure_pygame(),
                        "pygame mixer did not initialise")

    def test_real_stt_round_trip(self):
        """The TTS audio, transcribed back by Google Speech Recognition."""
        import speech_recognition as sr
        from aries.voice import VoiceEngine

        mp3 = self.client.tts(
            "Hello, this is a test of the Aries voice.", voice_id=None)
        with tempfile.TemporaryDirectory() as tmp:
            mp3_path = os.path.join(tmp, "tts.mp3")
            wav_path = os.path.join(tmp, "tts.wav")
            with open(mp3_path, "wb") as f:
                f.write(mp3)
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", mp3_path,
                 "-ar", "16000", "-ac", "1", wav_path],
                check=True)
            with sr.AudioFile(wav_path) as source:
                audio = sr.Recognizer().record(source)
            engine = VoiceEngine(client=self.client)
            text = engine.transcribe_audio_data(audio)
        self.assertIsNotNone(text, "Google could not transcribe the audio")
        lowered = text.lower()
        self.assertTrue(
            "hello" in lowered or "test" in lowered or "aries" in lowered,
            f"transcription does not match the spoken words: {text!r}")


if __name__ == "__main__":
    unittest.main()
