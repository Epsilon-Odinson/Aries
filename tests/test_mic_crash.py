"""Regression tests for the 0.6.0 mic crash + mini-click fixes.

The 0.5.0 bug: tapping the mic (or pressing the mini face, which
single-clicked into listen) could CLOSE the app, because
ListenWorker only caught VoiceError and _mic_available() enumerated
PortAudio devices on the GUI thread. 0.6.0 isolates the mic in a
helper subprocess, catches everything in the worker, and installs a
global excepthook — so a mic failure is a plain-words message, never
a crash. Mini mode now expands on a press (single or double click);
voice there is the wake phrase only.

Also covers: the 60 fps pointer orb (16 ms timer, cached veil
base), and the "© Made by Epsilon" stamp on desktop and web.

Run: QT_QPA_PLATFORM=offscreen python -m unittest tests.test_mic_crash -v
"""

import json
import os
import subprocess
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest import mock

import aries.ui as ui
from aries.ui import AriesWindow, ListenWorker, MiniWindow, VeilWidget
from aries.voice import VoiceEngine, VoiceError
from tests.test_ui_offscreen import FakeBackendClient


def _make_window(app):
    """A real window with the backend faked (no network)."""
    patcher = mock.patch.object(ui, "BackendClient", FakeBackendClient)
    patcher.start()
    try:
        window = AriesWindow()
        window.show()
        app.processEvents()
        return window, patcher
    except Exception:
        patcher.stop()
        raise


class _RaisingEngine:
    """A VoiceEngine stand-in whose listen_once raises."""

    def __init__(self, exc):
        self._exc = exc

    def listen_once(self, *args, **kwargs):
        raise self._exc


class TestListenWorkerNeverRaises(unittest.TestCase):
    def test_non_voice_error_emits_failed_not_raise(self):
        # The 0.5.0 regression: a RuntimeError from PortAudio escaped
        # ListenWorker.run() and could take the app down.
        worker = ListenWorker(_RaisingEngine(RuntimeError("PortAudio boom")))
        seen = []
        worker.failed.connect(seen.append)
        worker.finished.connect(lambda t: seen.append(("finished", t)))
        worker.run()  # must not raise
        self.assertEqual(len(seen), 1)
        self.assertIn("PortAudio boom", seen[0])
        self.assertIn("Try again", seen[0])

    def test_voice_error_emits_failed(self):
        worker = ListenWorker(
            _RaisingEngine(VoiceError("No microphone found.")))
        seen = []
        worker.failed.connect(seen.append)
        worker.run()
        self.assertEqual(seen, ["No microphone found."])

    def test_success_emits_finished(self):
        class OkEngine:
            def listen_once(self, *args, **kwargs):
                return "hello aries"

        worker = ListenWorker(OkEngine())
        seen = []
        worker.finished.connect(seen.append)
        worker.failed.connect(lambda m: seen.append(("failed", m)))
        worker.run()
        self.assertEqual(seen, ["hello aries"])


class TestMicDegradesPlainly(unittest.TestCase):
    def test_listen_once_with_no_mic_raises_voice_error(self):
        engine = VoiceEngine(None)
        with self.assertRaises(VoiceError) as ctx:
            engine.listen_once(timeout=3.0)
        message = str(ctx.exception)
        self.assertTrue(message.strip())
        self.assertNotIn("{", message)
        self.assertNotIn("Traceback", message)

    def test_start_wake_with_no_mic_raises_and_stays_off(self):
        engine = VoiceEngine(None)
        with self.assertRaises(VoiceError):
            engine.start_wake(lambda: None)
        self.assertFalse(engine.wake_active())

    def test_mic_available_reports_plainly(self):
        engine = VoiceEngine(None)
        ok, why = engine.mic_available()
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(why, str)
        if not ok:
            self.assertTrue(why.strip())
            self.assertNotIn("Traceback", why)

    def test_mic_probe_subprocess_returns_clean_json(self):
        # The helper subprocess must never crash the caller: it
        # prints one JSON line and exits with a code.
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = dict(os.environ, PYTHONPATH=root)
        proc = subprocess.run(
            [sys.executable, "-m", "aries.main", "--mic-probe"],
            cwd=root, env=env, capture_output=True, text=True,
            timeout=60)
        self.assertEqual(proc.returncode, 1,
                         "no mic on this VM, so the probe must fail")
        line = proc.stdout.strip().splitlines()[-1]
        payload = json.loads(line)  # must be valid JSON, not a traceback
        self.assertFalse(payload["ok"])
        self.assertIn("why", payload)
        self.assertNotIn("Traceback", payload["why"])

    def test_mic_record_subprocess_returns_clean_json(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = dict(os.environ, PYTHONPATH=root)
        proc = subprocess.run(
            [sys.executable, "-m", "aries.main", "--mic-record", "5"],
            cwd=root, env=env, capture_output=True, text=True,
            timeout=60)
        self.assertEqual(proc.returncode, 1)
        line = proc.stdout.strip().splitlines()[-1]
        payload = json.loads(line)
        self.assertFalse(payload["ok"])
        self.assertIn("why", payload)


class TestMiniClickExpands(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_single_click_expands_to_full_scale(self):
        window, patcher = _make_window(self.app)
        try:
            window._open_mini()
            self.app.processEvents()
            self.assertIsNotNone(window._mini,
                                 "mini mode must open")
            # A single press on the mini face expands (0.5.0: it
            # started listening and could crash the app).
            window._mini.face_clicked.emit()
            self.app.processEvents()
            self.assertIsNone(window._mini,
                              "single click on the mini face must "
                              "expand back to full scale")
            self.assertTrue(window.isVisible())
        finally:
            window.close()
            patcher.stop()

    def test_double_click_expands_to_full_scale(self):
        window, patcher = _make_window(self.app)
        try:
            window._open_mini()
            self.app.processEvents()
            self.assertIsNotNone(window._mini)
            window._mini.face_double_clicked.emit()
            self.app.processEvents()
            self.assertIsNone(window._mini,
                              "double click must also expand")
        finally:
            window.close()
            patcher.stop()

    def test_mini_still_has_gap_and_no_input(self):
        mini = MiniWindow()
        try:
            mini.show()
            self.app.processEvents()
            mini.dock_bottom_right()
            self.assertEqual(mini.gap_above_taskbar(), 56)
            from PyQt5.QtWidgets import QLineEdit
            self.assertEqual(mini.findChildren(QLineEdit), [],
                             "mini mode has no text input — voice only")
        finally:
            mini.close()


class TestVeilOrbSpeed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_veil_timer_is_16ms(self):
        veil = VeilWidget()
        try:
            self.assertEqual(veil._timer.interval(), 16,
                             "the pointer orb must update at 60 fps "
                             "(16 ms), not 20 fps (50 ms)")
        finally:
            veil.close()

    def test_veil_caches_static_base(self):
        veil = VeilWidget()
        try:
            veil.resize(800, 600)
            veil.show()
            self.app.processEvents()
            self.assertIsNotNone(veil._base_pix,
                                 "the static veil (bg + grain + "
                                 "vignette) must be cached, not "
                                 "redrawn every frame")
            self.assertEqual(veil._base_pix.width(), 800)
            self.assertEqual(veil._base_pix.height(), 600)
        finally:
            veil.close()

    def test_veil_renders_grain_and_artwork(self):
        veil = VeilWidget()
        try:
            veil.resize(400, 300)
            veil.show()
            self.app.processEvents()
            pix = veil.grab()
            self.assertFalse(pix.isNull())
            img = pix.toImage()
            colours = {img.pixelColor(x, y).rgb()
                       for x in range(0, 400, 17)
                       for y in range(0, 300, 13)}
            # More than one colour: grain + artwork, not flat purple.
            self.assertGreater(len(colours), 20,
                               "the veil must render grain/artwork, "
                               "not a flat fill")
        finally:
            veil.close()


class TestMadeByEpsilonStamp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_desktop_stamp_present_italic_script(self):
        window, patcher = _make_window(self.app)
        try:
            stamp = window._stamp
            self.assertEqual(stamp.text(), "© Made by Epsilon")
            self.assertTrue(stamp.isVisible())
            style = stamp.styleSheet()
            self.assertIn("italic", style)
            self.assertIn("Segoe Script", style)
            self.assertIn("Brush Script MT", style)
            # Bottom-left corner.
            self.assertLess(stamp.x(), 40)
            self.assertGreater(stamp.y(), window.height() - 60)
        finally:
            window.close()
            patcher.stop()

    def test_web_stamp_present_in_app_jsx_and_css(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        web = os.path.join(root, "..", "aries-prod-staging",
                           "production", "web", "src")
        with open(os.path.join(web, "App.jsx")) as f:
            jsx = f.read()
        with open(os.path.join(web, "App.css")) as f:
            css = f.read()
        self.assertIn("© Made by Epsilon", jsx)
        self.assertIn('className="made-by"', jsx)
        self.assertIn(".made-by", css)
        self.assertIn("Segoe Script", css)
        self.assertIn("italic", css)


if __name__ == "__main__":
    unittest.main()
