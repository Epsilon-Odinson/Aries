"""PyQt5 desktop UI for Aries (Module 2, JARVIS redesign).

JARVIS-style HUD in Aries' deep-purple palette (background #0d0010,
accent #7b2fff), restyled 0.3.1 with the makoma.io design language
(restraint, centred layout, huge whitespace, bold geometric sans
paired with an italic serif for the emotional word, tiny uppercase
letterspaced labels, pill controls, calm motion), refined 0.3.2:
the face is a MACHINE, not a human — a single angular glowing
visor, no smile, no eyebrows, no human mouth; expression comes
from light and motion (visor width/brightness, waveform bars when
speaking). The face is the hero: big, centred, first thing you
see. The window launches fullscreen like VLC (F11 toggles,
Esc exits). Mini mode opens by pressing the face — there is no
Pause-background button and no Mini-mode button. Text is cut to
the minimum: one-line greeting, one tiny status line, chat.

Fonts (0.3.2): Segoe UI everywhere ("Segoe UI Semibold" for
headings), Georgia italic for the serif greeting word only —
no default Qt font, no monospace for main text.

The face is a living machine, not a static picture: it breathes,
its visor dims in a blink-like pulse, it looks around on its own,
and follows the mouse cursor with its gaze when the cursor is
near. Mini mode (0.2.2) docks itself bottom-right, drags
smoothly, and stays always-on-top and active.

Light on purpose: background and face animations are capped at
~15-16 fps, and everything is drawn with QPainter (no image
assets), so this stays comfortable on a ThinkPad T14 (11th Gen
i5, 16 GB RAM). While the mini window is being dragged the face
drops to a cheap paint path, so dragging does not stutter.

Kept OUT of Module 2 on purpose: voice, face-recognition login,
permissions, DuckDuckGo search, tool calling / autonomy.
Those are later modules.
"""

from __future__ import annotations

import math
import random

from PyQt5.QtCore import QEvent, QObject, Qt, QThread, QTimer, pyqtSignal
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
from .main import SYSTEM_PROMPT

# --- Theme -----------------------------------------------------------------
BG = "#0d0010"
PANEL = "#1a0026"
PANEL_SEMI = "rgba(26, 0, 38, 215)"
ACCENT = "#7b2fff"
ACCENT_SOFT = "#a86bff"
TEXT = "#e8e0ff"
CREAM = "#f5f0e0"  # makoma-style cream for the italic serif word
MUTED = "#9a8fb5"
CYAN = "#35e0ff"  # JARVIS accent for the core only; purple stays dominant
GOLD = "#d9a441"  # makoma-style gold, rationed to ONE tiny dot only
ERROR_RED = "#ff6b81"
# Fonts (0.3.2): clean modern look, real Windows fonts.
# Segoe UI everywhere; Segoe UI Semibold for headings; Georgia
# italic for the serif greeting word ONLY. No default Qt font,
# no monospace for main text — monospace is not used at all now.
SANS = "Segoe UI"
SANS_SEMI = "Segoe UI Semibold"
SERIF = "Georgia"


def _alpha(color: str, alpha: int) -> QColor:
    c = QColor(color)
    c.setAlpha(max(0, min(255, alpha)))
    return c


# --- HUD background ----------------------------------------------------------
class HudBackgroundWidget(QWidget):
    """Dark JARVIS HUD background. No falling characters.

    Faint grid, slow rotating glowing rings centred behind the
    face, drifting particles, soft radial glow. Capped at ~15 fps.
    The animation just runs — there is no pause control (0.3.2
    removed the Pause-background button entirely).
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
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

    def _tick(self) -> None:
        # Makoma calm: slow drift, never snappy. ~0.35 deg/tick at 15 fps.
        self._angle = (self._angle + 0.35) % 360
        for p in self._particles:
            p["x"] = (p["x"] + p["dx"] * 0.6) % 1.0
            p["y"] = (p["y"] + p["dy"] * 0.6) % 1.0
        self.update()

    def _core_center(self):
        # The face is the hero, centred near the top of the screen.
        return self.width() * 0.50, self.height() * 0.26

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

        # Faint grid — even fainter now (makoma restraint).
        painter.setPen(QPen(_alpha(ACCENT_SOFT, 8), 1))
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

        # Slow rotating glowing rings around the core, softer now.
        for radius, span, color, width, speed in (
            (190, 110, ACCENT, 2, 1.0),
            (240, 70, CYAN, 1, -0.7),
            (290, 140, ACCENT_SOFT, 1, 0.45),
        ):
            painter.setPen(QPen(_alpha(color, 30), width))
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


# --- Aries machine face -----------------------------------------------------
class AriesFaceWidget(QWidget):
    """A MACHINE face — clearly a machine, not a human.

    Angular machine head with glowing purple edges and a single
    angular glowing VISOR instead of human eyes: a sharp hexagonal
    visor bar holding two slanted eye-slits whose bright cores
    shift with the gaze. No smile, no lips, no human mouth, no
    eyebrows — expression comes from light and motion only:
    - idle: visor at rest width, calm glow
    - thinking / listening: visor narrows, eyes shift up, ripple
    - speaking: visor pulses wider/brighter, waveform bars under
      it move with the reply
    - error: visor dims, LED goes red

    It stays a living machine, like the 0.2.2 core:
    - Breathing: the head gently swells and bobs, always, even idle.
    - Look-around: every few seconds the gaze drifts to a random
      point and back, like a head turning to look at something.
    - Cursor gaze: when the mouse is over/near this window the
      visor looks toward the cursor, smoothly (never a snap).
      Hovering brightens the face, like it noticed you.
    - Blink: every few seconds the visor dims and re-brightens
      in a quick pulse (a visor dim, not an eyelid).

    Clicking the face emits :attr:`clicked` — in the main window
    that opens mini mode (0.3.2: no Mini-mode button any more).

    All animation is capped at ~16 fps. While ``_dragging`` is set
    (mini window being dragged) the paint path is cheap — no halo
    arcs, fewer glow layers — so dragging stays smooth.
    Callers can also drive the gaze directly with
    :meth:`set_cursor_gaze` (values in -1..1).
    """

    STATES = {"idle", "listening", "thinking", "speaking", "error"}

    clicked = pyqtSignal()

    def __init__(self, diameter: int = 320, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(diameter, diameter)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self._diameter = diameter
        self._state = "idle"
        self._angle = 0.0
        self._pulse = 0.0
        self._ripple = 0.0
        self._bars = [0.4] * 5
        self._mouth_open = 0.0
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
        speed = {"idle": 1.2, "listening": 5.0, "thinking": 5.0,
                 "speaking": 2.5, "error": 0.5}
        self._angle = (self._angle + speed[self._state]) % 360
        self._pulse += 0.12
        if self._state in {"listening", "thinking"}:
            self._ripple = (self._ripple + 0.03) % 1.0
        if self._state == "speaking":
            # Speech energy: the visor pulses and the bars move.
            self._mouth_open = 0.35 + 0.65 * abs(
                math.sin(self._pulse * 1.9))
            self._bars = [
                0.3 + 0.7 * abs(math.sin(self._pulse * 2.1 + k * 1.3))
                for k in range(5)
            ]
        else:
            self._mouth_open = max(0.0, self._mouth_open - 0.2)
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
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        D = float(self._diameter)
        # Breathing: the head gently swells and bobs, always.
        breathe = math.sin(self._pulse)
        bob = 2.0 * breathe + (1.5 * math.sin(self._pulse * 1.7)
                               if self._state == "speaking" else 0.0)
        gx, gy = self._gaze
        # The whole head shifts a few px with the gaze (head-turn
        # feel) and bobs with the breath. Blink dims the face.
        cx = D / 2.0 + gx * D * 0.04
        cy = D / 2.0 + gy * D * 0.03 + bob
        scale = 1.0 + 0.02 * breathe
        bright = self._state in {"listening", "thinking", "speaking"} or self._hover
        dim = 1.0 - 0.55 * self._blink
        if self._state == "error":
            dim *= 0.6

        # --- Head geometry (all relative to D) ---
        # Angular machine head: small corner radius, not a soft blob.
        hw = D * 0.82 * scale  # head width
        hh = D * 0.76 * scale  # head height
        hx = cx - hw / 2.0
        hy = cy - hh / 2.0
        # Visor geometry: one angular bar across the head.
        visor_w = D * 0.56
        if self._state == "speaking":
            visor_w *= 1.0 + 0.08 * self._mouth_open
        visor_h = D * 0.16
        if self._state in {"listening", "thinking"}:
            visor_h *= 0.72  # narrowed visor, concentrating
        visor_h *= max(0.15, 1.0 - self._blink)  # blink dims the visor
        visor_y = hy + hh * 0.30
        eye_dx = D * 0.135  # half-distance between the two eye-slits
        eye_w = D * 0.13
        eye_h = visor_h * 0.62
        bar_y = hy + hh * 0.68  # waveform bars live here (no mouth)

        # --- Halo ring behind the head (subtle HUD accent) ---
        if not self._dragging:
            painter.setPen(QPen(_alpha(ACCENT_SOFT, 45), 1))
            painter.setBrush(Qt.NoBrush)
            for radius, span, color, width, direction in (
                (D * 0.47, 90, CYAN, 2, 1.0),
                (D * 0.50, 60, ACCENT, 1, -0.8),
            ):
                start = int(((self._angle * direction) % 360) * 16)
                painter.drawArc(
                    int(cx - radius), int(cy - radius),
                    int(radius * 2), int(radius * 2),
                    start, int(span * 16),
                )
            # Expanding ripple while thinking / listening.
            if self._state in {"listening", "thinking"}:
                rr = D * 0.30 * (0.45 + 0.55 * self._ripple)
                painter.setPen(QPen(_alpha(CYAN, int(60 * (1 - self._ripple))), 2))
                painter.drawEllipse(int(cx - rr), int(cy - rr),
                                    int(rr * 2), int(rr * 2))

        # --- Glow behind the head ---
        glow_layers = 1 if self._dragging else 3
        for i in range(glow_layers, 0, -1):
            painter.setPen(Qt.NoPen)
            painter.setBrush(_alpha(ACCENT, int((10 if not bright else 14) * dim)))
            gr = hw * (0.55 + 0.08 * i)
            painter.drawRoundedRect(
                int(cx - gr), int(cy - gr * 0.9),
                int(gr * 2), int(gr * 1.8),
                40, 40,
            )

        # --- Antenna (with glowing tip) ---
        painter.setPen(QPen(_alpha(ACCENT_SOFT, int(200 * dim)), 3))
        painter.drawLine(int(cx), int(hy), int(cx), int(hy - D * 0.06))
        painter.setPen(Qt.NoPen)
        painter.setBrush(_alpha(CYAN, int(255 * dim)))
        tip = max(3.0, D * 0.012)
        painter.drawEllipse(int(cx - tip), int(hy - D * 0.06 - tip),
                            int(tip * 2), int(tip * 2))

        # --- Ears (side plates) — machine, not human ---
        painter.setPen(QPen(_alpha(ACCENT, int(180 * dim)), 2))
        painter.setBrush(_alpha(PANEL, int(220 * dim)))
        ear_w, ear_h = D * 0.045, D * 0.16
        ear_y = visor_y + visor_h / 2.0
        painter.drawRoundedRect(int(hx - ear_w), int(ear_y - ear_h / 2),
                                int(ear_w), int(ear_h), 6, 6)
        painter.drawRoundedRect(int(hx + hw), int(ear_y - ear_h / 2),
                                int(ear_w), int(ear_h), 6, 6)

        # --- Head outline + face plate (angular, machine) ---
        painter.setPen(QPen(_alpha(ACCENT, int(230 * dim)), 3))
        painter.setBrush(_alpha("#1a0026", int(235 * dim)))
        painter.drawRoundedRect(int(hx), int(hy), int(hw), int(hh),
                                int(D * 0.05), int(D * 0.05))
        painter.setPen(QPen(_alpha(ACCENT_SOFT, int(60 * dim)), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(int(hx + 10), int(hy + 10),
                                int(hw - 20), int(hh - 20),
                                int(D * 0.04), int(D * 0.04))
        # Top vent slots — machine detail, not a face feature.
        painter.setPen(QPen(_alpha(ACCENT_SOFT, int(120 * dim)), 2))
        for k in range(3):
            vx = cx + (k - 1) * D * 0.09
            painter.drawLine(int(vx - D * 0.03), int(hy + D * 0.05),
                             int(vx + D * 0.03), int(hy + D * 0.05))

        # --- Visor: one angular bar, two sharp eye-slits, no human eyes ---
        # Hexagonal visor bar across the head.
        slant = visor_h * 0.5
        vx0, vx1 = cx - visor_w / 2.0, cx + visor_w / 2.0
        vy0, vy1 = visor_y, visor_y + visor_h
        from PyQt5.QtGui import QPolygonF
        from PyQt5.QtCore import QPointF
        visor_poly = QPolygonF([
            QPointF(vx0 + slant, vy0), QPointF(vx1 - slant, vy0),
            QPointF(vx1, vy0 + visor_h / 2), QPointF(vx1 - slant, vy1),
            QPointF(vx0 + slant, vy1), QPointF(vx0, vy0 + visor_h / 2),
        ])
        visor_edge = ERROR_RED if self._state == "error" else ACCENT_SOFT
        painter.setPen(QPen(_alpha(visor_edge, int(220 * dim)), 2))
        painter.setBrush(_alpha("#0a0014", int(240 * dim)))
        painter.drawPolygon(visor_poly)
        # Glow behind the visor.
        painter.setPen(Qt.NoPen)
        painter.setBrush(_alpha(CYAN, int(35 * dim)))
        painter.drawRoundedRect(int(vx0), int(vy0 - 3),
                                int(visor_w), int(visor_h + 6), 6, 6)
        # Eye-slits: sharp slanted parallelograms, bright cores that
        # shift with the gaze. No pupils, no human eyes.
        for sign in (-1, 1):
            ex = cx + sign * eye_dx
            slit_slant = eye_h * 0.35
            ey0 = visor_y + (visor_h - eye_h) / 2.0
            ey1 = ey0 + eye_h
            slit = QPolygonF([
                QPointF(ex - eye_w / 2 + slit_slant, ey0),
                QPointF(ex + eye_w / 2, ey0),
                QPointF(ex + eye_w / 2 - slit_slant, ey1),
                QPointF(ex - eye_w / 2, ey1),
            ])
            painter.setPen(Qt.NoPen)
            painter.setBrush(_alpha(
                CYAN, int((235 if bright else 190) * dim)))
            painter.drawPolygon(slit)
            # Bright core — looks where the gaze points.
            core_x = ex + gx * eye_w * 0.18
            core_y = ey0 + eye_h / 2 + gy * eye_h * 0.22
            if self._state in {"listening", "thinking"}:
                core_y -= D * 0.006  # visor looks up while thinking
            cr = max(2.5, eye_w * 0.16)
            painter.setBrush(_alpha("#ffffff", int(240 * dim)))
            painter.drawEllipse(int(core_x - cr), int(core_y - cr),
                                int(cr * 2), int(cr * 2))

        # --- Speech bars (the "voice", not a mouth) ---
        # Thin equaliser bars under the visor. They move while
        # speaking, sit still and dim when idle, and vanish on error.
        if self._state != "error":
            painter.setPen(QPen(_alpha(CYAN, int(200 * dim)), 2))
            for k, level in enumerate(self._bars):
                bx = cx + (k - 2) * D * 0.045
                bh = D * 0.012 + level * D * 0.035
                if self._state != "speaking":
                    bh = D * 0.012  # quiet, flat bars when not speaking
                painter.drawLine(int(bx), int(bar_y),
                                 int(bx), int(bar_y + bh))

        # --- Status LED (bottom corner of the head) ---
        led_color = ERROR_RED if self._state == "error" else (
            CYAN if self._state in {"listening", "thinking", "speaking"}
            else ACCENT_SOFT)
        painter.setPen(Qt.NoPen)
        painter.setBrush(_alpha(led_color, int(230 * dim)))
        lr = max(3.0, D * 0.012)
        painter.drawEllipse(int(hx + hw - D * 0.055 - lr),
                            int(hy + hh - D * 0.055 - lr),
                            int(lr * 2), int(lr * 2))


# Backwards-compatible alias: the old name was HudCoreWidget.
HudCoreWidget = AriesFaceWidget


# --- HUD panel frame -----------------------------------------------------------
class HudFrame(QWidget):
    """Panel, makoma-style: soft corners, one thin hairline border,
    no heavy shadows, no corner brackets, no chrome."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background: {PANEL_SEMI};")

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        painter.setPen(QPen(_alpha(ACCENT_SOFT, 45), 1))
        painter.setBrush(QColor(PANEL_SEMI))
        painter.drawRoundedRect(rect, 18, 18)


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
        self.setFixedSize(300, 360)
        self.setStyleSheet(f"background: {BG}; color: {TEXT};")
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_AlwaysShowToolTips, True)
        self._drag_offset = None
        self._dragging = False
        self._user_moved = False
        self._docked = False
        # Makoma in mini too: centred, breathing room, pill button.
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        self._core = AriesFaceWidget(180, self)
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


# --- Main window ---------------------------------------------------------------
class AriesWindow(QMainWindow):
    """Main Aries window (0.3.2): the face is the hero.

    Fullscreen like VLC by default (F11 toggles, Esc exits).
    One vertical column, sleek and minimal: a tiny fullscreen
    button top-right, the big machine face centred and first,
    a one-line greeting, one tiny status line, then chat.
    No Pause-background button, no Mini-mode button — pressing
    the face opens mini mode. No TIME / MODEL / KEY readout
    block, no big text sections."""

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
        self._build_ui()
        self._check_key()

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

        # Tiny fullscreen toggle, top-right, out of the way.
        top = QHBoxLayout()
        top.addStretch(1)
        self._fs_btn = QPushButton("⛶  Fullscreen  (F11)", self)
        self._fs_btn.setObjectName("fullscreen_btn")
        self._fs_btn.clicked.connect(self._toggle_fullscreen)
        self._fs_btn.setStyleSheet(
            f"background: transparent; color: {MUTED}; "
            f"padding: 6px 14px; font-family: {SANS}; font-size: 12px;"
            f"border: 1px solid {ACCENT_SOFT}; border-radius: 16px;"
        )
        top.addWidget(self._fs_btn)
        root.addLayout(top)

        # ---- The face: big, centred, FIRST thing you see -----------------
        self._core = AriesFaceWidget(360, self)
        self._core.setToolTip("Click the face for mini mode")
        self._core.clicked.connect(self._open_mini)
        root.addWidget(self._core, alignment=Qt.AlignCenter)

        # ---- One-line greeting, makoma pairing, one short line -----------
        greet = QLabel(
            f'<span style="font-family: {SANS_SEMI}; font-size: 24px; '
            f'font-weight: bold; color: {TEXT};">Aries active.</span> '
            f'<span style="font-family: {SERIF}; font-size: 24px; '
            f'font-style: italic; color: {CREAM};">Ready to cook.</span>'
        )
        greet.setAlignment(Qt.AlignCenter)
        greet.setWordWrap(True)
        root.addWidget(greet)

        # ---- One tiny status line — the only readout ----------------------
        self._status = QLabel("READY", self)
        self._status.setAlignment(Qt.AlignCenter)
        self._status.setStyleSheet(
            f"color: {MUTED}; font-size: 11px; font-family: {SANS};"
            "letter-spacing: 3px;"
        )
        root.addWidget(self._status)

        # ---- Chat: clean panel, no header, no extra text -----------------
        chat_frame = HudFrame(overlay)
        chat_layout = QVBoxLayout(chat_frame)
        chat_layout.setContentsMargins(24, 20, 24, 20)
        chat_layout.setSpacing(14)

        self._chat = QTextEdit(self)
        self._chat.setObjectName("chat")
        self._chat.setReadOnly(True)
        self._chat.setStyleSheet(
            f"background: transparent; color: {TEXT}; "
            f"font-family: {SANS}; font-size: 15px;"
            "border: none; padding: 8px; line-height: 1.6;"
        )
        chat_layout.addWidget(self._chat, stretch=1)

        bottom = QHBoxLayout()
        bottom.setSpacing(10)
        self._input = QLineEdit(self)
        self._input.setObjectName("input")
        self._input.setPlaceholderText("Type a message…")
        self._input.setMinimumHeight(48)
        self._input.setStyleSheet(
            f"background: {PANEL_SEMI}; color: {TEXT}; "
            f"font-family: {SANS}; font-size: 14px; padding: 8px 18px;"
            f"border: 1px solid {ACCENT_SOFT}; border-radius: 24px;"
        )
        self._input.returnPressed.connect(self._on_send)
        bottom.addWidget(self._input, stretch=1)
        self._send_btn = QPushButton("Send", self)
        self._send_btn.setObjectName("send_btn")
        self._send_btn.setMinimumHeight(48)
        self._send_btn.setStyleSheet(
            f"background: {ACCENT}; color: white; padding: 8px 24px;"
            f"border: none; border-radius: 24px; font-weight: bold;"
            f"font-family: {SANS_SEMI}; font-size: 14px;"
        )
        self._send_btn.clicked.connect(self._on_send)
        bottom.addWidget(self._send_btn)

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
        chat_layout.addLayout(bottom)
        root.addWidget(chat_frame, stretch=1)

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
        if hasattr(self, "_fs_btn"):
            self._fs_btn.setText(
                "⛶  Exit fullscreen  (Esc)" if self.isFullScreen()
                else "⛶  Fullscreen  (F11)"
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

    # -- chat ------------------------------------------------------------------
    def _append(self, who: str, text: str) -> None:
        color = ACCENT_SOFT if who == "Aries" else TEXT
        self._chat.append(
            f'<p><b><span style="color:{color}">{who}:</span></b> '
            f"{text}</p>"
        )

    def _set_status(self, text: str) -> None:
        # Tiny letterspaced label, with ONE rationed gold dot.
        clean = text.replace("…", "").strip().upper()
        self._status.setTextFormat(Qt.RichText)
        self._status.setText(
            f'<span style="color:{GOLD}">●</span>  {clean}'
        )

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
