"""PyQt5 desktop UI for Aries (Module 2).

Deep purple theme (background #0d0010, accent #7b2fff), Matrix-style
code-rain background, animated bot face, and a chat pane wired to
the NVIDIA LLM in a worker thread so the UI never freezes.

Light on purpose: the rain runs at ~15 fps with a modest column
count, and the face is drawn with QPainter (no image assets), so
this stays comfortable on a ThinkPad T14 (11th Gen i5, 16 GB RAM).

Kept OUT of Module 2 on purpose: voice, face-recognition login,
permissions, DuckDuckGo search, tool calling / autonomy.
Those are later modules.
"""

from __future__ import annotations

import random
import string

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

from .config import get_api_key
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
RAIN_GREEN = "#39ff88"
ERROR_RED = "#ff6b81"

RAIN_CHARS = string.ascii_letters + string.digits + "01<>/\\|#$%&*+=~"


# --- Code rain ---------------------------------------------------------------
class CodeRainWidget(QWidget):
    """Falling-characters canvas, capped at ~15 fps, modest columns.

    Drawn behind the chat pane. Pause it with ``set_paused(True)``
    to save CPU / battery.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._paused = False
        self._columns: list[dict] = []
        self._font = QFont("Consolas", 11)
        self._timer = QTimer(self)
        self._timer.setInterval(66)  # ~15 fps
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _reset_columns(self) -> None:
        width = max(self.width(), 1)
        step = 22  # pixels between columns — modest count on purpose
        self._columns = [
            {
                "x": x,
                "y": random.randint(-300, 0),
                "speed": random.randint(2, 6),
                "length": random.randint(6, 16),
            }
            for x in range(0, width, step)
        ]

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._reset_columns()

    def set_paused(self, paused: bool) -> None:
        self._paused = paused

    def is_paused(self) -> bool:
        return self._paused

    def _tick(self) -> None:
        if self._paused:
            return
        for col in self._columns:
            col["y"] += col["speed"]
            if col["y"] - col["length"] * 16 > self.height():
                col["y"] = random.randint(-200, 0)
                col["speed"] = random.randint(2, 6)
                col["length"] = random.randint(6, 16)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(BG))
        painter.setFont(self._font)
        for col in self._columns:
            for i in range(col["length"]):
                y = col["y"] - i * 16
                if y < 0 or y > self.height():
                    continue
                # Head of the stream is bright, the tail fades out.
                alpha = max(30, 230 - i * 16)
                color = QColor(RAIN_GREEN)
                color.setAlpha(alpha if i else 255)
                painter.setPen(color)
                painter.drawText(col["x"], y, random.choice(RAIN_CHARS))


# --- Bot face ----------------------------------------------------------------
class BotFaceWidget(QWidget):
    """Simple drawn face (eyes + mouth, QPainter only, no assets).

    States: "idle", "listening", "speaking".
    - idle: slow blink, small smile.
    - listening: eyes wide, small open mouth.
    - speaking: mouth opens/closes while the reply is "playing".
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(130, 95)
        self._state = "idle"
        self._blink = False
        self._mouth_open = 0  # 0..1, animated while speaking
        self._mouth_dir = 1
        self._anim = QTimer(self)
        self._anim.setInterval(120)
        self._anim.timeout.connect(self._animate)
        self._anim.start()

    def set_state(self, state: str) -> None:
        if state in {"idle", "listening", "speaking"}:
            self._state = state
            self.update()

    def state(self) -> str:
        return self._state

    def _animate(self) -> None:
        if self._state == "speaking":
            self._mouth_open += 0.25 * self._mouth_dir
            if self._mouth_open >= 1:
                self._mouth_open, self._mouth_dir = 1, -1
            elif self._mouth_open <= 0:
                self._mouth_open, self._mouth_dir = 0, 1
            self.update()
            return
        # Idle / listening: blink every so often.
        if random.random() < 0.06:
            self._blink = True
            self.update()
            QTimer.singleShot(150, self._stop_blink)

    def _stop_blink(self) -> None:
        self._blink = False
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        # Face outline.
        painter.setPen(QPen(QColor(ACCENT), 2))
        painter.setBrush(QColor(PANEL))
        painter.drawRoundedRect(4, 4, 122, 87, 18, 18)
        eye_y, eye_h = 32, 8 if self._blink else 18
        eye_w = 20 if self._state == "listening" else 16
        for eye_x in (32, 78):
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(ACCENT_SOFT))
            painter.drawEllipse(eye_x, eye_y, eye_w, eye_h)
            painter.setBrush(QColor(BG))
            painter.drawEllipse(eye_x + eye_w // 3, eye_y + eye_h // 3, 5, 5)
        # Mouth.
        painter.setPen(QPen(QColor(ACCENT_SOFT), 2))
        painter.setBrush(Qt.NoBrush)
        if self._state == "speaking":
            open_px = int(4 + self._mouth_open * 12)
            painter.setBrush(QColor(ACCENT))
            painter.drawEllipse(52, 58, 26, open_px)
        elif self._state == "listening":
            painter.drawEllipse(56, 60, 18, 8)
        else:  # idle smile
            painter.drawArc(48, 56, 34, 16, 180 * 16, 180 * 16)


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
    """Small always-on-top window. Stub for full mini mode (later module).

    Shows the face, the greeting, and a button to come back.
    """

    closed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setWindowTitle("Aries — mini")
        self.setFixedSize(220, 180)
        self.setStyleSheet(f"background: {BG}; color: {TEXT};")
        layout = QVBoxLayout(self)
        face = BotFaceWidget(self)
        face.set_state("idle")
        layout.addWidget(face, alignment=Qt.AlignCenter)
        label = QLabel(GREETING, self)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet(f"color: {ACCENT_SOFT}; font-weight: bold;")
        layout.addWidget(label)
        restore = QPushButton("Back to full Aries", self)
        restore.setStyleSheet(
            f"background: {ACCENT}; color: white; padding: 6px; border-radius: 6px;"
        )
        restore.clicked.connect(self.close)
        layout.addWidget(restore)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.closed.emit()
        super().closeEvent(event)


# --- Main window ---------------------------------------------------------------
class AriesWindow(QMainWindow):
    """Main Aries window: rain behind, face + greeting on top, chat below."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Aries")
        self.setMinimumSize(560, 620)
        self.resize(720, 700)
        self._history: list[dict[str, str]] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
        self._thread: QThread | None = None
        self._worker: ChatWorker | None = None
        self._mini: MiniWindow | None = None
        self._key_ok = True
        self._build_ui()
        self._check_key()

    # -- layout ------------------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)

        # Rain fills the whole window, behind everything.
        self._rain = CodeRainWidget(central)
        self._rain.setGeometry(central.rect())

        # Overlay: everything the user touches, on top of the rain.
        overlay = QWidget(central)
        overlay.setGeometry(central.rect())
        overlay.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(overlay)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        top = QHBoxLayout()
        self._face = BotFaceWidget(self)
        top.addWidget(self._face)
        greet = QVBoxLayout()
        title = QLabel("ARIES", self)
        title.setStyleSheet(
            f"color: {ACCENT_SOFT}; font-size: 22px; font-weight: bold;"
        )
        greet.addWidget(title)
        sub = QLabel(GREETING, self)
        sub.setStyleSheet(f"color: {TEXT}; font-size: 13px;")
        sub.setWordWrap(True)
        greet.addWidget(sub)
        self._status = QLabel("Idle", self)
        self._status.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        greet.addWidget(self._status)
        top.addLayout(greet, stretch=1)

        self._rain_btn = QPushButton("Pause rain", self)
        self._rain_btn.setCheckable(True)
        self._rain_btn.clicked.connect(self._toggle_rain)
        self._mini_btn = QPushButton("Mini mode", self)
        self._mini_btn.clicked.connect(self._open_mini)
        for btn in (self._rain_btn, self._mini_btn):
            btn.setStyleSheet(
                f"background: {PANEL}; color: {TEXT}; padding: 6px 10px;"
                f"border: 1px solid {ACCENT}; border-radius: 6px;"
            )
        top.addWidget(self._rain_btn)
        top.addWidget(self._mini_btn)
        layout.addLayout(top)

        self._chat = QTextEdit(self)
        self._chat.setReadOnly(True)
        self._chat.setStyleSheet(
            f"background: {PANEL_SEMI}; color: {TEXT}; font-size: 13px;"
            f"border: 1px solid {ACCENT}; border-radius: 8px; padding: 8px;"
        )
        layout.addWidget(self._chat, stretch=1)

        bottom = QHBoxLayout()
        self._input = QLineEdit(self)
        self._input.setPlaceholderText("Type a message…")
        self._input.setStyleSheet(
            f"background: {PANEL_SEMI}; color: {TEXT}; padding: 8px;"
            f"border: 1px solid {ACCENT}; border-radius: 6px;"
        )
        self._input.returnPressed.connect(self._on_send)
        bottom.addWidget(self._input, stretch=1)
        self._send_btn = QPushButton("Send", self)
        self._send_btn.setStyleSheet(
            f"background: {ACCENT}; color: white; padding: 8px 16px;"
            "border: none; border-radius: 6px; font-weight: bold;"
        )
        self._send_btn.clicked.connect(self._on_send)
        bottom.addWidget(self._send_btn)

        self._retry_btn = QPushButton("Check key again", self)
        self._retry_btn.setStyleSheet(
            f"background: {PANEL}; color: {TEXT}; padding: 8px;"
            f"border: 1px solid {ACCENT}; border-radius: 6px;"
        )
        self._retry_btn.clicked.connect(self._check_key)
        self._retry_btn.hide()
        bottom.addWidget(self._retry_btn)
        layout.addLayout(bottom)

        self._append("Aries", GREETING)
        self._overlay = overlay

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        if hasattr(self, "_rain"):
            rect = self.centralWidget().rect()
            self._rain.setGeometry(rect)
            self._overlay.setGeometry(rect)

    # -- key check -----------------------------------------------------------
    def _check_key(self) -> None:
        try:
            get_api_key()
        except RuntimeError as exc:
            self._key_ok = False
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
        self._status.setText(text)

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
        self._face.set_state("listening")
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
        self._face.set_state("speaking")
        # "Speak" for a bit, scaled to reply length, then back to idle.
        ms = min(4000, max(1200, len(reply) * 12))
        QTimer.singleShot(ms, lambda: self._face.set_state("idle"))
        self._set_busy(False)
        self._set_status("Ready")

    def _on_error(self, message: str) -> None:
        self._append("Aries", f"Sorry — {message}")
        self._face.set_state("idle")
        self._set_busy(False)
        self._set_status("Error — try again")

    def _set_busy(self, busy: bool) -> None:
        self._input.setEnabled(not busy)
        self._send_btn.setEnabled(not busy)

    # -- rain / mini -----------------------------------------------------------
    def _toggle_rain(self) -> None:
        paused = self._rain_btn.isChecked()
        self._rain.set_paused(paused)
        self._rain_btn.setText("Resume rain" if paused else "Pause rain")

    def _open_mini(self) -> None:
        if self._mini is not None:
            return
        self._mini = MiniWindow()
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
