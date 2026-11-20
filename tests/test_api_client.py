"""Unit tests for the Aries backend client (aries/api_client.py).

The live backend is NOT called here — requests.Session.request is
mocked with canned responses. Live behaviour is covered by
test_live_backend.py (opt-in, ARIES_RUN_LIVE=1).

Run:  python -m pytest tests/ -x -q
  or: python -m unittest discover -s tests -v
"""

import json
import unittest
from unittest import mock

import requests

from aries.api_client import (
    BackendClient,
    BackendError,
    BackendUnreachable,
    VoiceNotConfigured,
    looks_current,
)


class FakeResponse:
    """A minimal stand-in for requests.Response."""

    def __init__(self, status=200, payload=None, content=b"",
                 headers=None, url=""):
        self.status_code = status
        self._payload = payload
        self.content = content
        self.headers = headers or {}
        self.url = url
        self.text = (json.dumps(payload) if payload is not None
                     else content.decode("utf-8", "replace"))

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    def json(self):
        if self._payload is None:
            raise ValueError("no JSON here")
        return self._payload


class ClientTestCase(unittest.TestCase):
    def setUp(self):
        self.client = BackendClient("https://example.invalid")
        self.calls = []

    def _mock(self, response=None, side_effect=None):
        """Patch Session.request to return `response` (or raise)."""
        def _fake(method, url, timeout=None, **kwargs):
            self.calls.append((method, url, kwargs))
            if side_effect is not None:
                raise side_effect
            return response

        patcher = mock.patch.object(
            self.client._session, "request", side_effect=_fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return _fake

    # -- looks_current ---------------------------------------------------
    def test_looks_current_true(self):
        for q in (
            "What is the weather today?",
            "What is the latest news?",
            "How much is the A17 right now?",
            "When is the next CODM update?",
            "What is the score?",
            "What is the price of the GT 50 Pro?",
            "What happened yesterday?",
            "What is new this week?",
            "Any 2026 releases?",
            "Is it current?",
            "When does it release?",
        ):
            with self.subTest(q=q):
                self.assertTrue(looks_current(q), q)

    def test_looks_current_false(self):
        for q in (
            "What is the function of the heart?",
            "Teach me about the cell membrane",
            "How do I say hello in French?",
            "Tell me a joke",
            "",
            "Remind me to study at 19:30",
        ):
            with self.subTest(q=q):
                self.assertFalse(looks_current(q), q)

    # -- chat --------------------------------------------------------------
    def test_chat_posts_message_and_flags(self):
        self._mock(FakeResponse(200, {
            "reply": "Sunny today.",
            "sources": [{"title": "Weather", "url": "https://x.test/"}],
            "searched": True, "pending_memory": None, "task_added": None,
            "needs_permission": None}))
        out = self.client.chat("weather today?", use_search=True,
                               consent={"web_search": True})
        self.assertEqual(out["reply"], "Sunny today.")
        self.assertEqual(len(out["sources"]), 1)
        method, url, kwargs = self.calls[0]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/api/chat"))
        body = json.loads(kwargs["json"]) if isinstance(
            kwargs.get("json"), str) else kwargs.get("json")
        self.assertEqual(body["message"], "weather today?")
        self.assertTrue(body["use_search"])
        self.assertEqual(body["consent"], {"web_search": True})

    def test_chat_needs_permission(self):
        self._mock(FakeResponse(200, {
            "reply": "", "sources": [], "searched": False,
            "pending_memory": None, "task_added": None,
            "needs_permission": "web_search"}))
        out = self.client.chat("weather?")
        self.assertEqual(out["needs_permission"], "web_search")

    # -- state / health ------------------------------------------------------
    def test_state_and_health(self):
        self._mock(FakeResponse(200, {
            "profile": {"name": "Epsilon"},
            "messages": [{"role": "user", "content": "hi"}],
            "permissions": {"web_search": "ask"},
            "face": {"enrolled": False},
            "memories": [{"id": 1, "text": "Epsilon likes piano",
                          "pinned": False, "pending": False}],
            "tasks": [{"id": 1, "title": "Study anatomy", "done": False}],
            "memory_file": "Epsilon likes piano.\n"}))
        state = self.client.state()
        self.assertEqual(state["profile"]["name"], "Epsilon")
        self.assertEqual(len(state["messages"]), 1)
        # Memories and tasks come from /api/state, not separate list
        # endpoints — that is how the desktop Settings reads them.
        self.assertEqual(len(state["memories"]), 1)
        self.assertEqual(len(state["tasks"]), 1)

    def test_is_reachable_true_and_false(self):
        self._mock(FakeResponse(200, {"ok": True}))
        self.assertTrue(self.client.is_reachable())
        self._mock(side_effect=requests.exceptions.ConnectionError("down"))
        self.assertFalse(self.client.is_reachable())

    # -- memories --------------------------------------------------------------
    def test_memories_crud(self):
        self._mock(FakeResponse(200, {"ok": True, "id": 2}))
        out = self.client.add_memory("New fact")
        self.assertEqual(out["id"], 2)
        method, url, kwargs = self.calls[-1]
        self.assertEqual(json.loads(kwargs["json"])["text"]
                         if isinstance(kwargs.get("json"), str)
                         else kwargs["json"]["text"], "New fact")

        self._mock(FakeResponse(200, {"ok": True}))
        self.assertEqual(self.client.approve_memory(2), {"ok": True})
        self.assertEqual(self.client.pin_memory(2), {"ok": True})
        self.assertEqual(self.client.delete_memory(2), {"ok": True})

    # -- tasks ------------------------------------------------------------------
    def test_tasks_crud(self):
        self._mock(FakeResponse(200, {"ok": True, "id": 3}))
        self.assertEqual(self.client.add_task("Buy RAM")["id"], 3)

        self._mock(FakeResponse(200, {"ok": True}))
        self.assertEqual(self.client.toggle_task(3), {"ok": True})
        self.assertEqual(self.client.mark_reminded(3), {"ok": True})
        self.assertEqual(self.client.delete_task(3), {"ok": True})

        self._mock(FakeResponse(200, []))
        self.assertEqual(self.client.due_tasks(), [])

    # -- profile / personality / permissions -------------------------------------
    def test_profile_personality_permissions(self):
        self._mock(FakeResponse(200, {"ok": True}))
        self.assertEqual(
            self.client.save_profile(name="Epsilon"), {"ok": True})
        self.assertEqual(
            self.client.save_personality(voice="mentor", warmth=80,
                                         directness=60, humour=40,
                                         notes=""), {"ok": True})
        self.assertEqual(
            self.client.save_permission("web_search", "always"),
            {"ok": True})
        self.assertEqual(
            self.client.permissions(),
            {"ok": True})  # last mocked payload reused

    def test_clear_chat(self):
        self._mock(FakeResponse(200, {"ok": True}))
        self.assertEqual(self.client.clear_chat(), {"ok": True})

    # -- voices / tts --------------------------------------------------------------
    def test_voices_and_tts(self):
        self._mock(FakeResponse(200, {"voices": [
            {"voice_id": "abc", "name": "Adam"}]}))
        self.assertEqual(len(self.client.voices()["voices"]), 1)

        self._mock(FakeResponse(200, content=b"ID3fake-mp3-bytes"))
        mp3 = self.client.tts("Hello.", voice_id="abc")
        self.assertEqual(mp3, b"ID3fake-mp3-bytes")
        method, url, kwargs = self.calls[-1]
        self.assertTrue(url.endswith("/api/tts"))

    def test_tts_503_is_voice_not_configured(self):
        self._mock(FakeResponse(503, {"detail": "no voice key"}))
        with self.assertRaises(VoiceNotConfigured):
            self.client.tts("Hello.")

    # -- connections ------------------------------------------------------------------
    def test_connections_and_connect_url(self):
        self._mock(FakeResponse(200, {
            "google": {"name": "Gmail", "connected": False,
                       "configured": False},
            "spotify": {"name": "Spotify", "connected": False,
                        "configured": False},
            "whatsapp": {"name": "WhatsApp", "connected": False,
                         "note": "no one-tap OAuth"}}))
        conns = self.client.connections()
        self.assertIn("google", conns)
        self.assertIn("whatsapp", conns)

        self._mock(FakeResponse(
            302, headers={"Location": "https://accounts.google.com/o/oauth2/auth?x=1"}))
        url = self.client.connect_url("google")
        self.assertTrue(url.startswith("https://accounts.google.com/"))

        self._mock(FakeResponse(200, {"ok": True}))
        self.assertEqual(self.client.disconnect("google"), {"ok": True})

    # -- face / screenshot (multipart) + PIN (JSON) ------------------------------
    def test_face_and_screenshot_uploads(self):
        with mock.patch.object(
                self.client._session, "post",
                return_value=FakeResponse(200, {"match": True})) as post:
            out = self.client.verify_face(b"fake-jpg", "face.jpg")
            self.assertTrue(out["match"])
            args, kwargs = post.call_args
            self.assertTrue(args[0].endswith("/api/face/verify"))
            self.assertEqual(kwargs["files"]["file"][0], "face.jpg")

        # set_pin is a JSON call (session.request), not a multipart upload.
        self._mock(FakeResponse(200, {"ok": True}))
        self.assertEqual(self.client.set_pin("1234"), {"ok": True})
        method, url, kwargs = self.calls[-1]
        self.assertTrue(url.endswith("/api/face/set-pin"))

        with mock.patch.object(
                self.client._session, "post",
                return_value=FakeResponse(
                    200, {"description": "A purple screen."})) as post:
            out = self.client.describe_screenshot(
                b"fake-png", "shot.png", question="What is this?")
            self.assertEqual(out["description"], "A purple screen.")
            args, kwargs = post.call_args
            self.assertTrue(args[0].endswith("/api/screenshot"))

    # -- error paths ----------------------------------------------------------------------
    def test_connection_error_is_backend_unreachable(self):
        self._mock(side_effect=requests.exceptions.ConnectionError("down"))
        with self.assertRaises(BackendUnreachable):
            self.client.state()

    def test_timeout_is_backend_unreachable(self):
        self._mock(side_effect=requests.exceptions.Timeout("slow"))
        with self.assertRaises(BackendUnreachable):
            self.client.chat("hi")

    def test_http_500_is_backend_error_with_detail(self):
        self._mock(FakeResponse(500, {"detail": "Database is down"}))
        with self.assertRaises(BackendError) as ctx:
            self.client.state()
        self.assertIn("Database is down", str(ctx.exception))

    def test_http_401_is_backend_error(self):
        self._mock(FakeResponse(401, {"detail": "Unauthorized"}))
        with self.assertRaises(BackendError):
            self.client.chat("hi")


if __name__ == "__main__":
    unittest.main()
