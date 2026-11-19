"""PyQt5 desktop UI for Aries (Module 2).

The web front's look, as close as PyQt5 gets: deep purple
(background #0d0010, purple #7b2fff, purple-soft #b78bff, cream
#f6ecd9), the exact visor face from production/web/src/Face.jsx
(angular hexagonal shell, one eye band, chin light strip — no
human features, expression from light and motion only), the
stacked greeting lockup (ARIES / active / Ready to cook.), one
tiny status line with a purple dot, and a minimal main view —
only the composer (text input, mic, Send) and a small gear for
Settings. The window launches fullscreen like VLC (F11 toggles,
Esc exits). Mini mode opens by pressing the face — there is no
Mini-mode button. Text is cut to the minimum.

The face is a living machine, not a static picture: it breathes,
its visor dims in a blink-like pulse, its eye band pulses and a
faint light sweeps across it (the web front's eyePulse and
scanSweep), it looks around on its own, and follows the mouse
cursor with its gaze when the cursor is near. Mini mode (0.2.2)
docks itself bottom-right, drags smoothly, and stays
always-on-top and active.

The background is the web front's veil without its baked image
assets (the snapshot push cannot ship binary files): flat deep
purple with a vignette and a soft purple glow behind the face.

Fonts: Segoe UI everywhere ("Segoe UI Semibold" for headings),
Georgia italic for the serif greeting words only.

Light on purpose: the background is static and the face is
capped at ~16 fps, all drawn with QPainter (no image assets),
so this stays comfortable on a ThinkPad T14 (11th Gen i5,
16 GB RAM). While the mini window is being dragged the face
drops to a cheap paint path, so dragging does not stutter.

PC control (0.4.0): the operating agent — open apps/files,
type, click, clipboard, shell, read/write files, system info —
max 5 steps, confirmation before acting, twice for destructive
actions, master toggle in Settings, OFF by default. Replies are
sanitised so the chat never shows raw JSON: the pc-actions block
is parsed tolerantly, executed, and replaced by a plain-words
result (0.4.1).

Kept OUT of Module 2 on purpose: voice, face-recognition login,
permissions, DuckDuckGo search. Those are later modules.
"""

from __future__ import annotations

import math
import random

from PyQt5.QtCore import (
    QEvent,
    QObject,
    QSettings,
    Qt,
    QThread,
    QTimer,
    pyqtSignal,
)
from PyQt5.QtGui import QColor, QFont, QPainter, QPen
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from . import pc_control
from .config import delete_api_key, get_api_key, key_source, set_api_key
from .llm import AriesLLMError, chat
from .main import SYSTEM_PROMPT

# --- Theme -----------------------------------------------------------------
# The web front's palette, exactly: bg #0d0010 · bg-deep #08000b ·
# purple #7b2fff · purple-soft #b78bff · cream #f6ecd9 · cream-dim ·
# hairline · panel. No gold, no makoma. Fonts: Segoe UI sans,
# Georgia serif italic for "active" and "Ready to cook." only.
BG = "#0d0010"
BG_DEEP = "#08000b"
PANEL = "#13031e"
PANEL_SEMI = "rgba(19, 3, 30, 148)"
ACCENT = "#7b2fff"
ACCENT_SOFT = "#b78bff"
TEXT = "#f6ecd9"
CREAM = "#f6ecd9"
CREAM_DIM = "#9d928d"  # cream at 62% over the deep-purple bg
HAIRLINE = "rgba(246, 236, 217, 0.16)"
MUTED = "#9d928d"
CYAN = "#35e0ff"  # web front listening colour — eye band only
ERROR_RED = "#ff5d5d"  # web front error colour
# Fonts: Segoe UI everywhere; Segoe UI Semibold for headings; Georgia
# italic for the serif greeting words ONLY.
SANS = "Segoe UI"
SANS_SEMI = "Segoe UI Semibold"
SERIF = "Georgia"


def _alpha(color: str, alpha: int) -> QColor:
    c = QColor(color)
    c.setAlpha(max(0, min(255, alpha)))
    return c


# --- Veil background --------------------------------------------------------
class VeilBackgroundWidget(QWidget):
    """The web front's veil, as close as PyQt5 gets: flat deep purple
    with a soft vignette (darker edges) and a gentle purple glow
    behind the face. No grid, no rings, no particles, no HUD chrome.

    The web front's baked grain and mountain artwork are image
    assets the desktop .exe cannot carry (the snapshot push cannot
    ship binary files), so the desktop uses this flat deep-purple
    veil instead — same colours, same calm. The background is
    static; the face above it is what moves.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

    def _core_center(self):
        # The face is the hero, centred near the top of the screen.
        return self.width() * 0.50, self.height() * 0.26

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        from PyQt5.QtGui import QRadialGradient

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(BG))
        w, h = max(self.width(), 1), max(self.height(), 1)
        cx, cy = self._core_center()

        # Soft purple glow behind the face (the web front's drop-shadow).
        glow = QRadialGradient(cx, cy, max(w, h) * 0.42)
        glow.setColorAt(0.0, _alpha(ACCENT, 26))
        glow.setColorAt(0.55, _alpha(ACCENT, 10))
        glow.setColorAt(1.0, _alpha(ACCENT, 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(glow)
        painter.drawRect(self.rect())

        # Vignette: darker toward the edges, like the web front's.
        vignette = QRadialGradient(cx, cy, max(w, h) * 0.75)
        vignette.setColorAt(0.0, _alpha(BG_DEEP, 0))
        vignette.setColorAt(0.62, _alpha(BG_DEEP, 40))
        vignette.setColorAt(1.0, _alpha(BG_DEEP, 140))
        painter.setBrush(vignette)
        painter.drawRect(self.rect())


# Backwards-compatible alias: the old name was HudBackgroundWidget.
HudBackgroundWidget = VeilBackgroundWidget


# --- Aries machine face -----------------------------------------------------
class AriesFaceWidget(QWidget):
    """The web front's visor face, exactly — rendered from the same
    SVG geometry as production/web/src/Face.jsx (viewBox 200x168):
    an angular hexagonal shell, an inner visor plate, one angular
    eye band (two light slits + a centre bar), a chin light strip,
    and faint inner circuitry. No human features, no antenna, no
    ears, no mouth — expression comes from light and motion only.

    Eye colour by state, exactly like the web front:
    - idle / thinking / speaking: purple (#8f4dff → #e6d4ff)
    - listening: cyan (#35e0ff → #d8f6ff)
    - error: red (#ff5d5d → #ffd4d4)
    The eye band gently pulses (the web front's eyePulse) and a
    faint light sweeps across the visor (its scanSweep).

    It stays alive like the desktop face always was: it breathes
    and bobs gently, the gaze drifts and follows the cursor, and
    the visor dims in a blink-like pulse. Clicking the face emits
    :attr:`clicked` — in the main window that opens mini mode.
    All animation is capped at ~16 fps. While ``_dragging`` is set
    (mini window being dragged) the paint path is cheap — no glow
    layers — so dragging stays smooth.
    """

    STATES = {"idle", "listening", "thinking", "speaking", "error"}

    # The web front's visor SVG, viewBox 200x168 (Face.jsx, exactly).
    _SHELL = [(100, 6), (176, 42), (192, 100), (150, 160), (50, 160),
              (8, 100), (24, 42)]
    _PLATE = [(100, 14), (168, 46), (182, 100), (146, 152), (54, 152),
              (18, 100), (32, 46)]
    _EYE_LEFT = [(42, 88), (88, 80), (96, 92), (88, 104), (42, 102),
                 (34, 95)]
    _EYE_RIGHT = [(158, 88), (112, 80), (104, 92), (112, 104), (158, 102),
                  (166, 95)]
    _EYE_CENTRE = (95, 90, 10, 5)  # x, y, w, h
    _CHIN = (78, 136, 44, 4)  # x, y, w, h
    # Faint inner circuitry, drawn clipped to the visor plate.
    _CIRCUIT = (
        ((30, 128), (78, 128), (92, 142)),
        ((170, 128), (122, 128), (108, 142)),
        ((100, 20), (100, 44)),
    )
    _SCAN = (18, 60, 10, 76)  # x, y, w, h — sweeps +74 px in x

    # Eye colours by state (Face.jsx EYE_COLORS; default is purple).
    _EYE_COLORS = {
        "listening": ("#35e0ff", "#d8f6ff", (53, 224, 255, 191)),
        "error": ("#ff5d5d", "#ffd4d4", (255, 93, 93, 191)),
    }
    _EYE_DEFAULT = ("#8f4dff", "#e6d4ff", (183, 139, 255, 191))
    # Glow colour follows the state, like the web front's glow.
    _GLOW_COLORS = {"listening": "#35e0ff", "error": "#ff5d5d"}
    _GLOW_DEFAULT = "#7b2fff"

    clicked = pyqtSignal()

    def __init__(self, diameter: int = 280, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(diameter, diameter)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self._diameter = diameter
        self._state = "idle"
        self._pulse = 0.0
        self._scan = 0.0  # 0..1, one sweep per ~5.5 s (web scanSweep)
        # Gaze: current (smoothed) and target, each (x, y) in -1..1.
        self._gaze = (0.0, 0.0)
        self._gaze_target = (0.0, 0.0)
        self._frames_until_look = random.randint(40, 70)
        self._frames_until_blink = random.randint(50, 100)
        self._blink = 0.0  # 0 = eyes open, 1 = fully blinked
        self._blink_dir = 0  # -1 closing, +1 opening, 0 not blinking
        self._hover = False
        self._dragging = False
        self._press_pos = None  # for click-vs-drag on the face
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

    def gaze(self):
        """Current smoothed gaze offset, (x, y) in about -1..1."""
        return self._gaze

    def set_cursor_gaze(self, gx: float, gy: float) -> None:
        """Point the gaze at a direction, clamped to -0.45..0.45."""
        gx = max(-0.45, min(0.45, gx))
        gy = max(-0.45, min(0.45, gy))
        self._gaze_target = (gx, gy)

    def set_hover(self, hover: bool) -> None:
        self._hover = hover
        self.update()

    def is_hovered(self) -> bool:
        return self._hover

    def set_dragging(self, dragging: bool) -> None:
        """Cheap paint path while dragging, so a drag never stutters."""
        self._dragging = dragging
        self.update()

    def is_dragging(self) -> bool:
        return self._dragging

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.LeftButton:
            self._press_pos = event.pos()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        # A plain click (press + release in place) opens mini mode
        # in the main window. A drag does not count as a click.
        if (event.button() == Qt.LeftButton
                and self._press_pos is not None
                and (event.pos() - self._press_pos).manhattanLength() < 8):
            self.clicked.emit()
        self._press_pos = None
        super().mouseReleaseEvent(event)

    def _pick_look_target(self) -> None:
        """Idle look-around: drift to a random nearby point."""
        self._gaze_target = (
            random.uniform(-0.35, 0.35),
            random.uniform(-0.30, 0.30),
        )

    def _update_gaze_from_cursor(self) -> bool:
        """Follow the real cursor when it is over/near this widget.

        Returns True when the cursor is driving the gaze, so the
        idle look-around does not fight it.
        """
        try:
            from PyQt5.QtGui import QCursor
            pos = QCursor.pos()
        except Exception:
            return False
        local = self.mapFromGlobal(pos)
        # Near = inside the widget, or within ~80 px of it.
        near = (-80 <= local.x() <= self.width() + 80
                and -80 <= local.y() <= self.height() + 80)
        # Hovering brightens the face, like it noticed the cursor.
        self._hover = (-10 <= local.x() <= self.width() + 10
                       and -10 <= local.y() <= self.height() + 10)
        if not near:
            return False
        cx = cy = self._diameter / 2.0
        gx = (local.x() - cx) / max(cx, 1.0)
        gy = (local.y() - cy) / max(cy, 1.0)
        self.set_cursor_gaze(gx, gy)
        return True

    def _animate(self) -> None:
        self._pulse += 0.12
        # Scan sweep across the visor, one pass per ~5.5 s (web scanSweep).
        self._scan = (self._scan + 0.0114) % 1.0
        # Gaze: cursor wins when near, otherwise idle look-around.
        # Thinking/listening look around faster, like searching.
        cursor_driving = self._update_gaze_from_cursor()
        if not cursor_driving:
            self._frames_until_look -= 1
            look_every = 14 if self._state in {"listening", "thinking"} else 55
            if self._frames_until_look <= 0:
                self._pick_look_target()
                self._frames_until_look = random.randint(
                    max(8, look_every - 10), look_every + 25)
        # Smoothly ease the gaze toward its target (never a snap).
        gx, gy = self._gaze
        tx, ty = self._gaze_target
        self._gaze = (gx + (tx - gx) * 0.15, gy + (ty - gy) * 0.15)
        # Blink: schedule one every ~3-6 s, close+open over ~4 frames.
        if self._blink_dir == 0:
            self._frames_until_blink -= 1
            if self._frames_until_blink <= 0:
                self._blink_dir = -1
        else:
            self._blink += 0.35 * self._blink_dir
            if self._blink >= 1.0:
                self._blink, self._blink_dir = 1.0, 1
            elif self._blink <= 0.0:
                self._blink, self._blink_dir = 0.0, 0
                self._frames_until_blink = random.randint(50, 100)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Draw the web front's visor SVG (Face.jsx, viewBox 200x168).

        The SVG geometry is drawn exactly — shell, inner plate,
        circuitry, eye band, centre bar, chin strip — in viewBox
        coordinates, scaled so the visor is 78% of the widget width
        (218 px at the default 280 px widget, the web front's max).
        Life comes from the breathing bob, the gaze (the bright eye
        cores shift), the blink dim, the eye pulse and the scan
        sweep — the web front's own animations.
        """
        from PyQt5.QtCore import QPointF
        from PyQt5.QtGui import (
            QLinearGradient, QPainterPath, QPolygonF, QRadialGradient,
        )

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        D = float(self._diameter)

        # --- viewBox transform: visor = 78% of the widget width ---
        scale = (0.78 * D) / 200.0
        breathe = math.sin(self._pulse)
        gx, gy = self._gaze
        ox = (D - 200 * scale) / 2.0 + gx * D * 0.02
        oy = (D - 168 * scale) / 2.0 + gy * D * 0.015 + 2.0 * breathe
        dim = 1.0 - 0.55 * self._blink
        bright = self._state in {"listening", "thinking", "speaking"} or self._hover
        # Eye pulse (web eyePulse, 3.6 s): opacity 1 -> 0.55.
        eye_alpha = 1.0 - 0.45 * (0.5 + 0.5 * math.sin(self._pulse))
        glow_color = self._GLOW_COLORS.get(self._state, self._GLOW_DEFAULT)

        eye_a, eye_b, chin_rgb = self._EYE_COLORS.get(
            self._state, self._EYE_DEFAULT)
        chin = QColor(*chin_rgb)

        def poly(points):
            return QPolygonF([QPointF(float(x), float(y)) for x, y in points])

        painter.save()
        painter.translate(ox, oy)
        painter.scale(scale, scale)

        if not self._dragging:
            # Soft glow behind the shell (web drop-shadow), coloured
            # by state — purple normally, cyan when listening, red
            # on error.
            glow = QRadialGradient(100, 84, 130)
            glow.setColorAt(0.0, _alpha(glow_color, int(70 * dim)))
            glow.setColorAt(0.6, _alpha(glow_color, int(22 * dim)))
            glow.setColorAt(1.0, _alpha(glow_color, 0))
            painter.setPen(Qt.NoPen)
            painter.setBrush(glow)
            painter.drawEllipse(-30, -46, 260, 260)

        # --- Shell: vertical gradient, purple stroke ---
        shell_grad = QLinearGradient(0, 0, 0, 168)
        shell_grad.setColorAt(0.0, QColor("#2a0a44"))
        shell_grad.setColorAt(0.55, QColor("#170327"))
        shell_grad.setColorAt(1.0, QColor("#0d0010"))
        painter.setPen(QPen(QColor(ACCENT), 2.5))
        painter.setBrush(shell_grad)
        painter.drawPolygon(poly(self._SHELL))

        # --- Inner visor plate ---
        painter.setPen(QPen(_alpha(ACCENT_SOFT, int(0.35 * 255 * dim)), 1))
        painter.setBrush(QColor("#12021f"))
        painter.drawPolygon(poly(self._PLATE))

        # --- Faint inner circuitry, clipped to the plate ---
        plate_path = QPainterPath()
        plate_path.addPolygon(poly(self._PLATE))
        painter.save()
        painter.setClipPath(plate_path)
        painter.setPen(QPen(_alpha(ACCENT, int(0.4 * 255 * dim)), 2))
        painter.setBrush(Qt.NoBrush)
        for points in self._CIRCUIT:
            path = QPainterPath()
            path.moveTo(float(points[0][0]), float(points[0][1]))
            for x, y in points[1:]:
                path.lineTo(float(x), float(y))
            painter.drawPath(path)
        # --- Scanning light (web scanSweep): a faint bar sweeping
        # across the plate, visible for the first half of each cycle.
        if self._scan < 0.46:
            sweep = self._scan / 0.46
            opacity = 0.85 * min(1.0, self._scan / 0.12)
            sx, sy, sw, sh = self._SCAN
            painter.setPen(Qt.NoPen)
            painter.setBrush(_alpha(
                ACCENT_SOFT, int(0.16 * 255 * opacity * dim)))
            painter.drawRect(
                int(sx + 74 * sweep), int(sy), int(sw), int(sh))
        painter.restore()

        # --- Eye band: two angular light slits + centre bar ---
        eye_grad = QLinearGradient(0, 0, 200, 0)
        eye_grad.setColorAt(0.0, _alpha(eye_a, int(255 * eye_alpha * dim)))
        eye_grad.setColorAt(0.5, _alpha(eye_b, int(255 * eye_alpha * dim)))
        eye_grad.setColorAt(1.0, _alpha(eye_a, int(255 * eye_alpha * dim)))
        painter.setPen(Qt.NoPen)
        painter.setBrush(eye_grad)
        painter.drawPolygon(poly(self._EYE_LEFT))
        painter.drawPolygon(poly(self._EYE_RIGHT))
        cx_, cy_, cw_, ch_ = self._EYE_CENTRE
        painter.fillRect(int(cx_), int(cy_), int(cw_), int(ch_),
                         _alpha(eye_b, int(255 * eye_alpha * dim)))
        # Bright cores — they look where the gaze points.
        for sign in (-1, 1):
            core_x = 100 + sign * 35 + gx * 8
            core_y = 92 + gy * 5
            if self._state in {"listening", "thinking"}:
                core_y -= 2
            cr = 5.0
            painter.setBrush(_alpha("#ffffff", int(240 * eye_alpha * dim)))
            painter.drawEllipse(int(core_x - cr), int(core_y - cr),
                                int(cr * 2), int(cr * 2))

        # --- Chin light strip ---
        chx, chy, chw, chh = self._CHIN
        painter.setPen(Qt.NoPen)
        painter.setBrush(_alpha(chin, int(chin.alpha() * dim)))
        painter.drawRoundedRect(int(chx), int(chy), int(chw), int(chh), 2, 2)

        painter.restore()

# Backwards-compatible alias: the old name was HudCoreWidget.
HudCoreWidget = AriesFaceWidget


# --- HUD panel frame -----------------------------------------------------------
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
    """Small always-on-top window with the SAME robot face.

    Stub for full mini mode (later module): face, greeting, status,
    and a button to come back. The face shows the same states
    (idle / thinking / speaking) as the main window, and is just as
    lively — breathing, blinking, looking around, following the
    cursor with its gaze, mouth moving when it speaks.

    Smooth dragging (0.2.2): the old version had no drag handling
    at all, so a drag fell back to the OS moving a window whose core
    repainted heavily on every move — that is what stuttered. Now
    dragging is done here, directly: press anywhere (core included),
    remember the press offset, and move() the window on every
    mouse-move. While dragging the core drops to its cheap paint
    path and its animation timer is not asked to repaint the whole
    window, so the move stays smooth. The window is also
    mouse-tracked, so hovering brightens the core / draws its gaze.

    Auto-dock: on first show it docks itself bottom-right, 16 px
    from the right and bottom edges of the screen's *available*
    geometry (above the taskbar). It stays draggable from there.
    """

    DOCK_MARGIN = 16

    closed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            Qt.WindowStaysOnTopHint | Qt.Tool | Qt.Window,
        )
        self.setWindowTitle("Aries — mini")
        self.setFixedSize(280, 330)
        self.setStyleSheet(f"background: {BG}; color: {TEXT};")
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_AlwaysShowToolTips, True)
        self._drag_offset = None
        self._dragging = False
        self._user_moved = False
        self._docked = False
        # Web-front mini: the small visor (84 px there), greeting and
        # status, and a pill button to come back.
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        self._core = AriesFaceWidget(120, self)
        self._core.installEventFilter(self)
        layout.addWidget(self._core, alignment=Qt.AlignCenter)
        label = QLabel(
            f'<span style="font-family: {SANS_SEMI}; font-size: 16px; '
            f'font-weight: bold; color: {TEXT};">Aries active.</span> '
            f'<span style="font-family: {SERIF}; font-size: 16px; '
            f'font-style: italic; color: {CREAM};">Ready to cook.</span>'
        )
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)
        self._status = QLabel("READY", self)
        self._status.setAlignment(Qt.AlignCenter)
        self._status.setStyleSheet(
            f"color: {MUTED}; font-family: {SANS}; font-size: 11px;"
            "letter-spacing: 3px;"
        )
        layout.addWidget(self._status)
        restore = QPushButton("Back to full Aries", self)
        restore.setMinimumHeight(44)
        restore.setStyleSheet(
            f"background: {ACCENT}; color: white; padding: 8px 18px;"
            f"border: none; border-radius: 22px; font-weight: bold;"
            f"font-family: {SANS_SEMI}; font-size: 13px;"
        )
        restore.clicked.connect(self.close)
        layout.addWidget(restore)

    # -- auto-dock bottom-right --------------------------------------------
    def dock_bottom_right(self) -> None:
        """Move to the bottom-right corner, above the taskbar."""
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()  # excludes the taskbar
        x = avail.x() + avail.width() - self.width() - self.DOCK_MARGIN
        y = avail.y() + avail.height() - self.height() - self.DOCK_MARGIN
        self.move(x, y)
        self._docked = True

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().showEvent(event)
        if not self._docked and not self._user_moved:
            self.dock_bottom_right()
        # Always active: stay on top and take focus when shown.
        self.raise_()
        self.activateWindow()

    # -- smooth dragging ------------------------------------------------------
    def _begin_drag(self, global_pos) -> None:
        self._drag_offset = global_pos - self.pos()
        self._dragging = True
        self._core.set_dragging(True)

    def _move_drag(self, global_pos) -> None:
        if not self._dragging or self._drag_offset is None:
            return
        # Move the window directly — no layout, no full repaint.
        self.move(global_pos - self._drag_offset)
        self._user_moved = True

    def _end_drag(self) -> None:
        self._dragging = False
        self._drag_offset = None
        self._core.set_dragging(False)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.LeftButton:
            self._begin_drag(event.globalPos())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._move_drag(event.globalPos())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._end_drag()
        super().mouseReleaseEvent(event)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt naming
        # Dragging must work when the press starts on the core (a
        # child widget would otherwise swallow the mouse events),
        # and hovering the core must brighten it / draw its gaze.
        if watched is self._core:
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._begin_drag(event.globalPos())
                return True
            if event.type() == QEvent.MouseMove:
                self._move_drag(event.globalPos())
                return True
            if event.type() == QEvent.MouseButtonRelease:
                self._end_drag()
                return True
            if event.type() == QEvent.Enter:
                self._core.set_hover(True)
                return False
            if event.type() == QEvent.Leave:
                self._core.set_hover(False)
                return False
        return super().eventFilter(watched, event)

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._core.set_hover(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._core.set_hover(False)
        super().leaveEvent(event)

    def set_state(self, state: str) -> None:
        """Mirror the main window's core state."""
        self._core.set_state(state)
        self._status.setText(
            {"idle": "READY", "listening": "LISTENING",
             "thinking": "THINKING", "speaking": "SPEAKING"}.get(
                state, state.upper())
        )

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.closed.emit()
        super().closeEvent(event)


# --- Settings ---------------------------------------------------------------
class SettingsDialog(QDialog):
    """Settings: NVIDIA API key + the PC control master toggle.

    API key — the field saves to the OS credential store via keyring
    (service "Aries", username "nvidia_api_key"), the same entry
    ``config.get_api_key()`` reads. The key is never shown back: the
    field is password-masked, it is cleared after saving, and the
    status line only says *where* a key comes from. With a saved
    key the .exe works standalone — no environment variable needed.
    (If NVIDIA_API_KEY is set as an environment variable, that one
    still wins; the status line says so.)

    PC control — the master toggle for ``aries.pc_control``: OFF by
    default, saved per user with QSettings (Windows: the registry).
    Even when it is on, every plan still asks for confirmation
    first, and destructive actions ask a second time.
    """

    settings_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Aries — Settings")
        self.setMinimumWidth(480)
        self.setStyleSheet(
            f"background: {BG}; color: {TEXT}; font-family: {SANS};"
        )
        self._settings = QSettings("Aries", "AriesDesktop")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        # ---- NVIDIA API key --------------------------------------------
        key_title = QLabel("NVIDIA API key", self)
        key_title.setStyleSheet(
            f"font-family: {SANS_SEMI}; font-size: 15px; font-weight: bold;"
        )
        layout.addWidget(key_title)
        key_note = QLabel(
            "Saved in the OS credential store (Windows Credential "
            "Manager) — the .exe then works on its own, no environment "
            "variable needed. If NVIDIA_API_KEY is set as an environment "
            "variable, that one wins instead. The key is never shown "
            "here, printed, or logged.",
            self,
        )
        key_note.setWordWrap(True)
        key_note.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        layout.addWidget(key_note)
        self._key_status = QLabel(self)
        self._key_status.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        layout.addWidget(self._key_status)

        key_row = QHBoxLayout()
        key_row.setSpacing(10)
        self._key_input = QLineEdit(self)
        self._key_input.setEchoMode(QLineEdit.Password)
        self._key_input.setPlaceholderText("Paste your NVIDIA API key here")
        self._key_input.setMinimumHeight(44)
        self._key_input.setStyleSheet(
            f"background: {PANEL_SEMI}; color: {TEXT}; "
            f"padding: 8px 16px; border: 1px solid {ACCENT_SOFT}; "
            "border-radius: 22px;"
        )
        key_row.addWidget(self._key_input, stretch=1)
        save_btn = QPushButton("Save key", self)
        save_btn.setMinimumHeight(44)
        save_btn.setStyleSheet(
            f"background: {ACCENT}; color: white; padding: 8px 20px;"
            f"border: none; border-radius: 22px; font-weight: bold;"
            f"font-family: {SANS_SEMI};"
        )
        save_btn.clicked.connect(self._on_save_key)
        key_row.addWidget(save_btn)
        layout.addLayout(key_row)

        self._key_message = QLabel(self)
        self._key_message.setWordWrap(True)
        self._key_message.setStyleSheet(f"color: {ACCENT_SOFT}; font-size: 12px;")
        layout.addWidget(self._key_message)

        clear_btn = QPushButton("Remove the saved key", self)
        clear_btn.setStyleSheet(
            f"background: transparent; color: {MUTED}; padding: 6px 12px;"
            f"border: 1px solid {ACCENT_SOFT}; border-radius: 16px;"
        )
        clear_btn.clicked.connect(self._on_clear_key)
        layout.addWidget(clear_btn, alignment=Qt.AlignLeft)

        # ---- PC control ---------------------------------------------------
        pc_title = QLabel("PC control", self)
        pc_title.setStyleSheet(
            f"font-family: {SANS_SEMI}; font-size: 15px; font-weight: bold;"
        )
        layout.addWidget(pc_title)
        self._pc_check = QCheckBox("Allow PC control", self)
        self._pc_check.setStyleSheet(f"font-size: 14px;")
        self._pc_check.setChecked(bool(
            self._settings.value("pc_control_enabled", False, type=bool)
        ))
        self._pc_check.toggled.connect(self._on_pc_toggle)
        layout.addWidget(self._pc_check)
        pc_note = QLabel(
            "When this is on, Aries can open apps and files, type, "
            "click, use the clipboard, run commands, read/write files "
            "and check system info on this PC — but ONLY after you "
            "confirm each plan in a pop-up. Destructive actions "
            "(shutdown, restart, delete, overwriting a file) always "
            "ask a second time. Off by default. Desktop only — the "
            "webapp never gets this.",
            self,
        )
        pc_note.setWordWrap(True)
        pc_note.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        layout.addWidget(pc_note)

        layout.addStretch(1)
        done_btn = QPushButton("Done", self)
        done_btn.setMinimumHeight(44)
        done_btn.setStyleSheet(
            f"background: {ACCENT}; color: white; padding: 8px 24px;"
            f"border: none; border-radius: 22px; font-weight: bold;"
            f"font-family: {SANS_SEMI}; font-size: 14px;"
        )
        done_btn.clicked.connect(self.accept)
        layout.addWidget(done_btn, alignment=Qt.AlignRight)
        self._refresh_key_status()

    def _refresh_key_status(self) -> None:
        source = key_source()
        if source == "environment variable":
            text = ("Key status: set (from the NVIDIA_API_KEY environment "
                    "variable — that one wins over a saved key).")
        elif source == "saved in the OS credential store":
            text = ("Key status: set (saved in the OS credential store).")
        else:
            text = "Key status: not set."
        self._key_status.setText(text)

    def _on_save_key(self) -> None:
        try:
            set_api_key(self._key_input.text())
        except (ValueError, RuntimeError) as exc:
            self._key_message.setStyleSheet(
                f"color: {ERROR_RED}; font-size: 12px;"
            )
            self._key_message.setText(str(exc))
            return
        self._key_input.clear()
        self._key_message.setStyleSheet(
            f"color: {ACCENT_SOFT}; font-size: 12px;"
        )
        self._key_message.setText(
            "Saved to the OS credential store. The key itself is never shown."
        )
        self._refresh_key_status()
        self.settings_changed.emit()

    def _on_clear_key(self) -> None:
        if delete_api_key():
            self._key_message.setStyleSheet(
                f"color: {ACCENT_SOFT}; font-size: 12px;"
            )
            self._key_message.setText("Saved key removed.")
        else:
            self._key_message.setStyleSheet(
                f"color: {MUTED}; font-size: 12px;"
            )
            self._key_message.setText("There was no saved key to remove.")
        self._refresh_key_status()
        self.settings_changed.emit()

    def _on_pc_toggle(self, checked: bool) -> None:
        self._settings.setValue("pc_control_enabled", bool(checked))
        self.settings_changed.emit()


# --- Main window ---------------------------------------------------------------
class AriesWindow(QMainWindow):
    """Main Aries window: the web front's look on the desktop.

    Fullscreen like VLC by default (F11 toggles, Esc exits).
    One vertical column, minimal: a small gear top-right, the
    web-front visor face centred and first, the stacked greeting
    lockup (ARIES / active / Ready to cook.), one tiny status line,
    then the chat as cream text straight on the veil, and the
    composer pill at the bottom (text input, mic, Send). No
    Pause-background button, no Mini-mode button — pressing the
    face opens mini mode. No TIME / MODEL / KEY readout block,
    no big text sections."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Aries")
        # Windowed size, used when leaving fullscreen.
        self.setMinimumSize(800, 600)
        self.resize(1280, 800)
        self._was_fullscreen = False
        self._history: list[dict[str, str]] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
        self._thread: QThread | None = None
        self._worker: ChatWorker | None = None
        self._mini: MiniWindow | None = None
        self._key_ok = True
        # PC control (desktop only): master toggle from Settings,
        # OFF by default. _pc_round_used allows one action plan per
        # user message; _pc_followup_pending marks the summary reply
        # that follows a plan, so it is never treated as a new plan.
        self._settings = QSettings("Aries", "AriesDesktop")
        self._pc_control_enabled = bool(
            self._settings.value("pc_control_enabled", False, type=bool)
        )
        self._pc_round_used = False
        self._pc_followup_pending = False
        self._build_ui()
        self._check_key()
        self._refresh_system_prompt()

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
        # Sleek single column: face first, chat under it.
        root = QVBoxLayout(overlay)
        root.setContentsMargins(28, 16, 28, 28)
        root.setSpacing(12)

        # Tiny gear + fullscreen buttons, top-right, out of the way —
        # the web front's gear-btn: a small round icon, not a labelled
        # button. The fullscreen toggle stays (desktop need), small
        # and styled the same way.
        top = QHBoxLayout()
        top.addStretch(1)
        self._settings_btn = QPushButton("⚙", self)
        self._settings_btn.setObjectName("settings_btn")
        self._settings_btn.setToolTip("Settings")
        self._settings_btn.clicked.connect(self._open_settings)
        self._settings_btn.setFixedSize(38, 38)
        self._settings_btn.setStyleSheet(
            f"background: {PANEL_SEMI}; color: {TEXT}; "
            f"font-family: {SANS}; font-size: 17px;"
            f"border: 1px solid {HAIRLINE}; border-radius: 19px;"
        )
        top.addWidget(self._settings_btn)
        self._fs_btn = QPushButton("⛶", self)
        self._fs_btn.setObjectName("fullscreen_btn")
        self._fs_btn.setToolTip("Fullscreen (F11)")
        self._fs_btn.clicked.connect(self._toggle_fullscreen)
        self._fs_btn.setFixedSize(38, 38)
        self._fs_btn.setStyleSheet(
            f"background: {PANEL_SEMI}; color: {TEXT}; "
            f"font-family: {SANS}; font-size: 15px;"
            f"border: 1px solid {HAIRLINE}; border-radius: 19px;"
        )
        top.addWidget(self._fs_btn)
        root.addLayout(top)

        # ---- The face: the web front's visor, first thing you see -----
        self._core = AriesFaceWidget(280, self)
        self._core.setToolTip("Click the face for mini mode")
        self._core.clicked.connect(self._open_mini)
        root.addWidget(self._core, alignment=Qt.AlignCenter)

        # ---- Greeting lockup, exactly like the web front --------------
        # ARIES in bold tracked sans, "active" in Georgia italic,
        # "Ready to cook." in Georgia italic, dimmer — stacked.
        greet_wrap = QVBoxLayout()
        greet_wrap.setSpacing(2)
        greet_wrap.setContentsMargins(0, 0, 0, 0)
        word_aries = QLabel("ARIES", self)
        word_aries.setAlignment(Qt.AlignCenter)
        word_aries.setStyleSheet(
            f"color: {CREAM}; font-family: {SANS_SEMI}; font-size: 56px;"
            "font-weight: bold; letter-spacing: 9px;"
        )
        greet_wrap.addWidget(word_aries)
        word_active = QLabel("active", self)
        word_active.setAlignment(Qt.AlignCenter)
        word_active.setStyleSheet(
            f"color: {CREAM}; font-family: {SERIF}; font-size: 30px;"
            "font-style: italic;"
        )
        greet_wrap.addWidget(word_active)
        word_cook = QLabel("Ready to cook.", self)
        word_cook.setAlignment(Qt.AlignCenter)
        word_cook.setStyleSheet(
            f"color: {CREAM_DIM}; font-family: {SERIF}; font-size: 20px;"
            "font-style: italic;"
        )
        greet_wrap.addWidget(word_cook)
        root.addLayout(greet_wrap)

        # ---- One tiny status line — the only readout ----------------------
        self._status = QLabel("READY", self)
        self._status.setAlignment(Qt.AlignCenter)
        self._status.setStyleSheet(
            f"color: {MUTED}; font-size: 11px; font-family: {SANS};"
            "letter-spacing: 3px;"
        )
        root.addWidget(self._status)

        # ---- Chat: cream text straight on the veil, no panel, no
        # bubble — like the web front's latest reply. The log keeps
        # the conversation (the user needs to see PC results), but
        # visually it is just text on the deep purple.
        self._chat = QTextEdit(self)
        self._chat.setObjectName("chat")
        self._chat.setReadOnly(True)
        self._chat.setStyleSheet(
            f"background: transparent; color: {TEXT}; "
            f"font-family: {SANS}; font-size: 15px;"
            "border: none; padding: 8px; line-height: 1.6;"
        )
        root.addWidget(self._chat, stretch=1)

        # ---- Composer: the web front's dock pill — dark purple panel,
        # hairline border, input + mic + solid purple Send.
        bottom = QHBoxLayout()
        bottom.setSpacing(10)
        composer = QWidget(self)
        composer.setStyleSheet(
            f"background: {PANEL_SEMI}; border: 1px solid {HAIRLINE};"
            "border-radius: 24px;"
        )
        composer_row = QHBoxLayout(composer)
        composer_row.setContentsMargins(6, 4, 6, 4)
        composer_row.setSpacing(6)
        self._input = QLineEdit(self)
        self._input.setObjectName("input")
        self._input.setPlaceholderText("Type a message…")
        self._input.setMinimumHeight(40)
        self._input.setStyleSheet(
            f"background: transparent; color: {TEXT}; border: none;"
            f"font-family: {SANS}; font-size: 14px; padding: 8px 12px;"
        )
        self._input.returnPressed.connect(self._on_send)
        composer_row.addWidget(self._input, stretch=1)
        self._mic_btn = QPushButton("🎙", self)
        self._mic_btn.setObjectName("mic_btn")
        self._mic_btn.setToolTip("Voice input")
        self._mic_btn.setFixedSize(40, 40)
        self._mic_btn.setStyleSheet(
            f"background: transparent; color: {TEXT}; "
            f"border: 1px solid {HAIRLINE}; border-radius: 20px;"
            f"font-family: {SANS}; font-size: 16px;"
        )
        self._mic_btn.clicked.connect(self._on_mic)
        composer_row.addWidget(self._mic_btn)
        self._send_btn = QPushButton("Send", self)
        self._send_btn.setObjectName("send_btn")
        self._send_btn.setMinimumHeight(40)
        self._send_btn.setStyleSheet(
            f"background: {ACCENT}; color: white; padding: 8px 22px;"
            f"border: none; border-radius: 20px; font-weight: bold;"
            f"font-family: {SANS_SEMI}; font-size: 14px;"
        )
        self._send_btn.clicked.connect(self._on_send)
        composer_row.addWidget(self._send_btn)
        bottom.addWidget(composer, stretch=1)

        self._retry_btn = QPushButton("Check key again", self)
        self._retry_btn.setObjectName("retry_btn")
        self._retry_btn.setMinimumHeight(48)
        self._retry_btn.setStyleSheet(
            f"background: transparent; color: {TEXT}; padding: 8px 18px;"
            f"border: 1px solid {ACCENT_SOFT}; border-radius: 24px;"
            f"font-family: {SANS_SEMI}; font-size: 14px;"
        )
        self._retry_btn.clicked.connect(self._check_key)
        self._retry_btn.hide()
        bottom.addWidget(self._retry_btn)
        root.addLayout(bottom)

        self._overlay = overlay

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        if hasattr(self, "_bg"):
            rect = self.centralWidget().rect()
            self._bg.setGeometry(rect)
            self._overlay.setGeometry(rect)

    # -- fullscreen (VLC-style) --------------------------------------------
    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()
        self._update_fs_btn()

    def _update_fs_btn(self) -> None:
        # Icon-only button: just update the tooltip, never the label.
        if hasattr(self, "_fs_btn"):
            self._fs_btn.setToolTip(
                "Exit fullscreen (Esc)" if self.isFullScreen()
                else "Fullscreen (F11)"
            )

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.key() == Qt.Key_F11:
            self._toggle_fullscreen()
            return
        if event.key() == Qt.Key_Escape and self.isFullScreen():
            self.showNormal()
            self._update_fs_btn()
            return
        super().keyPressEvent(event)

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

    # -- settings ------------------------------------------------------------
    def _open_settings(self) -> None:
        dialog = SettingsDialog(self)
        dialog.settings_changed.connect(self._on_settings_changed)
        dialog.exec_()

    def _on_settings_changed(self) -> None:
        """A setting changed (key saved/cleared, PC toggle flipped)."""
        self._pc_control_enabled = bool(
            self._settings.value("pc_control_enabled", False, type=bool)
        )
        self._refresh_system_prompt()
        self._check_key()

    def _refresh_system_prompt(self) -> None:
        """System prompt, plus the PC control rules when it is on."""
        prompt = SYSTEM_PROMPT
        if self._pc_control_enabled:
            prompt += "\n\n" + pc_control.PC_CONTROL_INSTRUCTIONS
        self._history[0] = {"role": "system", "content": prompt}

    # -- chat ------------------------------------------------------------------
    def _append(self, who: str, text: str) -> None:
        color = ACCENT_SOFT if who == "Aries" else TEXT
        self._chat.append(
            f'<p><b><span style="color:{color}">{who}:</span></b> '
            f"{text}</p>"
        )

    def _set_status(self, text: str) -> None:
        # Tiny letterspaced label, with ONE purple dot (web front).
        clean = text.replace("…", "").strip().upper()
        self._status.setTextFormat(Qt.RichText)
        self._status.setText(
            f'<span style="color:{ACCENT_SOFT}">●</span>  {clean}'
        )

    def _set_core_state(self, state: str) -> None:
        self._core.set_state(state)
        if self._mini is not None:
            self._mini.set_state(state)

    def _start_chat_worker(self) -> None:
        """Run llm.chat() for the current history off the UI thread."""
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

    def _on_mic(self) -> None:
        """The mic button in the composer (web-front parity).

        Voice input is not in the desktop build yet — the webapp has
        the mic. This says so plainly instead of pretending to work.
        """
        self._append(
            "Aries",
            "The mic isn't in the desktop app yet — type your message "
            "for now. Voice input works on the website.",
        )

    def _on_send(self) -> None:
        if not self._key_ok:
            return
        text = self._input.text().strip()
        if not text:
            return
        self._input.clear()
        self._append("You", text)
        self._history.append({"role": "user", "content": text})
        self._pc_round_used = False
        self._set_busy(True)
        self._set_core_state("thinking")
        self._set_status("Thinking…")
        self._start_chat_worker()

    def _finish_reply(self, reply: str) -> None:
        """Common tail of a reply: speaking state, then back to idle."""
        self._set_core_state("speaking")
        # "Speak" for a bit, scaled to reply length, then back to idle.
        ms = min(4000, max(1200, len(reply) * 12))
        QTimer.singleShot(
            ms, lambda: self._set_core_state("idle")
        )
        self._set_busy(False)
        self._set_status("Ready")

    def _on_reply(self, reply: str) -> None:
        # A follow-up reply after PC actions ran: display it plainly,
        # never treat it as a new plan — and never show raw JSON from it.
        if self._pc_followup_pending:
            self._pc_followup_pending = False
            display = pc_control.sanitize_display(reply)
            self._history.append({"role": "assistant", "content": display})
            if display:
                self._append("Aries", display)
            self._finish_reply(display or reply)
            return
        try:
            actions = pc_control.extract_pc_actions(reply)
        except pc_control.PcControlError as exc:
            # The plan was malformed: show the plain-words reply plus
            # one clean error line. The raw JSON block is never shown.
            display = pc_control.sanitize_display(reply)
            self._history.append({"role": "assistant", "content": display})
            if display:
                self._append("Aries", display)
            self._append("Aries", f"Sorry — {exc}")
            self._finish_reply(display or reply)
            return
        display = pc_control.sanitize_display(reply)
        # History keeps plain words only, never the raw JSON block —
        # otherwise the model echoes raw JSON back in later turns.
        if actions:
            history_text = display or (
                "I will do this on your PC:\n"
                + "\n".join(
                    f"- {pc_control.describe_action(step)}"
                    for step in actions
                )
            )
        else:
            history_text = display
        self._history.append({"role": "assistant", "content": history_text})
        # When the reply was only the action block, the display is
        # empty — say nothing in chat; the confirmation dialog shows
        # the plan right away, and the result report follows.
        if display:
            self._append("Aries", display)
        if not actions:
            self._finish_reply(display or reply)
            return
        if not self._pc_control_enabled:
            self._append(
                "Aries",
                "That would need PC control, which is off. Turn on "
                "“Allow PC control” in Settings (gear icon, top right) "
                "and ask again.",
            )
            self._finish_reply(display or reply)
            return
        if self._pc_round_used:
            self._append(
                "Aries",
                "I already ran one PC plan for this message, so I "
                "stopped there — ask again if you want more done.",
            )
            self._finish_reply(display or reply)
            return
        self._pc_round_used = True
        self._handle_pc_actions(actions)

    def _handle_pc_actions(self, actions: list[dict]) -> None:
        """Confirm a PC plan, run it, then ask the model to sum up."""
        try:
            pc_control.validate_steps(actions)
        except pc_control.PcControlError as exc:
            self._append("Aries", f"Sorry — {exc}")
            self._finish_reply("")
            return
        lines = []
        destructive = False
        for i, step in enumerate(actions, 1):
            if pc_control.is_destructive(step):
                destructive = True
                lines.append(
                    f"{i}. {pc_control.describe_action(step)}"
                    "  ⚠ DESTRUCTIVE"
                )
            else:
                lines.append(f"{i}. {pc_control.describe_action(step)}")
        plan = "\n".join(lines)
        self._set_core_state("thinking")
        self._set_status("Waiting for your OK…")
        # First confirmation: every plan, every time. Default is No.
        answer = QMessageBox.question(
            self,
            "Aries wants to do this on your PC",
            "Aries is asking to do these steps on your PC:\n\n"
            + plan
            + "\n\nNothing runs until you say yes.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            self._append(
                "Aries",
                "OK — I didn't do anything. Nothing on the PC was changed.",
            )
            self._history.append(
                {"role": "user", "content": "No — do not run that plan."}
            )
            self._finish_reply("")
            return
        if destructive:
            # Second confirmation, with a warning, for destructive steps.
            answer2 = QMessageBox.warning(
                self,
                "Destructive step — are you sure?",
                "This plan includes a DESTRUCTIVE step (shutdown, "
                "restart, delete, or overwriting a file). It cannot be "
                "undone.\n\n" + plan + "\n\nAre you really sure?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer2 != QMessageBox.Yes:
                self._append(
                    "Aries",
                    "OK — I didn't do anything. Nothing on the PC was changed.",
                )
                self._history.append(
                    {"role": "user", "content": "No — do not run that plan."}
                )
                self._finish_reply("")
                return
        self._set_status("Doing it…")
        results = pc_control.execute_steps(actions)
        report_lines = []
        for step, result in zip(actions, results):
            tick = "✓" if result["ok"] else "✗"
            report_lines.append(
                f"{tick} {pc_control.describe_action(step)}"
                f" — {result['summary']}"
            )
        report = "\n".join(report_lines)
        self._append("Aries", "Done — here's what happened:\n" + report)
        # Ask the model to sum the results up in plain words.
        self._history.append(
            {
                "role": "user",
                "content": (
                    "PC action results (tell me what happened in plain "
                    "words, briefly):\n" + report
                ),
            }
        )
        self._pc_followup_pending = True
        self._set_busy(True)
        self._set_core_state("thinking")
        self._set_status("Thinking…")
        self._start_chat_worker()

    def _on_error(self, message: str) -> None:
        self._pc_followup_pending = False
        self._append("Aries", f"Sorry — {message}")
        self._set_core_state("error")
        self._set_busy(False)
        self._set_status("Error — try again")
        QTimer.singleShot(1800, lambda: self._set_core_state("idle"))

    def _set_busy(self, busy: bool) -> None:
        self._input.setEnabled(not busy)
        self._send_btn.setEnabled(not busy)

    # -- mini mode (opened by pressing the face) ---------------------------
    def _open_mini(self) -> None:
        if self._mini is not None:
            return
        self._was_fullscreen = self.isFullScreen()
        self._mini = MiniWindow()
        self._mini.set_state(self._core.state())
        self._mini.closed.connect(self._close_mini)
        # Dock bottom-right (above the taskbar) before showing.
        self._mini.dock_bottom_right()
        self._mini.show()
        self._mini.raise_()
        self._mini.activateWindow()
        self.hide()

    def _close_mini(self) -> None:
        self._mini = None
        if self._was_fullscreen:
            self.showFullScreen()
        else:
            self.show()
        self._update_fs_btn()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self._mini is not None:
            self._mini.close()
        super().closeEvent(event)


def run_ui() -> int:
    """Create the app and show the main window. Returns the exit code.

    Fullscreen by default, like VLC (F11 toggles, Esc exits).
    The app font is Segoe UI throughout (0.3.2) — headings use
    Segoe UI Semibold via stylesheets, Georgia italic only for
    the serif greeting word.
    """
    app = QApplication([])
    app.setFont(QFont("Segoe UI", 13))
    app.setStyleSheet(f"QWidget {{ background: {BG}; color: {TEXT}; "
                      f"font-family: {SANS}; }}")
    window = AriesWindow()
    window.showFullScreen()
    window._update_fs_btn()
    return app.exec_()
