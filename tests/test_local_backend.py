"""Tests for the 0.8.0 local backend (aries/service.py + the
production FastAPI app, run on the PC).

The desktop app is self-contained: the sidecar runs the SAME
backend as the website, against a local SQLite database in
~/.aries. These tests start that backend on a free port with an
isolated HOME and check the whole flow a user hits on first run:
register/login, admin by username (never by signup order), per-user
isolation, the honest no-key / dummy-key chat errors, tasks without
an LLM, face and screenshot fallbacks, and logout.

A dummy NVIDIA key is used for the chat test — it is not a real key
(the backend must answer 401 from NVIDIA, or a plain "could not
reach" when the network is down). No real key is ever used here.

Run: python -m unittest tests.test_local_backend -v

Run this suite in its OWN process, not together with
tests.test_service in one command: both suites load the production
FastAPI app module in-process, and the app's database engine is
bound at import to the first suite's temp HOME (which the first
suite deletes in tearDownClass). Run together, the second suite's
register calls hit a deleted database and fail with 500.
"""

import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
# Dummy engine key: chat must fail honestly (401 from NVIDIA, or a
# plain network error) — never with a traceback, never silently.
os.environ["NVIDIA_API_KEY"] = "nvapi-dummy-test-key-12345"

from aries import service as svc


def _call(base, method, path, body=None, token=None, form=None):
    data = None
    headers = {}
    if form is not None:
        data = form
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            return res.status, json.loads(res.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, {"detail": raw}


def _multipart_png(filename="test.png", size=(64, 64)):
    """A tiny PNG with no face in it (plain white)."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, format="PNG")
    boundary = "----aries-test-boundary"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: image/png\r\n\r\n"
    ).encode() + buf.getvalue() + (
        f"\r\n--{boundary}\r\n"
        'Content-Disposition: form-data; name="consent"\r\n\r\n'
        "true\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    return body, boundary


class LocalBackendTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp_home = tempfile.TemporaryDirectory()
        cls._old_home = os.environ.get("HOME")
        os.environ["HOME"] = cls._tmp_home.name
        server = svc.serve(0)
        cls.server = server
        cls.base = f"http://127.0.0.1:{server.port}"
        cls._thread = threading.Thread(target=server.serve_forever, daemon=True)
        cls._thread.start()
        cls.tokens = {}

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        if cls._old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = cls._old_home
        cls._tmp_home.cleanup()

    def _register(self, username, password="testpass123"):
        status, data = _call(self.base, "POST", "/api/auth/register",
                             {"username": username, "password": password})
        self.assertEqual(status, 200, data)
        self.tokens[username] = data["token"]
        return data

    def test_01_health_is_local(self):
        status, data = _call(self.base, "GET", "/health")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["version"], "0.8.0")
        self.assertEqual(data["backend"], "local")
        self.assertEqual(data["db"], "sqlite")
        self.assertTrue(data["db_path"].startswith("~/.aries/"))
        self.assertFalse(data["pc_enabled"])

    def test_02_api_health_reports_sqlite(self):
        status, data = _call(self.base, "GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["db"], "sqlite")
        self.assertTrue(data["key_configured"])  # dummy key is set
        self.assertFalse(data["elevenlabs_configured"])

    def test_03_unauthenticated_state_is_401(self):
        status, data = _call(self.base, "GET", "/api/state")
        self.assertEqual(status, 401)

    def test_04_admin_is_by_username_not_signup_order(self):
        # Kelfy signs up FIRST and is a normal user...
        kelfy = self._register("Kelfy")
        self.assertEqual(kelfy["user"]["role"], "user")
        # ...then Epsilon signs up later and is STILL the admin.
        epsilon = self._register("Epsilon")
        self.assertEqual(epsilon["user"]["role"], "admin")
        # Duplicate username, different case, is rejected.
        status, data = _call(self.base, "POST", "/api/auth/register",
                             {"username": "EPSILON", "password": "testpass123"})
        self.assertEqual(status, 400)
        # Wrong password is rejected.
        status, data = _call(self.base, "POST", "/api/auth/login",
                             {"username": "Epsilon", "password": "wrongpass"})
        self.assertEqual(status, 401)

    def test_05_admin_lists_users_kelfy_cannot(self):
        status, data = _call(self.base, "GET", "/api/admin/users",
                             token=self.tokens["Epsilon"])
        self.assertEqual(status, 200)
        names = {u["username"] for u in data["users"]}
        self.assertEqual(names, {"Epsilon", "Kelfy"})
        status, data = _call(self.base, "GET", "/api/admin/users",
                             token=self.tokens["Kelfy"])
        self.assertEqual(status, 403)

    def test_06_accounts_are_isolated(self):
        # Epsilon teaches a memory and saves a task (no LLM needed).
        status, data = _call(self.base, "POST", "/api/memories",
                             {"text": "Epsilon likes purple"},
                             token=self.tokens["Epsilon"])
        self.assertEqual(status, 200)
        status, data = _call(self.base, "POST", "/api/chat",
                             {"message": "remind me to call Kelfy tomorrow at 9am"},
                             token=self.tokens["Epsilon"])
        self.assertEqual(status, 200)
        self.assertEqual(data["task_added"]["title"], "Call Kelfy")
        # Kelfy sees none of it: empty memories, tasks and messages.
        status, data = _call(self.base, "GET", "/api/state",
                             token=self.tokens["Kelfy"])
        self.assertEqual(status, 200)
        self.assertEqual(data["memories"], [])
        self.assertEqual(data["tasks"], [])
        self.assertEqual(data["messages"], [])
        # Epsilon sees his own.
        status, data = _call(self.base, "GET", "/api/state",
                             token=self.tokens["Epsilon"])
        self.assertEqual(status, 200)
        self.assertEqual(len(data["memories"]), 1)
        self.assertEqual(data["memories"][0]["text"], "Epsilon likes purple")
        self.assertEqual(len(data["tasks"]), 1)

    def test_07_chat_with_dummy_key_fails_honestly(self):
        # Dummy key: NVIDIA answers 401 (or the network is down) —
        # either way the reply says so in plain words, no crash.
        status, data = _call(self.base, "POST", "/api/chat",
                             {"message": "hello there"},
                             token=self.tokens["Epsilon"])
        self.assertEqual(status, 200)
        self.assertIn("NVIDIA", data["reply"])
        self.assertNotIn("Traceback", data["reply"])

    def test_08_chat_with_no_key_fails_honestly(self):
        # With no NVIDIA key at all the local backend answers in plain
        # words that point at THIS PC's Settings (not Render — the
        # backend knows it is the sidecar via ARIES_LOCAL_BACKEND).
        old = os.environ.pop("NVIDIA_API_KEY", None)
        try:
            status, data = _call(self.base, "POST", "/api/chat",
                                 {"message": "hello there"},
                                 token=self.tokens["Kelfy"])
            self.assertEqual(status, 200)
            self.assertIn("No engine key on this PC yet", data["reply"])
            self.assertIn("Settings", data["reply"])
        finally:
            if old is not None:
                os.environ["NVIDIA_API_KEY"] = old

    def test_09_face_and_screenshot_fall_back_honestly(self):
        # Face verify with nothing enrolled: plain 400, no crash.
        # (Multipart needs the boundary header, so this posts raw.)
        body, boundary = _multipart_png()
        req = urllib.request.Request(
            self.base + "/api/face/verify", data=body, method="POST",
            headers={
                "Authorization": f"Bearer {self.tokens['Epsilon']}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                status, data = res.status, json.loads(res.read().decode())
        except urllib.error.HTTPError as exc:
            status, data = exc.code, json.loads(exc.read().decode())
        self.assertEqual(status, 400, data)
        self.assertIn("No face enrolled", data.get("detail", ""))
        # Screenshot with no vision model: honest fallback, not a crash.
        body, boundary = _multipart_png()
        req = urllib.request.Request(
            self.base + "/api/screenshot", data=body, method="POST",
            headers={
                "Authorization": f"Bearer {self.tokens['Epsilon']}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                status, data = res.status, json.loads(res.read().decode())
        except urllib.error.HTTPError as exc:
            status, data = exc.code, json.loads(exc.read().decode())
        self.assertEqual(status, 200, data)
        self.assertIn("VISION_MODEL is not configured", data["description"])

    def test_10_elevenlabs_key_status_and_empty_rejected(self):
        status, data = _call(self.base, "GET", "/local/elevenlabs/key/status")
        self.assertEqual(status, 200)
        self.assertFalse(data["has_key"])
        status, data = _call(self.base, "POST", "/local/elevenlabs/key",
                             {"key": "  "})
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertIn("empty", data["error"])

    def test_11_admin_can_delete_a_user_but_not_the_admin(self):
        # The admin account cannot be deleted, even by itself.
        status, data = _call(self.base, "DELETE", "/api/admin/users/Epsilon",
                             token=self.tokens["Epsilon"])
        self.assertEqual(status, 400)
        # Deleting Kelfy removes her account and all her data.
        status, data = _call(self.base, "DELETE", "/api/admin/users/Kelfy",
                             token=self.tokens["Epsilon"])
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        status, data = _call(self.base, "GET", "/api/auth/me",
                             token=self.tokens["Kelfy"])
        self.assertEqual(status, 401)

    def test_12_logout_kills_the_token(self):
        status, data = _call(self.base, "POST", "/api/auth/logout", {},
                             token=self.tokens["Epsilon"])
        self.assertEqual(status, 200)
        status, data = _call(self.base, "GET", "/api/auth/me",
                             token=self.tokens["Epsilon"])
        self.assertEqual(status, 401)


if __name__ == "__main__":
    unittest.main()
