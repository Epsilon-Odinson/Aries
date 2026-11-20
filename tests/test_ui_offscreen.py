"""Offscreen UI tests for the 0.5.0 desktop rebuild (aries/ui.py).

Runs with QT_QPA_PLATFORM=offscreen — no display needed. The
backend is faked (no network), the local NVIDIA key is absent
(on purpose: the whole point of 0.5.0 is that chat works through
the server without a key).

Covers: the main window renders with the real veil (baked grain +
artwork, not flat purple), the face and greeting sit on top, the
burst fires, Settings has all four tabs, mini mode is frameless /
transparent / face-only with a clear gap above the taskbar, the
Sources button expands/collapses, and the PC-command routing +
greeting strip behave.

Run:  QT_QPA_PLATFORM=offscreen python -m pytest tests/test_ui_offscreen.py -q
  or: QT_QPA_PLATFORM=offscreen python -m unittest tests.test_ui_offscreen -v
"""

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QDialog, QLabel, QLineEdit, QPushButton, QTabWidget

import aries.ui as ui
from aries.ui import (
    AriesMessageWidget,
    AriesWindow,
    ChatLog,
    MiniWindow,
    SettingsDialog,
    VeilWidget,
    looks_like_pc_command,
    strip_greeting_prefix,
)

FAKE_STATE = {
    "profile": {"name": "Epsilon", "call_name": "Epsilon", "about": ""},
    "personality": {"voice": "companion", "warmth": 70,
                    "directness": 60, "humour": 50, "notes": ""},
    "memory_file": "Epsilon likes piano.\n",
    "memories": [{"id": 1, "text": "Epsilon likes piano",
                  "category": "general", "pinned": False,
                  "pending": False}],
    "tasks": [{"id": 1, "title": "Study anatomy", "notes": "",
               "due_at": None, "done": False, "reminded": False}],
    "permissions": {"microphone": "ask", "web_search": "ask",
                    "save_memory": "ask", "delete_memory": "ask",
                    "clear_chat": "ask", "screenshot": "ask",
                    "face_login": "ask"},
    "face": {"enrolled": False, "login_enabled": False, "has_pin": False},
    "messages": [
        {"id": 1, "role": "user", "content": "What is the weather today?"},
        {"id": 2, "role": "assistant", "content": "Sunny and 31C.",
         "sources": [{"title": "Weather", "url": "https://x.test/",
                      "publisher": "Test"}]},
    ],
}


class FakeBackendClient:
    """Stand-in for BackendClient — no network, canned state."""

    def __init__(self, *args, **kwargs):
        pass

    def state(self):
        import copy
        return copy.deepcopy(FAKE_STATE)

    def due_tasks(self):
        return []

    def chat(self, message, use_search=False, consent=None):
        return {"reply": "Fake reply.", "sources": [], "searched": False,
                "pending_memory": None, "task_added": None,
                "needs_permission": None}

    def voices(self):
        return {"voices": []}

    def connections(self):
        return {"google": {"name": "Gmail", "connected": False,
                           "account": "", "configured": False},
                "spotify": {"name": "Spotify", "connected": False,
                            "account": "", "configured": False},
                "whatsapp": {"name": "WhatsApp", "connected": False,
                             "note": "no one-tap OAuth"}}

    def permissions(self):
        return FAKE_STATE["permissions"]

    def tts(self, text, voice_id=None):
        return b"ID3fake"


def get_app():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class UiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = get_app()

    def setUp(self):
        # Clean local prefs so each test starts from defaults.
        from PyQt5.QtCore import QSettings
        QSettings("Aries", "AriesDesktop").clear()
        self._patcher = mock.patch.object(
            ui, "BackendClient", FakeBackendClient)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def _make_window(self):
        window = AriesWindow()
        window.show()
        # Let the async state load finish.
        QTest.qWait(600)
        self.app.processEvents()
        return window

    # -- main window -------------------------------------------------------
    def test_main_window_renders_with_real_veil(self):
        window = self._make_window()
        try:
            self.assertTrue(window._backend_ok,
                            "state load should have succeeded")
            veil = window._bg
            self.assertIsInstance(veil, VeilWidget)
            self.assertTrue(veil._images_ok,
                            "grain/artwork must decode from veil_images.py")
            self.assertIsNotNone(veil._grain_pix)
            self.assertIsNotNone(veil._artwork_pix)
            # The veil is NOT flat purple: sample the rendered pixmap
            # and check the pixels vary (baked grain noise).
            pix = veil.grab()
            img = pix.toImage()
            colours = set()
            for x in range(0, img.width(), 7):
                for y in range(0, img.height(), 7):
                    colours.add(img.pixelColor(x, y).name())
            self.assertGreater(len(colours), 50,
                               f"veil looks flat ({len(colours)} colours) — "
                               "grain is not painting")
        finally:
            window.close()

    def test_face_greeting_and_composer_on_top(self):
        window = self._make_window()
        try:
            labels = [w.text() for w in window.findChildren(QLabel)]
            self.assertIn("ARIES", labels)
            self.assertIn("active", labels)
            self.assertIn("Ready to cook.", labels)
            self.assertIsNotNone(window._core)
            self.assertTrue(window._core.isVisible())
            # Composer: text input, mic, Send.
            self.assertIsInstance(window._input, QLineEdit)
            self.assertIsInstance(window._mic_btn, QPushButton)
            self.assertEqual(window._send_btn.text(), "Send")
            # Settings gear + fullscreen button, top-right.
            self.assertIsInstance(window._settings_btn, QPushButton)
            self.assertIsInstance(window._fs_btn, QPushButton)
        finally:
            window.close()

    def test_chat_works_without_a_local_key_when_backend_is_up(self):
        """0.5.0's point: no API key paste is needed on first run."""
        window = self._make_window()
        try:
            self.assertTrue(window._backend_ok)
            self.assertFalse(window._key_ok,
                             "this VM has no local key — the test "
                             "depends on that")
            self.assertTrue(window._input.isEnabled(),
                            "input must be enabled when the backend is up")
            # The restored history is in the log, with sources.
            self.assertGreater(window._chat._layout.count(), 1)
        finally:
            window.close()

    def test_veil_burst_fires(self):
        window = self._make_window()
        try:
            veil = window._bg
            self.assertEqual(len(veil._bursts), 0)
            veil.burst_at(100.0, 100.0)
            self.assertEqual(len(veil._bursts), 1)
            veil.burst_from_centre()
            self.assertEqual(len(veil._bursts), 2)
            # A burst entry carries its position and start time.
            self.assertIn("x", veil._bursts[0])
            self.assertIn("start", veil._bursts[0])
        finally:
            window.close()

    def test_grain_toggle_switches_images(self):
        window = self._make_window()
        try:
            veil = window._bg
            self.assertEqual(veil._grain_name, "fine")
            veil.set_grain("coarse")
            self.assertEqual(veil._grain_name, "coarse")
            veil.set_grain("bogus")
            self.assertEqual(veil._grain_name, "coarse",
                             "an unknown grain name must be ignored")
            veil.set_grain("fine")
            self.assertEqual(veil._grain_name, "fine")
        finally:
            window.close()

    # -- Sources button -------------------------------------------------------
    def test_sources_button_expands_and_collapses(self):
        sources = [
            {"title": "Weather today", "url": "https://x.test/1",
             "publisher": "Test"},
            {"title": "Forecast", "url": "https://x.test/2",
             "publisher": "Test"},
        ]
        widget = AriesMessageWidget("Sunny and 31C.", sources)
        widget.show()
        self.app.processEvents()
        try:
            btn = widget._btn
            self.assertIsNotNone(btn, "a reply with sources needs a button")
            self.assertEqual(btn.text(), "Sources (2)")
            self.assertFalse(widget._panel.isVisible(),
                             "sources start collapsed")
            btn.click()
            self.app.processEvents()
            self.assertTrue(widget._panel.isVisible(),
                            "tapping Sources must expand the links")
            links = widget._panel.findChildren(QLabel)
            self.assertEqual(len(links), 2)
            self.assertIn("https://x.test/1", links[0].text())
            btn.click()
            self.app.processEvents()
            self.assertFalse(widget._panel.isVisible(),
                             "tapping again must collapse them")
        finally:
            widget.close()

    def test_reply_without_sources_has_no_button(self):
        widget = AriesMessageWidget("Just an answer, no search.")
        widget.show()
        self.app.processEvents()
        try:
            self.assertIsNone(widget._btn)
            self.assertIsNone(widget._panel)
        finally:
            widget.close()

    def test_chat_log_adds_sources_button_for_history(self):
        window = self._make_window()
        try:
            # The fake state's history has one assistant message with
            # a source — its widget must carry a Sources button.
            buttons = [b for b in window._chat.findChildren(QPushButton)
                       if b.text().startswith("Sources")]
            self.assertEqual(len(buttons), 1)
            self.assertEqual(buttons[0].text(), "Sources (1)")
        finally:
            window.close()

    # -- Settings ---------------------------------------------------------------
    def test_settings_has_all_four_tabs(self):
        window = self._make_window()
        try:
            from aries.voice import VoiceEngine
            dialog = SettingsDialog(FakeBackendClient(),
                                    VoiceEngine(FakeBackendClient()), window)
            dialog.show()
            self.app.processEvents()
            try:
                tabs = dialog.findChild(QTabWidget)
                self.assertIsNotNone(tabs)
                names = [tabs.tabText(i) for i in range(tabs.count())]
                self.assertEqual(len(names), 4)
                for expected in ("Aries", "Memory", "More", "Connections"):
                    self.assertIn(expected, names,
                                  f"Settings is missing the {expected} tab")
            finally:
                dialog.close()
        finally:
            window.close()

    # -- mini mode ------------------------------------------------------------------
    def test_mini_mode_is_frameless_transparent_face_only(self):
        mini = MiniWindow()
        try:
            flags = mini.windowFlags()
            self.assertTrue(bool(flags & Qt.FramelessWindowHint),
                            "mini mode must be frameless")
            self.assertTrue(bool(flags & Qt.WindowStaysOnTopHint),
                            "mini mode must stay on top")
            self.assertTrue(mini.testAttribute(Qt.WA_TranslucentBackground),
                            "mini mode must be transparent")
            self.assertEqual(mini.width(), 200)
            self.assertEqual(mini.height(), 200)
            # No text input in mini mode — voice only.
            self.assertEqual(mini.findChildren(QLineEdit), [])
            # Only the face inside.
            self.assertIsNotNone(mini._core)
        finally:
            mini.close()

    def test_mini_mode_floats_with_a_gap_above_the_taskbar(self):
        mini = MiniWindow()
        mini.show()
        self.app.processEvents()
        try:
            mini.dock_bottom_right()
            self.app.processEvents()
            gap = mini.gap_above_taskbar()
            self.assertGreaterEqual(
                gap, MiniWindow.GAP_ABOVE_TASKBAR,
                f"mini mode sits {gap}px above the taskbar — it must "
                "float with a clear gap, never touch it")
            self.assertEqual(gap, 56)
        finally:
            mini.close()

    # -- routing + greeting ---------------------------------------------------------
    def test_pc_command_routing(self):
        for text in (
            "open notepad",
            "Open File Explorer",
            "take a screenshot",
            "shutdown the pc",
            "type hello world",
            "click on the start button",
            "open C:\\Users\\Epsilon\\file.txt",
        ):
            with self.subTest(text=text):
                self.assertTrue(looks_like_pc_command(text), text)
        for text in (
            "What is the weather today?",
            "Teach me about the heart",
            "How do I say hello in French?",
            "Remind me to study at 19:30",
            "What does the Bible say about love?",
        ):
            with self.subTest(text=text):
                self.assertFalse(looks_like_pc_command(text), text)

    def test_greeting_prefix_is_stripped(self):
        self.assertEqual(
            strip_greeting_prefix("Aries active. Ready to cook. Hello!"),
            "Hello!")
        self.assertEqual(
            strip_greeting_prefix("Aries, active. Ready to cook.\nHi."),
            "Hi.")
        self.assertEqual(
            strip_greeting_prefix("The heart pumps blood."),
            "The heart pumps blood.")
        self.assertEqual(strip_greeting_prefix(""), "")


if __name__ == "__main__":
    unittest.main()
