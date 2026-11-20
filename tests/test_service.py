"""Tests for the 0.7.0 local service (aries/service.py).

The Electron shell talks to this service on 127.0.0.1 for the
desktop-only powers: PC control, microphone, wake phrase. The
service must never crash on a bad request, must report plain
words (never raw JSON / tracebacks), and must isolate the mic so
a bad audio driver can never kill the app.

Run: python -m unittest tests.test_service -v
"""

import json
import os
import sys
import threading
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from aries import service as svc


def _start_server():
    server = svc.serve(0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _call(base, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        base + path, data=data, method=method,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return res.status, json.loads(res.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


class ServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, cls.base = _start_server()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_health(self):
        status, data = _call(self.base, "GET", "/health")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["version"], "0.7.0")
        self.assertIn("pc_enabled", data)

    def test_unknown_route_is_plain_404(self):
        status, data = _call(self.base, "GET", "/nope")
        self.assertEqual(status, 404)
        self.assertFalse(data["ok"])
        self.assertIn("No such route", data["error"])

    def test_mic_available_without_mic_is_plain_words(self):
        # This VM has no PyAudio and no mic: the probe must report
        # plainly, not crash, not raise.
        status, data = _call(self.base, "GET", "/mic/available")
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertTrue(data["why"])
        self.assertNotIn("Traceback", data["why"])

    def test_mic_listen_without_mic_is_plain_words(self):
        status, data = _call(self.base, "POST", "/mic/listen", {"timeout": 2})
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertTrue(data["why"])

    def test_wake_start_without_mic_is_plain_words(self):
        status, data = _call(self.base, "POST", "/mic/wake/start", {})
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertTrue(data["why"])

    def test_wake_poll_empty(self):
        status, data = _call(self.base, "GET", "/mic/wake/poll")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["wakes"], 0)

    def test_pc_describe(self):
        status, data = _call(self.base, "POST", "/local/pc/describe",
                             {"steps": [{"action": "open_app", "name": "notepad"},
                                        {"action": "system_info"}]})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["lines"][0], "Open the app “notepad”")
        self.assertFalse(data["destructive"])

    def test_pc_describe_destructive_flagged(self):
        status, data = _call(self.base, "POST", "/local/pc/describe",
                             {"steps": [{"action": "shell", "command": "shutdown /s /t 0"}]})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertTrue(data["destructive"])

    def test_pc_describe_too_many_steps(self):
        steps = [{"action": "system_info"}] * 6
        status, data = _call(self.base, "POST", "/local/pc/describe", {"steps": steps})
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertIn("5", data["error"])

    def test_pc_execute_refused_when_off(self):
        svc._set_pc_enabled(False)
        status, data = _call(self.base, "POST", "/local/pc/execute",
                             {"steps": [{"action": "system_info"}]})
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertIn("PC control is off", data["error"])

    def test_pc_execute_system_info_when_on(self):
        svc._set_pc_enabled(True)
        try:
            status, data = _call(self.base, "POST", "/local/pc/execute",
                                 {"steps": [{"action": "system_info"}]})
            self.assertEqual(status, 200)
            self.assertTrue(data["ok"])
            self.assertTrue(data["done"])
            self.assertIn("System info", data["done"][0])
        finally:
            svc._set_pc_enabled(False)

    def test_pc_execute_unknown_app_is_plain_words(self):
        # On Linux open_app refuses cleanly; on Windows it would say
        # there's no app by that name. Either way: plain words, no crash.
        svc._set_pc_enabled(True)
        try:
            status, data = _call(self.base, "POST", "/local/pc/execute",
                                 {"steps": [{"action": "open_app", "name": "No Such App XYZ"}]})
            self.assertEqual(status, 200)
            self.assertFalse(data["ok"])
            self.assertTrue(data["error"])
            self.assertNotIn("Traceback", data["error"])
        finally:
            svc._set_pc_enabled(False)

    def test_local_chat_needs_key_without_key(self):
        # No NVIDIA key on this VM: the PC flow must say so plainly.
        svc._set_pc_enabled(True)
        try:
            status, data = _call(self.base, "POST", "/local/chat",
                                 {"message": "open notepad"})
            self.assertEqual(status, 200)
            self.assertTrue(data["ok"])
            self.assertTrue(data.get("needs_key"))
            self.assertIn("engine key", data["reply"])
        finally:
            svc._set_pc_enabled(False)

    def test_local_chat_refused_when_pc_off(self):
        svc._set_pc_enabled(False)
        status, data = _call(self.base, "POST", "/local/chat",
                             {"message": "open notepad"})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertTrue(data.get("needs_pc"))

    def test_local_chat_plan_flow_with_mocked_llm(self):
        # Full plan flow: mocked LLM returns a pc-actions block, the
        # service extracts the steps and describes them; execute then
        # runs the confirmed plan.
        import aries.llm as llm_mod
        import aries.config as cfg

        real_chat = llm_mod.chat
        real_key = cfg.get_api_key
        llm_mod.chat = lambda messages, **kw: (
            '[{"action": "open_app", "name": "notepad"}]'
            "```pc-actions\n"
            '[{"action": "system_info"}]\n'
            "```"
        )
        # The service imports get_api_key inside _local_chat, so
        # patching aries.config.get_api_key is enough.
        cfg.get_api_key = lambda: "nvapi-test"
        svc._set_pc_enabled(True)
        try:
            status, data = _call(self.base, "POST", "/local/chat",
                                 {"message": "check the system info"})
            self.assertEqual(status, 200)
            self.assertTrue(data["ok"])
            self.assertTrue(data.get("is_pc"))
            self.assertEqual(data["steps"], [{"action": "system_info"}])
            self.assertIn("system info", data["lines"][0].lower())
            # Execute the confirmed plan.
            status, data = _call(self.base, "POST", "/local/pc/execute",
                                 {"steps": data["steps"]})
            self.assertEqual(status, 200)
            self.assertTrue(data["ok"])
            self.assertIn("System info", data["done"][0])
        finally:
            llm_mod.chat = real_chat
            cfg.get_api_key = real_key
            svc._set_pc_enabled(False)

    def test_key_status(self):
        status, data = _call(self.base, "GET", "/local/key/status")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertIn("has_key", data)

    def test_key_save_empty_rejected(self):
        status, data = _call(self.base, "POST", "/local/key", {"key": "  "})
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertIn("empty", data["error"])


if __name__ == "__main__":
    unittest.main()
