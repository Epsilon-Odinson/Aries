"""PyQt5 desktop UI for Aries (Module 2, JARVIS redesign).

JARVIS-style HUD in Aries' deep-purple palette (background #0d0010,
accent #7b2fff). No code rain, no cartoon face: the background is a
dark HUD — faint grid, slow rotating rings, drifting particles, a soft
radial glow behind the core — and the bot "face" is a glowing
arc-reactor core with concentric rotating arcs.

Light on purpose: background and core animations are capped at
~15 fps, and everything is drawn with QPainter (no image assets),
so this stays comfortable on a ThinkPad T14 (11th Gen i5, 16 GB RAM).

Kept OUT of Module 2 on purpose: voice, face-recognition login,
permissions, DuckDuckGo search, tool calling / autonomy.
Those are later modules.
"""

from __future__ import annotations

import datetime
import math
import random

from PyQt5.QtCore import QObject, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPen
from PyQt5.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .config import CHAT_MODEL, get_api_key, has_api_key
from .llm import AriesLLMError, chat
from .main import GREETING, SYSTEM_PROMPT

# --- Theme -----------------------------------------------------------------
BG = "#0d0010"
PANEL = "#1a0026"
PANEL_SEMI = "rgba(26, 0, 38, 215)"
ACCENT = "#7b2fff"
ACCENT_SOFT = "#a86bff"
TEXT = "#e8e0ff"
MUTED = "#9a8fb5"
CYAN = "#35e0ff"  # JARVIS accent for the core only; purple stays dominant
ERROR_RED = "#ff6b81"
MONO = "Consolas"


def _alpha(color: str, alpha: int) -> QColor:
    c = QColor(color)
    c.setAlpha(max(0, min(255, alpha)))
    return c


# --- HUD background ----------------------------------------------------------
class HudBackgroundWidget(QWidget):
    """Dark JARVIS HUD background. No falling characters.

    Faint grid, slow rotating glowing rings centred behind the core,
    drifting particles, soft radial glow. Capped at ~15 fps.
    Pause it with ``set_paused(True)`` to save CPU / battery.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._paused = False
        self._angle = 0.0
        self._particles = [
            {
                "x": random.random(),
                "y": random.random(),
                "dx": random.uniform(-0.0004, 0.0004),
                "dy": random.uniform(0.0003, 0.0012),
                "size": random.randint(1, 3),
                "alpha": random.randint(30, 90),
            }
            for _ in range(36)
        ]
        self._timer = QTimer(self)
        self._timer.setInterval(66)  # ~15 fps
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def set_paused(self, paused: bool) -> None:
        self._paused = paused

    def is_paused(self) -> bool:
        return self._paused

    def _tick(self) -> None:
        if self._paused:
            return
        self._angle = (self._angle + 0.6) % 360
        for p in self._particles:
            p["x"] = (p["x"] + p["dx"]) % 1.0
            p["y"] = (p["y"] + p["dy"]) % 1.0
        self.update()

    def _core_center(self):
        # The core sits in the left panel: ~20% across, ~38% down.
        return self.width() * 0.20, self.height() * 0.38

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(BG))
        w, h = max(self.width(), 1), max(self.height(), 1)
        cx, cy = self._core_center()

        # Soft radial glow behind the core.
        for i in range(6, 0, -1):
            r = 46 * i
            painter.setPen(Qt.NoPen)
            painter.setBrush(_alpha(ACCENT, 10))
            painter.drawEllipse(int(cx - r), int(cy - r), int(r * 2), int(r * 2))

        # Faint grid.
        painter.setPen(QPen(_alpha(ACCENT_SOFT, 12), 1))
        for x in range(0, w, 44):
            painter.drawLine(x, 0, x, h)
        for y in range(0, h, 44):
            painter.drawLine(0, y, w, y)

        # Faint hexagons in the corners (HUD decoration).
        painter.setPen(QPen(_alpha(ACCENT_SOFT, 28), 1))
        for hx, hy in ((w - 70, 60), (70, h - 60), (w - 70, h - 60)):
            points = [
                (hx + 26 * math.cos(math.radians(60 * k)),
                 hy + 26 * math.sin(math.radians(60 * k)))
                for k in range(6)
            ]
            for k in range(6):
                x1, y1 = points[k]
                x2, y2 = points[(k + 1) % 6]
                painter.drawLine(int(x1), int(y1), int(x2), int(y2))

        # Slow rotating glowing rings around the core.
        for radius, span, color, width, speed in (
            (190, 110, ACCENT, 2, 1.0),
            (240, 70, CYAN, 1, -0.7),
            (290, 140, ACCENT_SOFT, 1, 0.45),
        ):
            painter.setPen(QPen(_alpha(color, 45), width))
            painter.setBrush(Qt.NoBrush)
            start = int((self._angle * speed) % 360 * 16)
            painter.drawArc(
                int(cx - radius), int(cy - radius),
                int(radius * 2), int(radius * 2),
                start, int(span * 16),
            )
        # Tick ring around the core.
        painter.setPen(QPen(_alpha(ACCENT_SOFT, 50), 1))
        for k in range(36):
            a = math.radians(k * 10)
            r1, r2 = 158, 166 if k % 9 == 0 else 162
            painter.drawLine(
                int(cx + r1 * math.cos(a)), int(cy + r1 * math.sin(a)),
                int(cx + r2 * math.cos(a)), int(cy + r2 * math.sin(a)),
            )

        # Drifting particles.
        for p in self._particles:
            painter.setPen(Qt.NoPen)
            painter.setBrush(_alpha(ACCENT_SOFT, p["alpha"]))
            painter.drawEllipse(
                int(p["x"] * w), int(p["y"] * h), p["size"], p["size"]
            )


# --- JARVIS core (the bot "face") -------------------------------------------
class HudCoreWidget(QWidget):
    """Glowing arc-reactor core. This IS the bot face — no smiley.

    States: "idle" (slow pulse, slow rings), "listening"/"thinking"
    (faster spin, brighter core, expanding ripple), "speaking"
    (pulsing waveform bars around the core).
    """

    STATES = {"idle", "listening", "thinking", "speaking"}

    def __init__(self, diameter: int = 260, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(diameter, diameter)
        self._diameter = diameter
        self._state = "idle"
        self._angle = 0.0
        self._pulse = 0.0
        self._ripple = 0.0
        self._bars = [0.4] * 24
        self._timer = QTimer(self)
        self._timer.setInterval(60)  # ~16 fps
        self._timer.timeout.connect(self._animate)
        self._timer.start()

    def set_state(self, state: str) -> None:
        if state in self.STATES:
            self._state = state
            self.update()

    def state(self) -> str:
        return self._state

    def _animate(self) -> None:
        speed = {"idle": 1.2, "listening": 5.0, "thinking": 5.0, "speaking": 2.5}
        self._angle = (self._angle + speed[self._state]) % 360
        self._pulse += 0.12
        if self._state in {"listening", "thinking"}:
            self._ripple = (self._ripple + 0.03) % 1.0
        if self._state == "speaking":
            self._bars = [
                0.35 + 0.65 * abs(math.sin(self._pulse * 1.7 + k * 0.9))
                for k in range(24)
            ]
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        cx = cy = self._diameter / 2.0
        r = self._diameter / 2.0 - 6
        bright = self._state in {"listening", "thinking"}

        # Outer glow.
        for i in range(5, 0, -1):
            painter.setPen(Qt.NoPen)
            painter.setBrush(_alpha(ACCENT, 8 if not bright else 12))
            gr = r * (0.55 + 0.11 * i)
            painter.drawEllipse(int(cx - gr), int(cy - gr), int(gr * 2), int(gr * 2))

        # Expanding ripple while thinking / listening.
        if self._state in {"listening", "thinking"}:
            rr = r * (0.45 + 0.55 * self._ripple)
            painter.setPen(QPen(_alpha(CYAN, int(70 * (1 - self._ripple))), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(int(cx - rr), int(cy - rr), int(rr * 2), int(rr * 2))

        # Outer ring + tick marks.
        painter.setPen(QPen(_alpha(ACCENT, 160), 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(int(cx - r), int(cy - r), int(r * 2), int(r * 2))
        painter.setPen(QPen(_alpha(ACCENT_SOFT, 120), 1))
        for k in range(48):
            a = math.radians(k * 7.5)
            r1, r2 = r - 4, r - 9 if k % 6 == 0 else r - 7
            painter.drawLine(
                int(cx + r1 * math.cos(a)), int(cy + r1 * math.sin(a)),
                int(cx + r2 * math.cos(a)), int(cy + r2 * math.sin(a)),
            )

        # Concentric rotating arcs.
        for radius, span, color, width, direction in (
            (r * 0.88, 80, CYAN, 3, 1.0),
            (r * 0.74, 130, ACCENT, 2, -0.8),
            (r * 0.60, 55, ACCENT_SOFT, 2, 1.4),
        ):
            painter.setPen(QPen(_alpha(color, 190), width))
            painter.setBrush(Qt.NoBrush)
            start = int(((self._angle * direction) % 360) * 16)
            painter.drawArc(
                int(cx - radius), int(cy - radius),
                int(radius * 2), int(radius * 2),
                start, int(span * 16),
            )

        # Waveform bars while speaking.
        if self._state == "speaking":
            painter.setPen(QPen(_alpha(CYAN, 200), 3))
            for k, level in enumerate(self._bars):
                a = math.radians(k * 15)
                r1 = r * 0.46
                r2 = r1 + r * 0.20 * level
                painter.drawLine(
                    int(cx + r1 * math.cos(a)), int(cy + r1 * math.sin(a)),
                    int(cx + r2 * math.cos(a)), int(cy + r2 * math.sin(a)),
                )

        # Pulsing core.
        core_r = r * 0.34 * (1 + 0.06 * math.sin(self._pulse))
        if bright:
            core_r *= 1.08
        for i in range(4, 0, -1):
            painter.setPen(Qt.NoPen)
            painter.setBrush(_alpha(CYAN if i % 2 == 0 else ACCENT, 26))
            gr = core_r * (0.55 + 0.22 * i)
            painter.drawEllipse(int(cx - gr), int(cy - gr), int(gr * 2), int(gr * 2))
        painter.setPen(QPen(_alpha(ACCENT_SOFT, 220), 2))
        painter.setBrush(_alpha("#2a0a4a", 220))
        painter.drawEllipse(
            int(cx - core_r), int(cy - core_r),
            int(core_r * 2), int(core_r * 2),
        )
        # Core centre dot.
        painter.setPen(Qt.NoPen)
        painter.setBrush(_alpha(CYAN, 255 if bright else 200))
        dot = max(4.0, core_r * 0.22)
        painter.drawEllipse(int(cx - dot), int(cy - dot), int(dot * 2), int(dot * 2))


# --- HUD panel frame -----------------------------------------------------------
class HudFrame(QWidget):
    """Panel with a thin glowing border and corner brackets."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background: {PANEL_SEMI};")

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        painter.setPen(QPen(_alpha(ACCENT, 70), 1))
        painter.setBrush(QColor(PANEL_SEMI))
        painter.drawRect(rect)
        # Corner brackets.
        painter.setPen(QPen(QColor(ACCENT), 2))
        L = 18
        x0, y0, x1, y1 = rect.left(), rect.top(), rect.right(), rect.bottom()
        for (sx, sy, dx, dy) in (
            (x0, y0, 1, 1), (x1, y0, -1, 1),
            (x0, y1, 1, -1), (x1, y1, -1, -1),
        ):
            painter.drawLine(sx, sy, sx + dx * L, sy)
            painter.drawLine(sx, sy, sx, sy + dy * L)


# --- LLM worker ---------------------------------------------------------------
class ChatWorker(QObject):
    """Runs llm.chat() off the UI thread. Never touches widgets."""

    finished = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, history: list[dict[str, str]]) -> None:
        super().__init__()
        self._history = list(history)

    def run(self) -> None:
        try:
            reply = chat(self._history)
        except (AriesLLMError, RuntimeError) as exc:
            # Plain words, no traceback, no crash — shown in-chat.
            self.failed.emit(str(exc))
        else:
            self.finished.emit(reply)


# --- Mini mode (stub) ----------------------------------------------------------
class MiniWindow(QWidget):
    """Small always-on-top window with the SAME JARVIS core.

    Stub for full mini mode (later module): core, greeting, status,
    and a button to come back. The core shows the same states
    (idle / thinking / speaking) as the main window.
    """

    closed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setWindowTitle("Aries — mini")
        self.setFixedSize(280, 330)
        self.setStyleSheet(f"background: {BG}; color: {TEXT};")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        self._core = HudCoreWidget(150, self)
        layout.addWidget(self._core, alignment=Qt.AlignCenter)
        label = QLabel(GREETING, self)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet(f"color: {ACCENT_SOFT}; font-weight: bold;")
        layout.addWidget(label)
        self._status = QLabel("Ready", self)
        self._status.setAlignment(Qt.AlignCenter)
        self._status.setStyleSheet(f"color: {MUTED}; font-family: {MONO};")
        layout.addWidget(self._status)
        restore = QPushButton("Back to full Aries", self)
        restore.setStyleSheet(
            f"background: {ACCENT}; color: white; padding: 6px; border-radius: 6px;"
        )
        restore.clicked.connect(self.close)
        layout.addWidget(restore)

    def set_state(self, state: str) -> None:
        """Mirror the main window's core state."""
        self._core.set_state(state)
        self._status.setText(
            {"idle": "Ready", "listening": "Listening…",
             "thinking": "Thinking…", "speaking": "Speaking…"}.get(state, state)
        )

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.closed.emit()
        super().closeEvent(event)


# --- Main window ---------------------------------------------------------------
class AriesWindow(QMainWindow):
    """Main Aries window: JARVIS HUD background, core on the left,
    chat as a HUD panel on the right."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Aries")
        self.setMinimumSize(820, 600)
        self.resize(960, 660)
        self._history: list[dict[str, str]] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
        self._thread: QThread | None = None
        self._worker: ChatWorker | None = None
        self._mini: MiniWindow | None = None
        self._key_ok = True
        self._build_ui()
        self._check_key()
        # Real clock readout, updated every second.
        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start()
        self._update_clock()

    # -- layout ------------------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)

        # HUD background fills the whole window, behind everything.
        self._bg = HudBackgroundWidget(central)
        self._bg.setGeometry(central.rect())

        # Overlay: everything the user touches, on top of the background.
        overlay = QWidget(central)
        overlay.setGeometry(central.rect())
        overlay.setStyleSheet("background: transparent;")
        root = QHBoxLayout(overlay)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)

        # ---- Left: core + status readouts --------------------------------
        left = HudFrame(overlay)
        left.setFixedWidth(340)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(16, 14, 16, 14)
        left_layout.setSpacing(8)

        title = QLabel("A R I E S", self)
        title.setStyleSheet(
            f"color: {ACCENT_SOFT}; font-size: 26px; font-weight: bold;"
            f"letter-spacing: 4px; font-family: {MONO};"
        )
        title.setAlignment(Qt.AlignCenter)
        left_layout.addWidget(title)
        sub = QLabel(GREETING, self)
        sub.setStyleSheet(f"color: {TEXT}; font-size: 12px;")
        sub.setWordWrap(True)
        sub.setAlignment(Qt.AlignCenter)
        left_layout.addWidget(sub)

        self._core = HudCoreWidget(250, self)
        left_layout.addWidget(self._core, alignment=Qt.AlignCenter)

        # Real status readouts only: time, model, key, state.
        self._status = QLabel("STATUS  Ready", self)
        self._clock = QLabel("TIME  --:--:--", self)
        model_short = CHAT_MODEL.split("/")[-1]
        self._model = QLabel(f"MODEL  {model_short}", self)
        self._model.setToolTip(CHAT_MODEL)
        self._key_label = QLabel("KEY  checking…", self)
        for lbl in (self._status, self._clock, self._model, self._key_label):
            lbl.setStyleSheet(
                f"color: {ACCENT_SOFT}; font-size: 11px; font-family: {MONO};"
            )
            lbl.setWordWrap(True)
            left_layout.addWidget(lbl)
        left_layout.addStretch(1)

        self._bg_btn = QPushButton("Pause background", self)
        self._bg_btn.setObjectName("pause_btn")
        self._bg_btn.setCheckable(True)
        self._bg_btn.clicked.connect(self._toggle_bg)
        self._mini_btn = QPushButton("Mini mode", self)
        self._mini_btn.setObjectName("mini_btn")
        self._mini_btn.clicked.connect(self._open_mini)
        for btn in (self._bg_btn, self._mini_btn):
            btn.setStyleSheet(
                f"background: {PANEL}; color: {TEXT}; padding: 6px 10px;"
                f"border: 1px solid {ACCENT}; border-radius: 6px;"
            )
            left_layout.addWidget(btn)
        root.addWidget(left)

        # ---- Right: chat HUD panel ---------------------------------------
        right = HudFrame(overlay)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(14, 12, 14, 12)
        right_layout.setSpacing(10)
        header = QLabel("// COMMS LINK — ARIES", self)
        header.setStyleSheet(
            f"color: {MUTED}; font-size: 11px; font-family: {MONO};"
            "letter-spacing: 2px;"
        )
        right_layout.addWidget(header)

        self._chat = QTextEdit(self)
        self._chat.setObjectName("chat")
        self._chat.setReadOnly(True)
        self._chat.setStyleSheet(
            f"background: transparent; color: {TEXT}; font-size: 13px;"
            "border: none; padding: 4px;"
        )
        right_layout.addWidget(self._chat, stretch=1)

        bottom = QHBoxLayout()
        self._input = QLineEdit(self)
        self._input.setObjectName("input")
        self._input.setPlaceholderText("Type a message…")
        self._input.setStyleSheet(
            f"background: {PANEL_SEMI}; color: {TEXT}; padding: 8px;"
            f"border: 1px solid {ACCENT}; border-radius: 6px;"
        )
        self._input.returnPressed.connect(self._on_send)
        bottom.addWidget(self._input, stretch=1)
        self._send_btn = QPushButton("Send", self)
        self._send_btn.setObjectName("send_btn")
        self._send_btn.setStyleSheet(
            f"background: {ACCENT}; color: white; padding: 8px 16px;"
            "border: none; border-radius: 6px; font-weight: bold;"
        )
        self._send_btn.clicked.connect(self._on_send)
        bottom.addWidget(self._send_btn)

        self._retry_btn = QPushButton("Check key again", self)
        self._retry_btn.setObjectName("retry_btn")
        self._retry_btn.setStyleSheet(
            f"background: {PANEL}; color: {TEXT}; padding: 8px;"
            f"border: 1px solid {ACCENT}; border-radius: 6px;"
        )
        self._retry_btn.clicked.connect(self._check_key)
        self._retry_btn.hide()
        bottom.addWidget(self._retry_btn)
        right_layout.addLayout(bottom)
        root.addWidget(right, stretch=1)

        self._append("Aries", GREETING)
        self._overlay = overlay

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        if hasattr(self, "_bg"):
            rect = self.centralWidget().rect()
            self._bg.setGeometry(rect)
            self._overlay.setGeometry(rect)

    # -- status readouts ------------------------------------------------------
    def _update_clock(self) -> None:
        now = datetime.datetime.now().strftime("%H:%M:%S")
        self._clock.setText(f"TIME  {now}")

    # -- key check -----------------------------------------------------------
    def _check_key(self) -> None:
        try:
            get_api_key()
        except RuntimeError as exc:
            self._key_ok = False
            self._key_label.setText("KEY  NOT SET")
            self._input.setEnabled(False)
            self._send_btn.setEnabled(False)
            self._retry_btn.show()
            self._append(
                "Aries",
                "I can't start chatting yet — no NVIDIA API key found.\n\n"
                f"{exc}\n\n"
                "Set the key, then press 'Check key again'.",
            )
            self._set_status("No API key — see setup steps above")
            return
        self._key_ok = True
        self._key_label.setText("KEY  SET")
        self._input.setEnabled(True)
        self._send_btn.setEnabled(True)
        self._retry_btn.hide()
        self._set_status("Ready")

    # -- chat ------------------------------------------------------------------
    def _append(self, who: str, text: str) -> None:
        color = ACCENT_SOFT if who == "Aries" else TEXT
        self._chat.append(
            f'<p><b><span style="color:{color}">{who}:</span></b> '
            f"{text}</p>"
        )

    def _set_status(self, text: str) -> None:
        self._status.setText(f"STATUS  {text}")

    def _set_core_state(self, state: str) -> None:
        self._core.set_state(state)
        if self._mini is not None:
            self._mini.set_state(state)

    def _on_send(self) -> None:
        if not self._key_ok:
            return
        text = self._input.text().strip()
        if not text:
            return
        self._input.clear()
        self._append("You", text)
        self._history.append({"role": "user", "content": text})
        self._set_busy(True)
        self._set_core_state("thinking")
        self._set_status("Thinking…")

        self._thread = QThread(self)
        self._worker = ChatWorker(self._history)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_reply)
        self._worker.failed.connect(self._on_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _on_reply(self, reply: str) -> None:
        self._history.append({"role": "assistant", "content": reply})
        self._append("Aries", reply)
        self._set_core_state("speaking")
        # "Speak" for a bit, scaled to reply length, then back to idle.
        ms = min(4000, max(1200, len(reply) * 12))
        QTimer.singleShot(
            ms, lambda: self._set_core_state("idle")
        )
        self._set_busy(False)
        self._set_status("Ready")

    def _on_error(self, message: str) -> None:
        self._append("Aries", f"Sorry — {message}")
        self._set_core_state("idle")
        self._set_busy(False)
        self._set_status("Error — try again")

    def _set_busy(self, busy: bool) -> None:
        self._input.setEnabled(not busy)
        self._send_btn.setEnabled(not busy)

    # -- background / mini -------------------------------------------------------
    def _toggle_bg(self) -> None:
        paused = self._bg_btn.isChecked()
        self._bg.set_paused(paused)
        self._bg_btn.setText(
            "Resume background" if paused else "Pause background"
        )

    def _open_mini(self) -> None:
        if self._mini is not None:
            return
        self._mini = MiniWindow()
        self._mini.set_state(self._core.state())
        self._mini.closed.connect(self._close_mini)
        self._mini.show()
        self.hide()

    def _close_mini(self) -> None:
        self._mini = None
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self._mini is not None:
            self._mini.close()
        super().closeEvent(event)


def run_ui() -> int:
    """Create the app and show the main window. Returns the exit code."""
    app = QApplication([])
    app.setStyleSheet(f"QWidget {{ background: {BG}; color: {TEXT}; }}")
    window = AriesWindow()
    window.show()
    return app.exec_()
