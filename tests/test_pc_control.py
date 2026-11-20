"""Tests for aries/pc_control.py — all 25 actions (0.6.0).

Covers: every action validates with its required fields, every
action has a plain-words describe_action line (no JSON, no field
names), the 5-step cap, the confirmation model (destructive
detection), mocked runs of every action (real hardware is absent
on this Linux VM), and plain-words failures (no raw JSON ever).

Run: QT_QPA_PLATFORM=offscreen python -m unittest tests.test_pc_control -v
"""

import ctypes  # noqa: F401  (must load before os.name is patched)
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aries.pc_control as pc
from aries.pc_control import (
    ACTIONS,
    MAX_STEPS,
    PcControlError,
    describe_action,
    execute_step,
    execute_steps,
    is_destructive,
    sanitize_display,
    validate_steps,
)

# A minimal valid step per action (values chosen to be safe on Linux).
VALID_STEPS = {
    "open_app": {"action": "open_app", "name": "notepad"},
    "open_path": {"action": "open_path", "path": "/tmp"},
    "open_url": {"action": "open_url", "url": "https://example.com"},
    "type_text": {"action": "type_text", "text": "hello"},
    "press_keys": {"action": "press_keys", "keys": ["ctrl", "c"]},
    "mouse_move": {"action": "mouse_move", "x": 500, "y": 300},
    "mouse_click": {"action": "mouse_click", "x": 500, "y": 300},
    "mouse_scroll": {"action": "mouse_scroll", "amount": 3},
    "clipboard_copy": {"action": "clipboard_copy", "text": "copy me"},
    "clipboard_paste": {"action": "clipboard_paste"},
    "shell": {"action": "shell", "command": "echo hi"},
    "read_file": {"action": "read_file", "path": "/etc/hostname"},
    "write_file": {"action": "write_file", "path": "/tmp/aries-test.txt",
                   "text": "hi"},
    "system_info": {"action": "system_info"},
    "window_minimise": {"action": "window_minimise"},
    "window_maximise": {"action": "window_maximise"},
    "window_restore": {"action": "window_restore"},
    "window_close": {"action": "window_close"},
    "volume_up": {"action": "volume_up"},
    "volume_down": {"action": "volume_down"},
    "volume_mute": {"action": "volume_mute"},
    "media_key": {"action": "media_key", "key": "play_pause"},
    "screenshot": {"action": "screenshot"},
    "lock_pc": {"action": "lock_pc"},
    "open_settings": {"action": "open_settings", "page": "bluetooth"},
}


def _fake_gui():
    """A mock pyautogui: records calls, never touches the screen."""
    gui = mock.MagicMock()
    shot = mock.MagicMock()
    gui.screenshot.return_value = shot
    return gui


class TestActionSet(unittest.TestCase):
    def test_twenty_five_actions(self):
        self.assertEqual(len(ACTIONS), 25,
                         "0.6.0 must have 25 PC actions (12 old + 13 new)")
        for name in ("open_url", "press_keys", "window_minimise",
                     "window_maximise", "window_restore", "window_close",
                     "volume_up", "volume_down", "volume_mute",
                     "media_key", "screenshot", "lock_pc",
                     "open_settings"):
            self.assertIn(name, ACTIONS, f"new action {name} is missing")

    def test_every_action_validates(self):
        for name, step in VALID_STEPS.items():
            with self.subTest(action=name):
                validate_steps([step])

    def test_every_action_has_plain_words_description(self):
        for name, step in VALID_STEPS.items():
            with self.subTest(action=name):
                text = describe_action(step)
                self.assertIsInstance(text, str)
                self.assertTrue(text.strip(), "description must not be empty")
                # No JSON, no field names in what the user sees.
                self.assertNotIn("{", text)
                self.assertNotIn('"action"', text)
                self.assertNotIn('"name"', text)
                self.assertNotIn("pc-actions", text)

    def test_instructions_mention_new_actions(self):
        for name in ("open_url", "press_keys", "window_minimise",
                     "window_maximise", "window_restore", "window_close",
                     "volume_up", "volume_mute", "media_key",
                     "screenshot", "lock_pc", "open_settings"):
            self.assertIn(name, pc.PC_CONTROL_INSTRUCTIONS,
                          f"instructions must tell the model about {name}")


class TestValidationAndCap(unittest.TestCase):
    def test_five_step_cap(self):
        self.assertEqual(MAX_STEPS, 5)
        plan = [dict(VALID_STEPS["volume_up"]) for _ in range(6)]
        with self.assertRaises(PcControlError) as ctx:
            validate_steps(plan)
        self.assertIn("5", str(ctx.exception))

    def test_five_steps_ok(self):
        plan = [dict(VALID_STEPS["volume_up"]) for _ in range(5)]
        validate_steps(plan)

    def test_unknown_action_rejected(self):
        with self.assertRaises(PcControlError):
            validate_steps([{"action": "format_drive"}])

    def test_missing_required_field_rejected(self):
        with self.assertRaises(PcControlError):
            validate_steps([{"action": "open_url"}])
        with self.assertRaises(PcControlError):
            validate_steps([{"action": "media_key"}])
        with self.assertRaises(PcControlError):
            validate_steps([{"action": "press_keys"}])

    def test_destructive_detection_intact(self):
        self.assertTrue(is_destructive(
            {"action": "shell", "command": "shutdown /s /t 0"}))
        self.assertTrue(is_destructive(
            {"action": "shell", "command": "del /q important.txt"}))
        # write_file is destructive only when it would overwrite a
        # file that already exists.
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            existing = tmp.name
        try:
            self.assertTrue(is_destructive(
                {"action": "write_file", "path": existing, "text": "y"}))
        finally:
            os.unlink(existing)
        self.assertFalse(is_destructive(
            {"action": "write_file", "path": "/tmp/aries-no-such-file.txt",
             "text": "y"}))
        self.assertFalse(is_destructive(
            {"action": "shell", "command": "echo hi"}))
        self.assertFalse(is_destructive(
            {"action": "volume_up"}))
        self.assertFalse(is_destructive(
            {"action": "screenshot"}))


class TestMockedRuns(unittest.TestCase):
    """Every action through execute_steps with mocks — plain words,
    no raw JSON, stops at the first failure."""

    def _run(self, step):
        return execute_steps([step])[0]

    def test_open_url_adds_https_and_opens(self):
        with mock.patch("webbrowser.open", return_value=True) as opened:
            result = self._run({"action": "open_url", "url": "google.com"})
        self.assertTrue(result["ok"], result)
        opened.assert_called_once_with("https://google.com")
        self.assertIn("https://google.com", result["summary"])

    def test_open_url_rejects_non_http(self):
        result = self._run({"action": "open_url", "url": "ftp://x.test/"})
        self.assertFalse(result["ok"])
        self.assertNotIn("{", result["summary"])
        self.assertIn("ftp", result["summary"])

    def test_press_keys_single_and_combo(self):
        gui = _fake_gui()
        with mock.patch.object(pc, "_pyautogui", return_value=gui):
            result = self._run({"action": "press_keys", "keys": ["ctrl", "c"]})
            gui.hotkey.assert_called_once_with("ctrl", "c")
            self.assertTrue(result["ok"], result)
            self.assertIn("ctrl+c", result["summary"])
            result = self._run({"action": "press_keys", "keys": "enter"})
            gui.press.assert_called_with("enter")
            self.assertTrue(result["ok"], result)

    def test_window_actions(self):
        gui = _fake_gui()
        with mock.patch.object(pc, "_pyautogui", return_value=gui):
            for action, call in (
                    ("window_minimise", ("win", "down")),
                    ("window_maximise", ("win", "up")),
                    ("window_close", ("alt", "f4"))):
                with self.subTest(action=action):
                    gui.reset_mock()
                    result = self._run({"action": action})
                    self.assertTrue(result["ok"], result)
                    gui.hotkey.assert_called_with(*call)
                    self.assertNotIn("{", result["summary"])
            gui.reset_mock()
            result = self._run({"action": "window_restore"})
            self.assertTrue(result["ok"], result)
            gui.hotkey.assert_called_with("alt", "space")
            gui.press.assert_called_with("r")

    def test_volume_and_media_keys(self):
        gui = _fake_gui()
        with mock.patch.object(pc, "_pyautogui", return_value=gui):
            for action, key in (("volume_up", "volumeup"),
                                ("volume_down", "volumedown"),
                                ("volume_mute", "volumemute")):
                with self.subTest(action=action):
                    gui.reset_mock()
                    result = self._run({"action": action})
                    self.assertTrue(result["ok"], result)
                    gui.press.assert_called_with(key)
            for key, expected in (("play_pause", "audioplaypause"),
                                  ("next", "audionext"),
                                  ("previous", "audioprev"),
                                  ("stop", "audiostop")):
                with self.subTest(media=key):
                    gui.reset_mock()
                    result = self._run(
                        {"action": "media_key", "key": key})
                    self.assertTrue(result["ok"], result)
                    gui.press.assert_called_with(expected)

    def test_unknown_media_key_is_plain_words(self):
        gui = _fake_gui()
        with mock.patch.object(pc, "_pyautogui", return_value=gui):
            result = self._run({"action": "media_key", "key": "rewind"})
        self.assertFalse(result["ok"])
        self.assertIn("rewind", result["summary"])
        self.assertNotIn("{", result["summary"])
        self.assertNotIn('"', result["summary"])

    def test_screenshot_saves_and_reports_path(self):
        gui = _fake_gui()
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "shot.png")
            with mock.patch.object(pc, "_pyautogui", return_value=gui):
                result = self._run(
                    {"action": "screenshot", "path": target})
            self.assertTrue(result["ok"], result)
            self.assertIn(target, result["summary"])
            gui.screenshot.return_value.save.assert_called_once_with(target)

    def test_screenshot_bad_extension_is_plain_words(self):
        gui = _fake_gui()
        with mock.patch.object(pc, "_pyautogui", return_value=gui):
            result = self._run(
                {"action": "screenshot", "path": "/tmp/shot.bmp"})
        self.assertFalse(result["ok"])
        self.assertIn(".png", result["summary"])

    def test_lock_pc_windows_only(self):
        # On this Linux VM the action must fail in plain words.
        result = self._run({"action": "lock_pc"})
        self.assertFalse(result["ok"])
        self.assertIn("Windows", result["summary"])
        # Mocked Windows: it calls LockWorkStation.
        with mock.patch.object(pc.os, "name", "nt"):
            windll = mock.MagicMock()
            windll.user32.LockWorkStation.return_value = True
            with mock.patch("ctypes.windll", windll, create=True):
                result = self._run({"action": "lock_pc"})
        self.assertTrue(result["ok"], result)
        self.assertIn("Locked", result["summary"])

    def test_open_settings_windows_only(self):
        result = self._run({"action": "open_settings", "page": "wifi"})
        self.assertFalse(result["ok"])
        self.assertIn("Windows", result["summary"])
        with mock.patch.object(pc.os, "name", "nt"), \
                mock.patch.object(pc.os, "startfile", create=True) as startfile:
            result = self._run({"action": "open_settings", "page": "wifi"})
        self.assertTrue(result["ok"], result)
        startfile.assert_called_once_with("ms-settings:network-wifi")
        self.assertIn("wifi", result["summary"])

    def test_open_settings_unknown_page_opens_main_settings(self):
        with mock.patch.object(pc.os, "name", "nt"), \
                mock.patch.object(pc.os, "startfile", create=True) as startfile:
            result = self._run(
                {"action": "open_settings", "page": "nonsense"})
        self.assertTrue(result["ok"], result)
        startfile.assert_called_once_with("ms-settings:")

    def test_execute_steps_stops_at_first_failure(self):
        gui = _fake_gui()
        with mock.patch.object(pc, "_pyautogui", return_value=gui):
            results = execute_steps([
                {"action": "volume_up"},
                {"action": "media_key", "key": "rewind"},
                {"action": "volume_down"},
            ])
        self.assertEqual(len(results), 2)
        self.assertTrue(results[0]["ok"])
        self.assertFalse(results[1]["ok"])

    def test_execute_step_never_raises(self):
        # Even a step whose runner raises a non-PcControlError.
        with mock.patch.object(pc, "_pyautogui",
                               side_effect=RuntimeError("boom")):
            result = execute_step({"action": "volume_up"})
        self.assertFalse(result["ok"])
        self.assertIn("volume_up", result["summary"])


class TestNoRawJson(unittest.TestCase):
    def test_sanitize_display_strips_pc_actions_block(self):
        reply = ("Sure thing.\n```pc-actions\n"
                 '[{"action": "open_app", "name": "notepad"}]\n```\nDone.')
        cleaned = sanitize_display(reply)
        self.assertNotIn("pc-actions", cleaned)
        self.assertNotIn('"action"', cleaned)
        self.assertNotIn("{", cleaned)
        self.assertIn("Sure thing.", cleaned)

    def test_summaries_are_not_json(self):
        # Every VALID_STEPS summary must not parse as JSON.
        gui = _fake_gui()
        with mock.patch.object(pc, "_pyautogui", return_value=gui), \
                mock.patch("webbrowser.open", return_value=True), \
                mock.patch.object(pc.os, "name", "nt"), \
                mock.patch.object(pc.os, "startfile", create=True):
            for name, step in VALID_STEPS.items():
                with self.subTest(action=name):
                    if name in ("lock_pc",):
                        with mock.patch("ctypes.windll", create=True) as windll:
                            windll.user32.LockWorkStation.return_value = True
                            result = execute_step(step)
                    else:
                        result = execute_step(step)
                    if not result["ok"]:
                        # Only the genuinely environment-dependent
                        # ones may fail here (open_app/open_path on
                        # Linux, read of a missing file).
                        self.assertNotIn("{", result["summary"])
                        continue
                    try:
                        json.loads(result["summary"])
                    except (ValueError, TypeError):
                        pass
                    else:
                        self.fail(
                            f"{name} summary parses as JSON: "
                            f"{result['summary']!r}")


if __name__ == "__main__":
    unittest.main()
