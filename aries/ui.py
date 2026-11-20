"""PyQt5 desktop UI for Aries (0.5.0).

The desktop is a client of the SAME Render backend the web app uses
(https://aries-ahvo.onrender.com), so it has ALL the web app's
abilities — not just chat + PC control. Chat (with web search and
live info), ElevenLabs talk-back, the memory vault, tasks,
permissions, face login, screenshots, connections and the full
Settings (gear) tabs all come from the backend. The direct NVIDIA
key (aries/config.py + aries/llm.py) is kept only as a fallback for
when the backend cannot be reached at all.

The look is the web front, exactly: the SAME baked-grain background
and artwork (the real images from production/web/src/veilImages.js,
decoded at runtime — grain as the cover-fit base, the artwork as the
full-colour reveal behind a radial mask with a purple rim, idle
lissajous drift, and a 750 ms click burst), the exact visor face,
the stacked greeting lockup, and a minimal main view (chat + mic
only). Deep purple #0d0010 underneath, so any uncovered area still
matches.

Voice chat is a first-class citizen: the mic dictates (SpeechRecognition
+ Google), replies are spoken back in sentence chunks with prefetch
(ElevenLabs turbo via /api/tts), and "Hey Aries" wakes it. Mini mode
is just the face, floating — a frameless, transparent, borderless
window with a clear gap above the taskbar, voice-activated only.

PC control (desktop only): the operating agent — open apps/files,
type, click, clipboard, shell, read/write files, system info — max 5
steps, confirmation before acting, twice for destructive actions,
master toggle in Settings, OFF by default. Replies are sanitised so
the chat never shows raw JSON: the pc-actions block is parsed
tolerantly, executed, and replaced by a plain-words result (0.4.1).
PC requests are routed to the local NVIDIA LLM (the backend does not
do PC actions); everything else goes to the backend.

Sources: when a reply carries sources (web search / live info), the
full answer text comes first, then a "Sources (n)" button under it.
Tapping the button expands/collapses the source links. Sources are
never rendered as plain listed links in the reply body.

Fonts: Segoe UI everywhere ("Segoe UI Semibold" for headings),
Georgia italic for the serif greeting words only.
"""

from __future__ import annotations

import base64
import math
import random
import re
import webbrowser

from PyQt5.QtCore import (
    QEvent,
    QObject,
    QSettings,
    Qt,
    QThread,
    QTimer,
    QUrl,
    pyqtSignal,
    pyqtSlot,
)
from PyQt5.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QImage,
    QPainter,
    QPen,
    QPixmap,
)
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from . import pc_control, veil_images
from .api_client import (
    BackendClient,
    BackendError,
    BackendUnreachable,
    VoiceNotConfigured,
    looks_current,
)
from .config import delete_api_key, get_api_key, key_source, set_api_key
from .llm import AriesLLMError, chat as direct_chat
from .main import SYSTEM_PROMPT
from .voice import VoiceEngine, VoiceError, split_sentences

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
SANS = "Segoe UI"
SANS_SEMI = "Segoe UI Semibold"
SERIF = "Georgia"

# The Render backend the web app uses. The desktop is its client.
BACKEND_URL = "https://aries-ahvo.onrender.com"


def _alpha(color: str, alpha: int) -> QColor:
    c = QColor(color)
    c.setAlpha(max(0, min(255, alpha)))
    return c


def _cover_pixmap(image: QImage, width: int, height: int) -> QPixmap:
    """Scale an image to cover a widget (like CSS object-fit: cover):
    scale so it covers, then crop the centred overflow."""
    if image.isNull() or width <= 0 or height <= 0:
        return QPixmap()
    scaled = image.scaled(
        width, height, Qt.KeepAspectRatioByExpanding,
        Qt.SmoothTransformation)
    x = max(0, (scaled.width() - width) // 2)
    y = max(0, (scaled.height() - height) // 2)
    return QPixmap.fromImage(
        scaled.copy(x, y, width, height))


# --- Real veil background (the web front's baked images) --------------------
class VeilWidget(QWidget):
    """The web front's veil, for real — the same baked images.

    The grain (fine or coarse) is the cover-fit base layer, exactly
    like the web's <img id="veilBase">. The artwork (the mountains)
    is the full-colour reveal: it shows through a radial mask that
    follows the pointer (or drifts on its own in a slow lissajous,
    like the web), with a soft purple rim (#b78bff) where the veil
    lifts. Clicking the veil fires a 750 ms burst — an expanding
    ring of full colour. A vignette darkens the edges.

    The images come from aries/veil_images.py (base64 text copied
    from production/web/src/veilImages.js), decoded once at startup.
    Deep purple #0d0010 is painted underneath, so if an image ever
    fails to decode the background still matches. The face, greeting,
    chat and composer sit on top of this widget.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._grain_name = "fine"
        self._grain_img: QImage | None = None
        self._artwork_img: QImage | None = None
        self._grain_pix: QPixmap | None = None
        self._artwork_pix: QPixmap | None = None
        self._images_ok = False
        self._load_images()
        # Pointer / drift state (web Veil.jsx, same numbers).
        self._pointer = {"x": 0.0, "y": 0.0, "tx": 0.0, "ty": 0.0,
                         "seen": False, "inside": False}
        self._bursts: list[dict] = []  # {x, y, start_ms}
        self._start_ms = 0
        self._elapsed = 0.0  # seconds since start
        self._reveal_buf: QImage | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(50)  # ~20 fps: smooth drift, cheap paint
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _load_images(self) -> None:
        """Decode the three baked JPEGs once. False when any fails."""
        try:
            fine = QImage.fromData(
                base64.b64decode(veil_images.GRAIN_FINE), "JPG")
            coarse = QImage.fromData(
                base64.b64decode(veil_images.GRAIN_COARSE), "JPG")
            artwork = QImage.fromData(
                base64.b64decode(veil_images.ARTWORK), "JPG")
            if fine.isNull() or artwork.isNull() or coarse.isNull():
                self._images_ok = False
                return
            self._fine_img = fine
            self._coarse_img = coarse
            self._grain_img = fine if self._grain_name == "fine" else coarse
            self._artwork_img = artwork
            self._images_ok = True
            self._rebuild_layers()
        except Exception:
            self._images_ok = False

    def set_grain(self, name: str) -> None:
        """Switch the base grain (fine / coarse), like the web toggle."""
        if name not in ("fine", "coarse"):
            return
        if name == self._grain_name:
            return
        self._grain_name = name
        if not self._images_ok:
            return
        self._grain_img = (self._fine_img if name == "fine"
                           else self._coarse_img)
        self._rebuild_layers()
        self.update()

    def _rebuild_layers(self) -> None:
        """Rebuild the cover-fit pixmaps for the current widget size."""
        w, h = max(self.width(), 1), max(self.height(), 1)
        if self._grain_img is not None:
            self._grain_pix = _cover_pixmap(self._grain_img, w, h)
        if self._artwork_img is not None:
            self._artwork_pix = _cover_pixmap(self._artwork_img, w, h)
        self._reveal_buf = QImage(w, h, QImage.Format_ARGB32_Premultiplied)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        if self._images_ok:
            self._rebuild_layers()

    def _reveal_radius(self) -> float:
        """Web: max(120, min(210, innerWidth * 0.32))."""
        return max(120.0, min(210.0, self.width() * 0.32))

    def set_pointer(self, x: float, y: float, inside: bool = True) -> None:
        """The pointer moved (main window forwards mouse moves)."""
        self._pointer["tx"] = x
        self._pointer["ty"] = y
        self._pointer["seen"] = True
        self._pointer["inside"] = inside

    def pointer_left(self) -> None:
        self._pointer["inside"] = False

    def burst_at(self, x: float, y: float) -> None:
        """A click burst at (x, y) — an expanding ring of full colour,
        750 ms, like the web's click burst."""
        import time
        self._bursts.append({"x": x, "y": y,
                             "start": time.monotonic()})

    def burst_from_centre(self) -> None:
        """A burst from the centre (the Settings 'Release a burst'
        button, like the web's burstFromCentre)."""
        self.burst_at(self.width() * 0.5, self.height() * 0.44)

    def _tick(self) -> None:
        """Advance the drift clock and repaint. ~20 fps."""
        self._elapsed += 0.05
        self.update()

    def _draw_masked_reveal(self, painter: QPainter, cx: float, cy: float,
                            radius: float, alpha: float) -> None:
        """Draw the artwork through a radial mask at (cx, cy).

        The mask is fully opaque within 55% of the radius and fades
        to transparent at the radius (web Veil.jsx), with a soft
        purple rim at 97% of the radius. Uses an offscreen buffer
        with CompositionMode_DestinationIn."""
        if radius <= 0 or alpha <= 0 or self._artwork_pix is None:
            return
        from PyQt5.QtGui import QRadialGradient

        w, h = self.width(), self.height()
        buf = self._reveal_buf
        if buf is None or buf.size() != self.size():
            buf = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
            self._reveal_buf = buf
        buf.fill(Qt.transparent)
        bp = QPainter(buf)
        bp.setRenderHint(QPainter.Antialiasing)
        bp.drawPixmap(0, 0, self._artwork_pix)
        # Radial mask: opaque to 55% of the radius, fading to 0 at r.
        grad = QRadialGradient(cx, cy, radius)
        grad.setColorAt(0.0, QColor(0, 0, 0, 255))
        grad.setColorAt(0.55, QColor(0, 0, 0, 255))
        grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        bp.setCompositionMode(QPainter.CompositionMode_DestinationIn)
        bp.setPen(Qt.NoPen)
        bp.setBrush(grad)
        bp.drawRect(0, 0, w, h)
        bp.end()
        painter.save()
        painter.setOpacity(alpha)
        painter.drawImage(0, 0, buf)
        painter.restore()
        # Soft lit rim where the veil lifts.
        painter.save()
        painter.setOpacity(0.35 * alpha)
        painter.setPen(QPen(QColor(ACCENT_SOFT), 1.5))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(int(cx - radius * 0.97), int(cy - radius * 0.97),
                            int(radius * 1.94), int(radius * 1.94))
        painter.restore()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        from PyQt5.QtGui import QRadialGradient

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = max(self.width(), 1), max(self.height(), 1)
        painter.fillRect(self.rect(), QColor(BG))
        if self._images_ok and self._grain_pix is not None:
            painter.drawPixmap(0, 0, self._grain_pix)
        else:
            # Fallback (images failed): flat deep purple + a soft glow.
            glow = QRadialGradient(w * 0.5, h * 0.26, max(w, h) * 0.42)
            glow.setColorAt(0.0, _alpha(ACCENT, 26))
            glow.setColorAt(0.55, _alpha(ACCENT, 10))
            glow.setColorAt(1.0, _alpha(ACCENT, 0))
            painter.setPen(Qt.NoPen)
            painter.setBrush(glow)
            painter.drawRect(self.rect())

        # Idle drift (web lissajous) when the pointer has not been seen,
        # then ease the reveal toward its target.
        p = self._pointer
        t = self._elapsed
        if not p["seen"]:
            p["tx"] = w * (0.5 + 0.26 * math.sin(t * 0.32))
            p["ty"] = h * (0.46 + 0.2 * math.sin(t * 0.47 + 1.3))
        p["x"] += (p["tx"] - p["x"]) * 0.12
        p["y"] += (p["ty"] - p["y"]) * 0.12

        if self._images_ok:
            if p["inside"] or not p["seen"]:
                self._draw_masked_reveal(
                    painter, p["x"], p["y"], self._reveal_radius(), 1.0)
            # Click bursts: expanding rings of full colour, 750 ms.
            import time
            now = time.monotonic()
            alive = []
            for b in self._bursts:
                prog = (now - b["start"]) / 0.75
                if prog < 1:
                    alive.append(b)
                    ease = 1 - (1 - prog) ** 3
                    br = 60 + ease * max(w, h) * 0.55
                    self._draw_masked_reveal(
                        painter, b["x"], b["y"], br, (1 - prog) * 0.95)
            self._bursts = alive

        # Vignette: darker toward the edges, like the web front's.
        vignette = QRadialGradient(w * 0.5, h * 0.26, max(w, h) * 0.75)
        vignette.setColorAt(0.0, _alpha(BG_DEEP, 0))
        vignette.setColorAt(0.62, _alpha(BG_DEEP, 40))
        vignette.setColorAt(1.0, _alpha(BG_DEEP, 140))
        painter.setPen(Qt.NoPen)
        painter.setBrush(vignette)
        painter.drawRect(self.rect())


# Backwards-compatible alias.
HudBackgroundWidget = VeilWidget
VeilBackgroundWidget = VeilWidget



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


# --- Chat log: message widgets + the Sources button --------------------------
class SourcesPanel(QWidget):
    """The expandable list of source links under a reply.

    Hidden until the "Sources (n)" button is tapped. Each source is a
    real link (clicking opens it in the system browser). The panel is
    never rendered as plain listed links inside the reply text — the
    full answer text comes first, then the button, then this panel.
    """

    def __init__(self, sources: list[dict], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setVisible(False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(4)
        for s in sources:
            title = (s.get("title") or s.get("url") or "").strip()
            url = (s.get("url") or "").strip()
            publisher = (s.get("publisher") or "").strip()
            label = QLabel(self)
            label.setWordWrap(True)
            label.setOpenExternalLinks(True)
            label.setTextInteractionFlags(Qt.TextBrowserInteraction)
            shown = title or url
            if publisher:
                shown += f" — {publisher}"
            if url:
                label.setText(
                    f'<a href="{url}" style="color:{ACCENT_SOFT}">{shown}</a>')
            else:
                label.setText(shown)
            label.setStyleSheet(
                f"color: {MUTED}; font-family: {SANS}; font-size: 12px;")
            layout.addWidget(label)


class AriesMessageWidget(QWidget):
    """One Aries reply in the chat log: the full answer text first,
    then — only when the reply carries sources — a "Sources (n)"
    button under it. Tapping the button expands/collapses the source
    links. The button matches the web-front styling: deep purple,
    cream text, subtle border, pill shape."""

    def __init__(self, text: str, sources: list[dict] | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._sources = list(sources or [])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(6)
        body = QLabel(self)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body.setText(
            f'<span style="color:{ACCENT_SOFT}"><b>Aries:</b></span> '
            f'<span style="color:{TEXT}">{text}</span>')
        body.setStyleSheet(
            f"font-family: {SANS}; font-size: 15px; line-height: 1.6;")
        layout.addWidget(body)
        self._panel = None
        self._btn = None
        if self._sources:
            self._btn = QPushButton(
                f"Sources ({len(self._sources)})", self)
            self._btn.setCheckable(True)
            self._btn.setChecked(False)
            self._btn.setStyleSheet(
                f"background: {PANEL_SEMI}; color: {TEXT}; "
                f"border: 1px solid {HAIRLINE}; border-radius: 14px; "
                f"padding: 5px 14px; font-family: {SANS_SEMI}; "
                f"font-size: 12px;")
            self._btn.setCursor(Qt.PointingHandCursor)
            self._btn.toggled.connect(self._on_toggle)
            layout.addWidget(self._btn, alignment=Qt.AlignLeft)
            self._panel = SourcesPanel(self._sources, self)
            layout.addWidget(self._panel)

    def _on_toggle(self, checked: bool) -> None:
        if self._panel is not None:
            self._panel.setVisible(checked)
            self._btn.setText(
                f"Sources ({len(self._sources)})"
                + ("  ▲" if checked else "  ▼"))


class ChatLog(QWidget):
    """The chat as a scrollable list of message widgets (not one big
    text box), so each reply can carry its own Sources button."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self._layout.setSpacing(2)
        self._layout.addStretch(1)
        self._scroll = None  # set by the owner (the QScrollArea)

    def set_scroll(self, scroll: QScrollArea) -> None:
        self._scroll = scroll

    def _scroll_to_bottom(self) -> None:
        if self._scroll is not None:
            bar = self._scroll.verticalScrollBar()
            bar.setValue(bar.maximum())

    def add_user(self, text: str) -> None:
        label = QLabel(self)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        label.setText(
            f'<span style="color:{TEXT}"><b>You:</b></span> '
            f'<span style="color:{TEXT}">{text}</span>')
        label.setStyleSheet(
            f"font-family: {SANS}; font-size: 15px; line-height: 1.6;")
        self._layout.insertWidget(self._layout.count() - 1, label)
        self._scroll_to_bottom()

    def add_aries(self, text: str, sources: list[dict] | None = None) -> None:
        widget = AriesMessageWidget(text, sources, self)
        self._layout.insertWidget(self._layout.count() - 1, widget)
        self._scroll_to_bottom()

    def add_note(self, text: str) -> None:
        label = QLabel(self)
        label.setWordWrap(True)
        label.setText(
            f'<span style="color:{MUTED}; font-size: 13px;">{text}</span>')
        label.setStyleSheet(f"font-family: {SANS};")
        self._layout.insertWidget(self._layout.count() - 1, label)
        self._scroll_to_bottom()

    def clear(self) -> None:
        while self._layout.count() > 1:
            item = self._layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()


# --- Background workers (never touch widgets) --------------------------------
class BackendChatWorker(QObject):
    """POST /api/chat off the UI thread. Emits the whole response dict."""

    finished = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, client: BackendClient, message: str,
                 use_search: bool, consent: dict) -> None:
        super().__init__()
        self._client = client
        self._message = message
        self._use_search = use_search
        self._consent = consent

    def run(self) -> None:
        try:
            result = self._client.chat(
                self._message, self._use_search, self._consent)
        except BackendError as exc:
            self.failed.emit(str(exc))
        else:
            self.finished.emit(result)


class DirectChatWorker(QObject):
    """Direct NVIDIA llm.chat() off the UI thread (fallback path)."""

    finished = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, history: list[dict[str, str]]) -> None:
        super().__init__()
        self._history = list(history)

    def run(self) -> None:
        try:
            reply = direct_chat(self._history)
        except (AriesLLMError, RuntimeError) as exc:
            self.failed.emit(str(exc))
        else:
            self.finished.emit(reply)


class StateWorker(QObject):
    """GET /api/state off the UI thread."""

    finished = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, client: BackendClient) -> None:
        super().__init__()
        self._client = client

    def run(self) -> None:
        try:
            result = self._client.state()
        except BackendError as exc:
            self.failed.emit(str(exc))
        else:
            self.finished.emit(result)


class ListenWorker(QObject):
    """One dictation (microphone -> text) off the UI thread."""

    finished = pyqtSignal(str)   # transcribed text (empty = heard nothing)
    failed = pyqtSignal(str)     # plain-words error

    def __init__(self, engine: VoiceEngine) -> None:
        super().__init__()
        self._engine = engine

    def run(self) -> None:
        try:
            text = self._engine.listen_once()
        except VoiceError as exc:
            self.failed.emit(str(exc))
        else:
            self.finished.emit(text or "")


class ApiCallWorker(QObject):
    """A generic backend call off the UI thread (settings actions)."""

    finished = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn, *args, **kwargs) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self) -> None:
        try:
            result = self._fn(*self._args, **self._kwargs)
        except BackendError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - surface it plainly
            self.failed.emit(str(exc))
        else:
            self.finished.emit(result)


# Threads started by run_worker, kept alive until they finish.
# (A QThread destroyed while its thread is still running aborts the
# process, and a worker with no other references is garbage-collected
# before it ever runs — so both are held here, not by the caller.)
_live_threads: list[QThread] = []


class _WorkerForwarder(QObject):
    """Forwards a worker's results to plain callables, on the GUI thread.

    A worker signal connected straight to a plain function or lambda
    would run IN the worker thread (no QObject receiver to queue to),
    and must never touch widgets. The forwarder is a QObject living
    in the GUI thread, so its slots run there. It also drops the call
    when the owning widget is already gone.
    """

    def __init__(self, on_finished, on_failed, owner=None) -> None:
        super().__init__()
        self._on_finished = on_finished
        self._on_failed = on_failed
        self._owner = owner

    def _owner_gone(self) -> bool:
        if self._owner is None:
            return False
        try:
            import sip
            return sip.isdeleted(self._owner)
        except Exception:
            return False

    @pyqtSlot(object)
    def finished(self, result) -> None:
        if not self._owner_gone():
            self._on_finished(result)

    @pyqtSlot(str)
    def failed(self, message) -> None:
        if not self._owner_gone():
            self._on_failed(message)


def run_worker(worker: QObject, on_finished, on_failed, parent) -> QThread:
    """Run a worker QObject on its own QThread, wiring finished/failed
    to callbacks (on the GUI thread), and cleaning up when it ends.

    The thread is parentless and held in _live_threads until it
    finishes, so destroying the caller can never destroy a running
    thread (that aborts the process), and the worker is held on the
    thread so it cannot be garbage-collected before it runs — a
    fire-and-forget worker otherwise never completes at all.
    """
    thread = QThread()
    worker.moveToThread(thread)
    # Keep the worker (and the forwarder) alive for the thread's life.
    thread._worker = worker
    forwarder = _WorkerForwarder(on_finished, on_failed, parent)
    thread._forwarder = forwarder
    thread.started.connect(worker.run)
    worker.finished.connect(forwarder.finished)
    worker.failed.connect(forwarder.failed)
    worker.finished.connect(thread.quit)
    worker.failed.connect(thread.quit)
    thread.finished.connect(thread.deleteLater)
    thread.finished.connect(forwarder.deleteLater)
    _live_threads.append(thread)

    def _drop() -> None:
        if thread in _live_threads:
            _live_threads.remove(thread)

    thread.finished.connect(_drop)
    thread.start()
    return thread



# --- Mini mode: just the face, floating --------------------------------------
class MiniWindow(QWidget):
    """Mini mode is ONLY the visor face, floating.

    A frameless, transparent, borderless window — no title bar, no
    border, no frame, no text box, no button. Just the face. It floats
    bottom-right with a CLEAR gap above the taskbar (it never touches
    the taskbar): 56 px above the bottom of the screen's available
    geometry (which already excludes the taskbar) and 24 px from the
    right edge. It stays always-on-top and is draggable by the face.

    Voice-activated ONLY: a single click on the face starts voice
    listening (the main window dictates, chats, and speaks the reply
    back — replies in mini mode are always spoken). A double-click
    expands back to full mode. The wake phrase ("Hey Aries") also
    works here when it is on. There is no text input in mini mode.
    """

    # Clear gap above the taskbar — the face floats, it never touches.
    GAP_ABOVE_TASKBAR = 56
    GAP_FROM_RIGHT = 24

    closed = pyqtSignal()
    face_clicked = pyqtSignal()   # single click -> start voice listening
    face_double_clicked = pyqtSignal()  # double click -> back to full mode

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            Qt.WindowStaysOnTopHint | Qt.Tool | Qt.FramelessWindowHint,
        )
        self.setWindowTitle("Aries — mini")
        # Transparent window: only the visor itself is visible.
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent;")
        self.setMouseTracking(True)
        self._face_size = 200
        self.setFixedSize(self._face_size, self._face_size)
        self._drag_offset = None
        self._dragging = False
        self._user_moved = False
        self._docked = False
        self._press_pos = None
        self._press_time = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._core = AriesFaceWidget(self._face_size, self)
        self._core.installEventFilter(self)
        layout.addWidget(self._core, alignment=Qt.AlignCenter)

    # -- docking: bottom-right, floating with a clear gap -------------------
    def dock_bottom_right(self) -> None:
        """Move to the bottom-right corner, floating with a clear gap
        above the taskbar (never touching it)."""
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()  # excludes the taskbar
        x = avail.x() + avail.width() - self.width() - self.GAP_FROM_RIGHT
        y = (avail.y() + avail.height() - self.height()
             - self.GAP_ABOVE_TASKBAR)
        self.move(x, y)
        self._docked = True

    def gap_above_taskbar(self) -> int:
        """How far the window's bottom edge sits above the taskbar
        (used by tests)."""
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return -1
        avail = screen.availableGeometry()
        return (avail.y() + avail.height()
                - (self.y() + self.height()))

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().showEvent(event)
        if not self._docked and not self._user_moved:
            self.dock_bottom_right()
        self.raise_()
        self.activateWindow()

    # -- dragging (press the face, drag it anywhere) -------------------------
    def _begin_drag(self, global_pos) -> None:
        self._drag_offset = global_pos - self.pos()
        self._dragging = True
        self._core.set_dragging(True)

    def _move_drag(self, global_pos) -> None:
        if not self._dragging or self._drag_offset is None:
            return
        self.move(global_pos - self._drag_offset)
        self._user_moved = True

    def _end_drag(self) -> None:
        self._dragging = False
        self._drag_offset = None
        self._core.set_dragging(False)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.LeftButton:
            import time
            self._press_pos = event.pos()
            self._press_time = time.monotonic()
            self._begin_drag(event.globalPos())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._move_drag(event.globalPos())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.LeftButton and self._press_pos is not None:
            import time
            moved = (event.pos() - self._press_pos).manhattanLength()
            held = time.monotonic() - self._press_time
            # A plain click (not a drag): single click = listen,
            # double click = expand. Qt delivers a double-click as
            # two press/release pairs plus a DoubleClick event, so
            # the single-click action is fired on release only when
            # it was not part of a double-click (handled in
            # mouseDoubleClickEvent).
            if moved < 8 and held < 0.6:
                self.face_clicked.emit()
        self._press_pos = None
        self._end_drag()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.LeftButton:
            self.face_double_clicked.emit()
        super().mouseDoubleClickEvent(event)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt naming
        # The face is a child widget: forward its mouse events to the
        # window (drag, click, double-click) and its hover to the face.
        if watched is self._core:
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self.mousePressEvent(event)
                return True
            if event.type() == QEvent.MouseMove:
                self.mouseMoveEvent(event)
                return True
            if event.type() == QEvent.MouseButtonRelease:
                self.mouseReleaseEvent(event)
                return True
            if event.type() == QEvent.MouseButtonDblClick:
                self.mouseDoubleClickEvent(event)
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
        """Mirror the main window's face state."""
        self._core.set_state(state)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.closed.emit()
        super().closeEvent(event)



# --- Settings: every web gear tab, nothing dropped ---------------------------
class SettingsDialog(QDialog):
    """Settings — the web app's gear sheet, ported in full.

    Four tabs, exactly like the web: Aries, Memory, More, Connections.
    Nothing is dropped. Desktop-only extras (the NVIDIA API key
    fallback and the PC control master toggle) live in the Aries tab
    under "Desktop (this PC)".

    - Aries: profile, personality (preset + warmth/directness/humour
      sliders + notes), voice picker (ElevenLabs voices with Hear /
      Use this voice, plus the device's own voices), Talk back / Wake
      phrase / Search the web toggles.
    - Memory: the vault (filter, teach, approve pending, pin, forget),
      the read-only memory file, and full chat history with Clear.
    - More: veil grain (fine/coarse) + burst, tasks & reminders,
      permissions (Ask / Always / Never per capability), face login
      (enroll / remove / PIN), screenshot (take one or pick an image,
      ask about it), desktop app & installer (download).
    - Connections: Gmail / Spotify / WhatsApp status, Connect (opens
      the provider's sign-in in the system browser — never a fake
      login) / Disconnect, Check again.

    Local prefs (talk back, wake, web search, grain, voice picks, PC
    toggle) live in QSettings; server state is fetched from the
    backend when the dialog opens and refreshed after each action.
    """

    settings_changed = pyqtSignal()

    PERM_LABELS = {
        "microphone": "Microphone",
        "web_search": "Web search",
        "save_memory": "Save memories",
        "delete_memory": "Delete / forget memories",
        "clear_chat": "Clear chat history",
        "screenshot": "Read screenshots",
        "face_login": "Face login",
    }
    CAPABILITIES = (
        "microphone", "web_search", "save_memory", "delete_memory",
        "clear_chat", "screenshot", "face_login",
    )

    def __init__(self, client: BackendClient, voice: VoiceEngine,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._client = client
        self._voice = voice
        self._settings = QSettings("Aries", "AriesDesktop")
        self._state: dict = {}
        self._eleven_voices: list[dict] = []
        self._eleven_configured = False
        self._eleven_default_id: str | None = None
        self._eleven_default_name = "Adam"
        self.setWindowTitle("Aries — Settings")
        self.setMinimumWidth(560)
        self.setMinimumHeight(600)
        self.resize(640, 720)
        self.setStyleSheet(
            f"background: {BG}; color: {TEXT}; font-family: {SANS};")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        self._tabs = QTabWidget(self)
        self._tabs.setStyleSheet(
            f"QTabBar::tab {{ background: {PANEL_SEMI}; color: {TEXT}; "
            f"padding: 8px 18px; border: 1px solid {HAIRLINE}; "
            f"border-top-left-radius: 8px; border-top-right-radius: 8px; }}"
            f"QTabBar::tab:selected {{ background: {ACCENT}; color: white; }}"
            f"QTabWidget::pane {{ border: 1px solid {HAIRLINE}; }}")
        layout.addWidget(self._tabs, stretch=1)
        self._msg = QLabel(self)
        self._msg.setWordWrap(True)
        self._msg.setStyleSheet(f"color: {ACCENT_SOFT}; font-size: 12px;")
        layout.addWidget(self._msg)
        done_btn = QPushButton("Done", self)
        done_btn.setMinimumHeight(44)
        done_btn.setStyleSheet(
            f"background: {ACCENT}; color: white; padding: 8px 24px;"
            f"border: none; border-radius: 22px; font-weight: bold;"
            f"font-family: {SANS_SEMI}; font-size: 14px;")
        done_btn.clicked.connect(self.accept)
        layout.addWidget(done_btn, alignment=Qt.AlignRight)
        self._build_aries_tab()
        self._build_memory_tab()
        self._build_more_tab()
        self._build_connections_tab()
        self._refresh_state()
        self._load_eleven_voices()

    # -- helpers -------------------------------------------------------------
    def _say(self, text: str, error: bool = False) -> None:
        self._msg.setStyleSheet(
            f"color: {ERROR_RED if error else ACCENT_SOFT}; font-size: 12px;")
        self._msg.setText(text)

    def _run_api(self, fn, on_ok=None, on_err=None, *args, **kwargs) -> None:
        """Run a backend call off the UI thread."""
        def _ok(result):
            if on_ok is not None:
                on_ok(result)

        def _err(message):
            if on_err is not None:
                on_err(message)
            else:
                self._say(message, error=True)

        worker = ApiCallWorker(fn, *args, **kwargs)
        run_worker(worker, _ok, _err, self)

    def _refresh_state(self) -> None:
        """Re-fetch /api/state and rebuild the server-driven lists."""
        self._run_api(
            self._client.state,
            on_ok=self._on_state,
            on_err=lambda m: self._say(
                f"Could not load settings from the server: {m}", error=True))

    def _on_state(self, state: dict) -> None:
        self._state = state or {}
        self._rebuild_memory_tab()
        self._rebuild_more_tab()
        self._rebuild_connections_tab()
        person = self._state.get("personality") or {}
        if person:
            self._person_voice.setCurrentText(person.get("voice", "companion"))
            for key, slider in (("warmth", self._warmth),
                                ("directness", self._directness),
                                ("humour", self._humour)):
                slider.setValue(int(person.get(key, 50)))
            self._person_notes.setPlainText(person.get("notes", ""))
        profile = self._state.get("profile") or {}
        if profile:
            self._prof_name.setText(profile.get("name", ""))
            self._prof_call.setText(profile.get("call_name", ""))
            self._prof_about.setPlainText(profile.get("about", ""))
        mem_file = self._state.get("memory_file") or ""
        self._mem_file.setPlainText(
            mem_file or "No memory file on the server yet.")
        face = self._state.get("face") or {}
        self._face_status.setText(
            "Status: " + ("Enrolled" if face.get("enrolled") else
                          "Not enrolled"))

    def _load_eleven_voices(self) -> None:
        def _ok(result):
            self._eleven_configured = bool(result.get("configured"))
            self._eleven_voices = list(result.get("voices") or [])
            self._eleven_default_id = result.get("default_voice_id")
            self._eleven_default_name = (
                result.get("default_voice_name") or "Adam")
            self._rebuild_eleven_list()

        def _err(_message):
            self._eleven_configured = False
            self._eleven_voices = []
            self._rebuild_eleven_list()

        self._run_api(self._client.voices, on_ok=_ok, on_err=_err)

    def _scroll_wrap(self, widget: QWidget) -> QScrollArea:
        scroll = QScrollArea(self)
        scroll.setWidget(widget)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border: none; background: transparent;")
        return scroll

    def _section(self, parent_layout, title: str) -> None:
        label = QLabel(title, self)
        label.setStyleSheet(
            f"font-family: {SANS_SEMI}; font-size: 15px; font-weight: bold;"
            f"color: {TEXT}; margin-top: 10px;")
        parent_layout.addWidget(label)

    def _note(self, parent_layout, text: str) -> None:
        label = QLabel(text, self)
        label.setWordWrap(True)
        label.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        parent_layout.addWidget(label)

    def _styled_button(self, text: str) -> QPushButton:
        btn = QPushButton(text, self)
        btn.setStyleSheet(
            f"background: {ACCENT}; color: white; padding: 7px 16px;"
            f"border: none; border-radius: 16px; font-weight: bold;"
            f"font-family: {SANS_SEMI}; font-size: 13px;")
        return btn

    def _ghost_button(self, text: str) -> QPushButton:
        btn = QPushButton(text, self)
        btn.setStyleSheet(
            f"background: transparent; color: {TEXT}; padding: 6px 12px;"
            f"border: 1px solid {ACCENT_SOFT}; border-radius: 14px;"
            f"font-family: {SANS}; font-size: 12px;")
        return btn

    # -- Aries tab --------------------------------------------------------------
    def _build_aries_tab(self) -> None:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)
        # Personality
        self._section(layout, "Personality")
        form = QFormLayout()
        form.setSpacing(8)
        self._person_voice = QComboBox(self)
        self._person_voice.addItems(["companion", "mentor", "hype", "calm"])
        form.addRow("Voice", self._person_voice)
        self._warmth = QSlider(Qt.Horizontal, self)
        self._warmth.setRange(0, 100)
        self._directness = QSlider(Qt.Horizontal, self)
        self._directness.setRange(0, 100)
        self._humour = QSlider(Qt.Horizontal, self)
        self._humour.setRange(0, 100)
        form.addRow("Warmth", self._warmth)
        form.addRow("Directness", self._directness)
        form.addRow("Humour", self._humour)
        self._person_notes = QTextEdit(self)
        self._person_notes.setPlaceholderText("How should Aries act?")
        self._person_notes.setMaximumHeight(70)
        form.addRow("Character notes", self._person_notes)
        layout.addLayout(form)
        save_person = self._styled_button("Save personality")
        save_person.clicked.connect(self._on_save_personality)
        layout.addWidget(save_person, alignment=Qt.AlignLeft)
        # Profile
        self._section(layout, "Profile")
        pform = QFormLayout()
        pform.setSpacing(8)
        self._prof_name = QLineEdit(self)
        self._prof_call = QLineEdit(self)
        self._prof_about = QTextEdit(self)
        self._prof_about.setMaximumHeight(60)
        pform.addRow("Name", self._prof_name)
        pform.addRow("Call me", self._prof_call)
        pform.addRow("About me", self._prof_about)
        layout.addLayout(pform)
        save_profile = self._styled_button("Save profile")
        save_profile.clicked.connect(self._on_save_profile)
        layout.addWidget(save_profile, alignment=Qt.AlignLeft)
        # Voice picker
        self._section(layout, "Talk-back voice")
        self._eleven_status = QLabel("Loading voices…", self)
        self._eleven_status.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        layout.addWidget(self._eleven_status)
        self._eleven_list = QListWidget(self)
        self._eleven_list.setMaximumHeight(150)
        layout.addWidget(self._eleven_list)
        eleven_row = QHBoxLayout()
        self._eleven_hear = self._ghost_button("Hear")
        self._eleven_hear.clicked.connect(self._on_hear_eleven)
        self._eleven_use = self._styled_button("Use this voice")
        self._eleven_use.clicked.connect(self._on_use_eleven)
        self._eleven_default = self._ghost_button("Use Adam (default)")
        self._eleven_default.clicked.connect(self._on_default_eleven)
        eleven_row.addWidget(self._eleven_hear)
        eleven_row.addWidget(self._eleven_use)
        eleven_row.addWidget(self._eleven_default)
        layout.addLayout(eleven_row)
        self._note(layout, "ElevenLabs voices are the real narrator voice, "
                   "served from the server. Your pick is saved on this PC.")
        self._section(layout, "Voices on this device")
        self._note(layout, "Fallback only — used when the ElevenLabs voice "
                   "is unavailable.")
        self._device_list = QListWidget(self)
        self._device_list.setMaximumHeight(120)
        layout.addWidget(self._device_list)
        self._rebuild_device_list()
        dev_row = QHBoxLayout()
        self._dev_hear = self._ghost_button("Hear")
        self._dev_hear.clicked.connect(self._on_hear_device)
        self._dev_use = self._styled_button("Use this voice")
        self._dev_use.clicked.connect(self._on_use_device)
        self._dev_default = self._ghost_button("Use Aries' default")
        self._dev_default.clicked.connect(self._on_default_device)
        dev_row.addWidget(self._dev_hear)
        dev_row.addWidget(self._dev_use)
        dev_row.addWidget(self._dev_default)
        layout.addLayout(dev_row)
        # Talk & search toggles
        self._section(layout, "Talk & search")
        self._talk_back = QCheckBox("Talk back (read replies aloud)", self)
        self._talk_back.setChecked(bool(
            self._settings.value("talk_back", True, type=bool)))
        self._talk_back.toggled.connect(self._on_local_toggle)
        layout.addWidget(self._talk_back)
        self._wake_on = QCheckBox("Wake phrase (“Hey Aries”)", self)
        self._wake_on.setChecked(bool(
            self._settings.value("wake_on", False, type=bool)))
        self._wake_on.toggled.connect(self._on_local_toggle)
        layout.addWidget(self._wake_on)
        self._web_search = QCheckBox("Search the web", self)
        self._web_search.setChecked(bool(
            self._settings.value("web_search", False, type=bool)))
        self._web_search.toggled.connect(self._on_local_toggle)
        layout.addWidget(self._web_search)
        self._note(layout, "Talk back reads Aries' replies aloud. Wake "
                   "phrase listens for “Hey Aries”. Search the web grounds "
                   "the next answer in live results.")
        # Desktop-only: NVIDIA key + PC control
        self._section(layout, "Desktop (this PC)")
        self._note(layout, "The backend is the primary engine, so no key "
                   "is needed for normal chat. The key below is the "
                   "fallback for when the server cannot be reached, and "
                   "for PC control. It is saved in the OS credential "
                   "store and never shown here.")
        self._key_status = QLabel(self)
        self._key_status.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        layout.addWidget(self._key_status)
        key_row = QHBoxLayout()
        self._key_input = QLineEdit(self)
        self._key_input.setEchoMode(QLineEdit.Password)
        self._key_input.setPlaceholderText("Paste your NVIDIA API key here")
        key_row.addWidget(self._key_input, stretch=1)
        save_key = self._styled_button("Save key")
        save_key.clicked.connect(self._on_save_key)
        key_row.addWidget(save_key)
        layout.addLayout(key_row)
        self._key_message = QLabel(self)
        self._key_message.setWordWrap(True)
        self._key_message.setStyleSheet(
            f"color: {ACCENT_SOFT}; font-size: 12px;")
        layout.addWidget(self._key_message)
        clear_key = self._ghost_button("Remove the saved key")
        clear_key.clicked.connect(self._on_clear_key)
        layout.addWidget(clear_key, alignment=Qt.AlignLeft)
        self._pc_check = QCheckBox("Allow PC control", self)
        self._pc_check.setChecked(bool(
            self._settings.value("pc_control_enabled", False, type=bool)))
        self._pc_check.toggled.connect(self._on_local_toggle)
        layout.addWidget(self._pc_check)
        self._note(layout, "When this is on, Aries can open apps and "
                   "files, type, click, use the clipboard, run commands, "
                   "read/write files and check system info on this PC — "
                   "but ONLY after you confirm each plan in a pop-up. "
                   "Destructive actions always ask a second time. Off by "
                   "default. Desktop only — the webapp never gets this.")
        layout.addStretch(1)
        self._tabs.addTab(self._scroll_wrap(page), "Aries")
        self._refresh_key_status()

    def _rebuild_eleven_list(self) -> None:
        self._eleven_list.clear()
        saved = self._settings.value("eleven_voice_id", "", type=str)
        if not self._eleven_configured:
            self._eleven_status.setText(
                "The ElevenLabs voice is not set up on the server yet — "
                "add the key in Render and Aries will speak with a real "
                "narrator voice.")
            return
        now = "Adam (default)"
        if saved:
            match = next((v for v in self._eleven_voices
                          if v.get("voice_id") == saved), None)
            if match:
                now = match.get("name", "ElevenLabs voice")
        self._eleven_status.setText(f"Now using: {now}")
        for v in self._eleven_voices:
            labels = v.get("labels") or {}
            extra = ""
            if labels.get("gender"):
                extra = (f" ({labels['gender']}"
                         + (f", {labels['accent']}" if labels.get("accent")
                            else "") + ")")
            item = QListWidgetItem(f"{v.get('name', '')}{extra}")
            item.setData(Qt.UserRole, v.get("voice_id"))
            self._eleven_list.addItem(item)
            if v.get("voice_id") == saved:
                self._eleven_list.setCurrentItem(item)

    def _rebuild_device_list(self) -> None:
        self._device_list.clear()
        saved = self._settings.value("device_voice_name", "", type=str)
        voices = self._voice.device_voices()
        if not voices:
            item = QListWidgetItem(
                "No device voices found (pyttsx3 not available).")
            item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
            self._device_list.addItem(item)
            return
        for v in voices:
            item = QListWidgetItem(v.get("name", ""))
            item.setData(Qt.UserRole, v.get("id"))
            self._device_list.addItem(item)
            if v.get("name") == saved or v.get("id") == saved:
                self._device_list.setCurrentItem(item)

    def _on_save_personality(self) -> None:
        self._run_api(
            self._client.save_personality,
            on_ok=lambda _r: self._say("Personality saved."),
            voice=self._person_voice.currentText(),
            warmth=self._warmth.value(),
            directness=self._directness.value(),
            humour=self._humour.value(),
            notes=self._person_notes.toPlainText())

    def _on_save_profile(self) -> None:
        self._run_api(
            self._client.save_profile,
            on_ok=lambda _r: self._say("Profile saved."),
            name=self._prof_name.text(),
            call_name=self._prof_call.text(),
            about=self._prof_about.toPlainText())

    def _eleven_selected_id(self) -> str | None:
        item = self._eleven_list.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def _on_hear_eleven(self) -> None:
        voice_id = self._eleven_selected_id()
        if not voice_id:
            self._say("Pick an ElevenLabs voice from the list first.",
                      error=True)
            return
        self._say("Playing a preview…")
        threading = __import__("threading")
        threading.Thread(
            target=lambda: self._voice.preview_eleven_voice(
                voice_id, "Aries active. Ready to cook."),
            daemon=True).start()

    def _on_use_eleven(self) -> None:
        voice_id = self._eleven_selected_id()
        if not voice_id:
            self._say("Pick an ElevenLabs voice from the list first.",
                      error=True)
            return
        self._settings.setValue("eleven_voice_id", voice_id)
        self._rebuild_eleven_list()
        self._say("ElevenLabs voice saved — Aries will speak with it.")
        self.settings_changed.emit()

    def _on_default_eleven(self) -> None:
        self._settings.setValue("eleven_voice_id", "")
        self._rebuild_eleven_list()
        self._say("Back to the default ElevenLabs voice (Adam).")
        self.settings_changed.emit()

    def _device_selected_id(self) -> str | None:
        item = self._device_list.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def _on_hear_device(self) -> None:
        voice_id = self._device_selected_id()
        if not voice_id:
            self._say("Pick a device voice from the list first.",
                      error=True)
            return
        threading = __import__("threading")
        threading.Thread(
            target=lambda: self._voice.preview_device_voice(
                voice_id, "Aries active. Ready to cook."),
            daemon=True).start()

    def _on_use_device(self) -> None:
        item = self._device_list.currentItem()
        if item is None:
            self._say("Pick a device voice from the list first.",
                      error=True)
            return
        self._settings.setValue("device_voice_name", item.text())
        self._voice.set_device_voice(item.data(Qt.UserRole))
        self._say("Device voice saved — used when ElevenLabs is off.")
        self.settings_changed.emit()

    def _on_default_device(self) -> None:
        self._settings.setValue("device_voice_name", "")
        self._voice.set_device_voice(None)
        self._say("Back to Aries' default device voice.")
        self.settings_changed.emit()

    def _on_local_toggle(self) -> None:
        self._settings.setValue("talk_back", self._talk_back.isChecked())
        self._settings.setValue("wake_on", self._wake_on.isChecked())
        self._settings.setValue("web_search", self._web_search.isChecked())
        self._settings.setValue(
            "pc_control_enabled", self._pc_check.isChecked())
        self.settings_changed.emit()

    def _refresh_key_status(self) -> None:
        source = key_source()
        if source == "environment variable":
            text = ("Key status: set (from the NVIDIA_API_KEY environment "
                    "variable — that one wins over a saved key).")
        elif source == "saved in the OS credential store":
            text = "Key status: set (saved in the OS credential store)."
        else:
            text = "Key status: not set."
        self._key_status.setText(text)

    def _on_save_key(self) -> None:
        try:
            set_api_key(self._key_input.text())
        except (ValueError, RuntimeError) as exc:
            self._key_message.setStyleSheet(
                f"color: {ERROR_RED}; font-size: 12px;")
            self._key_message.setText(str(exc))
            return
        self._key_input.clear()
        self._key_message.setStyleSheet(
            f"color: {ACCENT_SOFT}; font-size: 12px;")
        self._key_message.setText(
            "Saved to the OS credential store. The key itself is never shown.")
        self._refresh_key_status()
        self.settings_changed.emit()

    def _on_clear_key(self) -> None:
        if delete_api_key():
            self._key_message.setStyleSheet(
                f"color: {ACCENT_SOFT}; font-size: 12px;")
            self._key_message.setText("Saved key removed.")
        else:
            self._key_message.setStyleSheet(
                f"color: {MUTED}; font-size: 12px;")
            self._key_message.setText("There was no saved key to remove.")
        self._refresh_key_status()
        self.settings_changed.emit()

    # -- Memory tab ---------------------------------------------------------------
    def _build_memory_tab(self) -> None:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)
        self._section(layout, "Memory vault")
        self._mem_filter = QLineEdit(self)
        self._mem_filter.setPlaceholderText("Filter memories…")
        self._mem_filter.textChanged.connect(self._rebuild_memory_tab)
        layout.addWidget(self._mem_filter)
        add_row = QHBoxLayout()
        self._mem_text = QLineEdit(self)
        self._mem_text.setPlaceholderText("Teach Aries something…")
        add_row.addWidget(self._mem_text, stretch=1)
        add_mem = self._styled_button("Remember")
        add_mem.clicked.connect(self._on_add_memory)
        add_row.addWidget(add_mem)
        layout.addLayout(add_row)
        self._mem_list_wrap = QVBoxLayout()
        self._mem_list_wrap.setSpacing(6)
        layout.addLayout(self._mem_list_wrap)
        self._mem_empty = QLabel("No memories yet. Teach Aries something "
                                 "above, or say “remember …” in chat.", self)
        self._mem_empty.setWordWrap(True)
        self._mem_empty.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        layout.addWidget(self._mem_empty)
        self._section(layout, "About you — from Aries' memory file")
        self._note(layout, "Durable facts Aries always knows, kept in its "
                   "memory file on the server. This is read-only — "
                   "day-to-day things Aries learns live in the vault above.")
        self._mem_file = QTextEdit(self)
        self._mem_file.setReadOnly(True)
        self._mem_file.setMaximumHeight(140)
        layout.addWidget(self._mem_file)
        self._section(layout, "Full history")
        clear_btn = self._ghost_button("Clear chat history")
        clear_btn.clicked.connect(self._on_clear_history)
        layout.addWidget(clear_btn, alignment=Qt.AlignLeft)
        self._history_list = QListWidget(self)
        self._history_list.setMaximumHeight(220)
        layout.addWidget(self._history_list)
        layout.addStretch(1)
        self._tabs.addTab(self._scroll_wrap(page), "Memory")

    def _rebuild_memory_tab(self) -> None:
        # Clear the vault rows.
        while self._mem_list_wrap.count():
            item = self._mem_list_wrap.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        memories = list(self._state.get("memories") or [])
        filt = (self._mem_filter.text() if hasattr(self, "_mem_filter")
                else "").lower()
        if filt:
            memories = [m for m in memories
                        if filt in (m.get("text") or "").lower()]
        self._mem_empty.setVisible(len(memories) == 0)
        for m in memories:
            row = QWidget(self)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)
            prefix = ""
            if m.get("pending"):
                prefix += "⏳ Awaiting approval — "
            if m.get("pinned"):
                prefix += "📌 "
            label = QLabel(prefix + (m.get("text") or ""), self)
            label.setWordWrap(True)
            row_layout.addWidget(label, stretch=1)
            if m.get("pending"):
                approve = self._ghost_button("Approve")
                approve.clicked.connect(
                    lambda _c, mid=m["id"]: self._on_approve_memory(mid))
                row_layout.addWidget(approve)
            pin = self._ghost_button(
                "Unpin" if m.get("pinned") else "Pin")
            pin.clicked.connect(
                lambda _c, mid=m["id"]: self._on_pin_memory(mid))
            row_layout.addWidget(pin)
            forget = self._ghost_button("Forget")
            forget.clicked.connect(
                lambda _c, mid=m["id"]: self._on_forget_memory(mid))
            row_layout.addWidget(forget)
            self._mem_list_wrap.addWidget(row)
        # History list.
        self._history_list.clear()
        for msg in (self._state.get("messages") or []):
            who = "You" if msg.get("role") == "user" else "Aries"
            item = QListWidgetItem(f"{who}: {msg.get('content', '')}")
            self._history_list.addItem(item)

    def _on_add_memory(self) -> None:
        text = self._mem_text.text().strip()
        if not text:
            return
        self._run_api(
            self._client.add_memory,
            on_ok=lambda _r: (self._mem_text.clear(),
                              self._say("Memory saved."),
                              self._refresh_state()),
            text=text, category="general")

    def _on_approve_memory(self, memory_id: int) -> None:
        self._run_api(
            self._client.approve_memory,
            on_ok=lambda _r: (self._say("Memory approved."),
                              self._refresh_state()),
            memory_id=memory_id)

    def _on_pin_memory(self, memory_id: int) -> None:
        self._run_api(
            self._client.pin_memory,
            on_ok=lambda _r: self._refresh_state(),
            memory_id=memory_id)

    def _on_forget_memory(self, memory_id: int) -> None:
        answer = QMessageBox.question(
            self, "Forget this memory?",
            "Aries will forget this. This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        self._run_api(
            self._client.delete_memory,
            on_ok=lambda _r: (self._say("Memory forgotten."),
                              self._refresh_state()),
            memory_id=memory_id)

    def _on_clear_history(self) -> None:
        answer = QMessageBox.question(
            self, "Clear all chat history?",
            "This clears the whole conversation on the server.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        self._run_api(
            self._client.clear_chat,
            on_ok=lambda _r: (self._say("Chat history cleared."),
                              self._refresh_state()),
            consent=True)


    # -- More tab ------------------------------------------------------------------
    grain_changed = pyqtSignal(str)
    burst_requested = pyqtSignal()

    def _build_more_tab(self) -> None:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)
        # Veil
        self._section(layout, "Veil")
        self._note(layout, "The veil is the dithered grain over the "
                   "mountains. Tap the veil itself for a burst, or use "
                   "these.")
        grain_row = QHBoxLayout()
        self._grain_fine = self._ghost_button("Fine grain")
        self._grain_fine.setCheckable(True)
        self._grain_fine.clicked.connect(lambda: self._on_grain("fine"))
        self._grain_coarse = self._ghost_button("Coarse grain")
        self._grain_coarse.setCheckable(True)
        self._grain_coarse.clicked.connect(lambda: self._on_grain("coarse"))
        grain_row.addWidget(self._grain_fine)
        grain_row.addWidget(self._grain_coarse)
        layout.addLayout(grain_row)
        burst_btn = self._ghost_button("Release a burst")
        burst_btn.clicked.connect(self.burst_requested.emit)
        layout.addWidget(burst_btn, alignment=Qt.AlignLeft)
        self._sync_grain_buttons()
        # Tasks & reminders
        self._section(layout, "Tasks & reminders")
        task_row = QHBoxLayout()
        self._task_title = QLineEdit(self)
        self._task_title.setPlaceholderText("New task…")
        task_row.addWidget(self._task_title, stretch=1)
        self._task_due = QLineEdit(self)
        self._task_due.setPlaceholderText("Due (YYYY-MM-DD HH:MM, optional)")
        task_row.addWidget(self._task_due, stretch=1)
        add_task = self._styled_button("Add")
        add_task.clicked.connect(self._on_add_task)
        task_row.addWidget(add_task)
        layout.addLayout(task_row)
        self._task_list_wrap = QVBoxLayout()
        self._task_list_wrap.setSpacing(6)
        layout.addLayout(self._task_list_wrap)
        self._note(layout, "Tip: in chat, say “remind me to call Kelfy "
                   "tomorrow at 9am” and Aries saves it.")
        # Permissions
        self._section(layout, "Permissions")
        self._note(layout, "Aries asks before anything sensitive. "
                   "“Never” hard-blocks on the server.")
        self._perm_combos: dict[str, QComboBox] = {}
        perm_form = QFormLayout()
        perm_form.setSpacing(6)
        for cap in self.CAPABILITIES:
            combo = QComboBox(self)
            combo.addItems(["Ask me", "Always allow", "Never"])
            combo.setProperty("capability", cap)
            combo.currentIndexChanged.connect(self._on_perm_changed)
            self._perm_combos[cap] = combo
            perm_form.addRow(self.PERM_LABELS.get(cap, cap), combo)
        layout.addLayout(perm_form)
        # Face login
        self._section(layout, "Face login")
        self._face_status = QLabel("Status: …", self)
        layout.addWidget(self._face_status)
        face_row = QHBoxLayout()
        self._face_file_label = QLabel("No photo chosen.", self)
        self._face_file_label.setStyleSheet(
            f"color: {MUTED}; font-size: 12px;")
        face_row.addWidget(self._face_file_label, stretch=1)
        pick_face = self._ghost_button("Choose a photo…")
        pick_face.clicked.connect(self._on_pick_face_photo)
        face_row.addWidget(pick_face)
        webcam_btn = self._ghost_button("Use webcam")
        webcam_btn.clicked.connect(self._on_webcam_photo)
        face_row.addWidget(webcam_btn)
        layout.addLayout(face_row)
        pin_row = QHBoxLayout()
        self._face_pin = QLineEdit(self)
        self._face_pin.setEchoMode(QLineEdit.Password)
        self._face_pin.setPlaceholderText(
            "Unlock PIN (optional, sets fallback)")
        pin_row.addWidget(self._face_pin, stretch=1)
        enroll = self._styled_button("Enroll my face")
        enroll.clicked.connect(self._on_enroll_face)
        pin_row.addWidget(enroll)
        layout.addLayout(pin_row)
        remove_face = self._ghost_button("Remove enrollment")
        remove_face.clicked.connect(self._on_remove_face)
        layout.addWidget(remove_face, alignment=Qt.AlignLeft)
        self._note(layout, "OpenCV LBPH recognition — a convenience lock, "
                   "not biometric-grade security. The PIN is the real "
                   "fallback; only its hash is stored.")
        # Screenshot
        self._section(layout, "Screenshot")
        self._note(layout, "Aries asks permission before reading any image.")
        shot_row = QHBoxLayout()
        take_shot = self._styled_button("Take a screenshot")
        take_shot.clicked.connect(self._on_take_screenshot)
        shot_row.addWidget(take_shot)
        pick_shot = self._ghost_button("Choose an image…")
        pick_shot.clicked.connect(self._on_pick_screenshot)
        shot_row.addWidget(pick_shot)
        layout.addLayout(shot_row)
        self._shot_label = QLabel("No image chosen.", self)
        self._shot_label.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        layout.addWidget(self._shot_label)
        self._shot_q = QLineEdit(self)
        self._shot_q.setPlaceholderText("Ask about the image (optional)")
        layout.addWidget(self._shot_q)
        describe = self._styled_button("Describe this image")
        describe.clicked.connect(self._on_describe_screenshot)
        layout.addWidget(describe, alignment=Qt.AlignLeft)
        self._shot_desc = QTextEdit(self)
        self._shot_desc.setReadOnly(True)
        self._shot_desc.setMaximumHeight(120)
        layout.addWidget(self._shot_desc)
        self._shot_bytes: bytes | None = None
        self._shot_name = "screenshot.png"
        self._face_bytes: bytes | None = None
        self._face_name = "face.jpg"
        # Desktop app & installer
        self._section(layout, "Desktop app & installer")
        self._note(layout, "The desktop installer is packaged from the "
                   "PyQt app in the Aries repo (aries/). It fills the full "
                   "screen when activated, is freely resizable, and "
                   "tapping the bot drops it into mini mode floating "
                   "bottom-right, just above the taskbar.")
        self._note(layout, "Engine key: server-side only. The installer "
                   "never contains an API key — the desktop talks to the "
                   "SaaS backend instead. Aries is private to Epsilon "
                   "and Kelfy until it goes commercial.")
        download = self._styled_button("Download for Windows (Installer)")
        download.clicked.connect(
            lambda: webbrowser.open(
                "https://github.com/Epsilon-Odinson/Aries/releases"))
        layout.addWidget(download, alignment=Qt.AlignLeft)
        self._note(layout, "Version 0.5.0 — the installer sets up Aries, "
                   "offers a desktop shortcut and a taskbar pin. The "
                   "portable single file (Aries.exe) is on the same "
                   "release page.")
        layout.addStretch(1)
        self._tabs.addTab(self._scroll_wrap(page), "More")

    def _sync_grain_buttons(self) -> None:
        grain = self._settings.value("grain", "fine", type=str)
        self._grain_fine.setChecked(grain == "fine")
        self._grain_coarse.setChecked(grain == "coarse")

    def _on_grain(self, name: str) -> None:
        self._settings.setValue("grain", name)
        self._sync_grain_buttons()
        self.grain_changed.emit(name)
        self.settings_changed.emit()

    def _rebuild_more_tab(self) -> None:
        # Tasks list.
        while self._task_list_wrap.count():
            item = self._task_list_wrap.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for t in (self._state.get("tasks") or []):
            row = QWidget(self)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)
            text = ("✓ " if t.get("done") else "") + (t.get("title") or "")
            if t.get("due_at"):
                text += f" — due {t['due_at']}"
            label = QLabel(text, self)
            label.setWordWrap(True)
            row_layout.addWidget(label, stretch=1)
            done = self._ghost_button("Undo" if t.get("done") else "Done")
            done.clicked.connect(
                lambda _c, tid=t["id"]: self._on_toggle_task(tid))
            row_layout.addWidget(done)
            delete = self._ghost_button("Delete")
            delete.clicked.connect(
                lambda _c, tid=t["id"]: self._on_delete_task(tid))
            row_layout.addWidget(delete)
            self._task_list_wrap.addWidget(row)
        # Permissions.
        perms = self._state.get("permissions") or {}
        value_to_index = {"ask": 0, "always": 1, "never": 2}
        for cap, combo in self._perm_combos.items():
            setting = perms.get(cap, "ask")
            combo.blockSignals(True)
            combo.setCurrentIndex(value_to_index.get(setting, 0))
            combo.blockSignals(False)

    def _on_add_task(self) -> None:
        title = self._task_title.text().strip()
        if not title:
            return
        due_raw = self._task_due.text().strip()
        due_at = None
        if due_raw:
            # Accept "YYYY-MM-DD HH:MM" (and date-only).
            try:
                from datetime import datetime
                if len(due_raw) == 10:
                    due_raw += " 09:00"
                due = datetime.strptime(due_raw, "%Y-%m-%d %H:%M")
                due_at = due.isoformat()
            except ValueError:
                self._say("Due date format: YYYY-MM-DD HH:MM",
                          error=True)
                return
        self._run_api(
            self._client.add_task,
            on_ok=lambda _r: (self._task_title.clear(),
                              self._task_due.clear(),
                              self._say("Task added."),
                              self._refresh_state()),
            title=title, due_at=due_at)

    def _on_toggle_task(self, task_id: int) -> None:
        self._run_api(self._client.toggle_task,
                      on_ok=lambda _r: self._refresh_state(),
                      task_id=task_id)

    def _on_delete_task(self, task_id: int) -> None:
        answer = QMessageBox.question(
            self, "Delete this task?",
            "Delete this task for good?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        self._run_api(self._client.delete_task,
                      on_ok=lambda _r: self._refresh_state(),
                      task_id=task_id)

    def _on_perm_changed(self) -> None:
        combo = self.sender()
        if combo is None:
            return
        cap = combo.property("capability")
        index_to_value = {0: "ask", 1: "always", 2: "never"}
        setting = index_to_value.get(combo.currentIndex(), "ask")
        self._run_api(
            self._client.save_permission,
            on_ok=lambda _r: self._say(
                f"Permission saved: {self.PERM_LABELS.get(cap, cap)} → "
                f"{setting}."),
            capability=cap, setting=setting)

    def _on_pick_face_photo(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a photo of your face", "",
            "Images (*.png *.jpg *.jpeg)")
        if not path:
            return
        try:
            with open(path, "rb") as f:
                self._face_bytes = f.read()
            self._face_name = path.split("/")[-1].split("\\")[-1]
            self._face_file_label.setText(f"Photo: {self._face_name}")
        except OSError as exc:
            self._say(f"Could not read that photo: {exc}", error=True)

    def _on_webcam_photo(self) -> None:
        """Capture a face photo from the webcam (OpenCV). Falls back
        to the file picker when OpenCV or the camera is missing."""
        try:
            import cv2
        except ImportError:
            self._say("OpenCV is not installed, so the webcam cannot be "
                      "used here — choose a photo file instead.",
                      error=True)
            return
        try:
            cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                self._say("Could not open the webcam — choose a photo "
                          "file instead.", error=True)
                return
            ok, frame = cap.read()
            cap.release()
            if not ok or frame is None:
                self._say("The webcam did not return a photo — choose a "
                          "photo file instead.", error=True)
                return
            ok, buf = cv2.imencode(".jpg", frame)
            if not ok:
                self._say("Could not encode the webcam photo.",
                          error=True)
                return
            self._face_bytes = bytes(buf.tobytes())
            self._face_name = "webcam.jpg"
            self._face_file_label.setText("Photo: webcam capture")
            self._say("Webcam photo captured — press Enroll my face.")
        except Exception as exc:  # noqa: BLE001 - plain words
            self._say(f"Webcam capture failed: {exc}", error=True)

    def _on_enroll_face(self) -> None:
        if not self._face_bytes:
            self._say("Choose a photo of your face first (or use the "
                      "webcam).", error=True)
            return
        self._say("Enrolling…")
        self._run_api(
            self._client.enroll_face,
            on_ok=lambda _r: (self._say("Face enrolled. Face login is on."),
                              self._refresh_state()),
            image_bytes=self._face_bytes, filename=self._face_name,
            name=self._prof_name.text(), pin=self._face_pin.text())

    def _on_remove_face(self) -> None:
        answer = QMessageBox.question(
            self, "Remove face enrollment?",
            "Face login will be turned off.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        self._run_api(
            self._client.remove_face,
            on_ok=lambda _r: (self._say("Face enrollment removed."),
                              self._refresh_state()))

    def _on_take_screenshot(self) -> None:
        """Capture the screen (pyautogui) and keep it for describing."""
        try:
            import pyautogui
            import io as _io
            shot = pyautogui.screenshot()
            buf = _io.BytesIO()
            shot.save(buf, format="PNG")
            self._shot_bytes = buf.getvalue()
            self._shot_name = "screenshot.png"
            self._shot_label.setText("Screenshot captured — ready.")
        except Exception as exc:  # noqa: BLE001 - plain words
            self._say(f"Could not take a screenshot: {exc}", error=True)

    def _on_pick_screenshot(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose an image", "", "Images (*.png *.jpg *.jpeg)")
        if not path:
            return
        try:
            with open(path, "rb") as f:
                self._shot_bytes = f.read()
            self._shot_name = path.split("/")[-1].split("\\")[-1]
            self._shot_label.setText(f"Image: {self._shot_name}")
        except OSError as exc:
            self._say(f"Could not read that image: {exc}", error=True)

    def _on_describe_screenshot(self) -> None:
        if not self._shot_bytes:
            self._say("Take a screenshot or choose an image first.",
                      error=True)
            return
        self._say("Looking at the image…")
        self._run_api(
            self._client.describe_screenshot,
            on_ok=lambda r: (self._shot_desc.setPlainText(
                r.get("description", "")), self._say("")),
            image_bytes=self._shot_bytes, filename=self._shot_name,
            question=self._shot_q.text(), consent=True)

    # -- Connections tab ---------------------------------------------------------
    def _build_connections_tab(self) -> None:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)
        self._section(layout, "Connections")
        self._note(layout, "Connect an account once and the skills that "
                   "need it just work. Aries keeps the sign-in on the "
                   "server — your password and tokens never appear here.")
        self._conn_err = QLabel("", self)
        self._conn_err.setWordWrap(True)
        self._conn_err.setStyleSheet(f"color: {ERROR_RED}; font-size: 12px;")
        layout.addWidget(self._conn_err)
        self._conn_wrap = QVBoxLayout()
        self._conn_wrap.setSpacing(10)
        layout.addLayout(self._conn_wrap)
        check = self._ghost_button("Check again")
        check.clicked.connect(self._refresh_connections)
        layout.addWidget(check, alignment=Qt.AlignLeft)
        self._note(layout, "Setting up Gmail or Spotify needs a free "
                   "developer app first. In Google Cloud Console / "
                   "Spotify Developer Dashboard, create an OAuth app and "
                   "add these redirect URLs — "
                   "https://aries-ahvo.onrender.com/api/connections/"
                   "google/callback and "
                   "https://aries-ahvo.onrender.com/api/connections/"
                   "spotify/callback — then add the client ID and secret "
                   "in Render (Environment) as GOOGLE_CLIENT_ID / "
                   "GOOGLE_CLIENT_SECRET and SPOTIFY_CLIENT_ID / "
                   "SPOTIFY_CLIENT_SECRET.")
        layout.addStretch(1)
        self._tabs.addTab(self._scroll_wrap(page), "Connections")
        self._refresh_connections()

    def _refresh_connections(self) -> None:
        self._run_api(self._client.connections,
                      on_ok=self._on_connections,
                      on_err=lambda m: self._conn_err.setText(m))

    def _rebuild_connections_tab(self) -> None:
        """Refresh the Connections rows from the server (called when
        /api/state reloads). The rows come from /api/connections,
        not from the state payload."""
        self._refresh_connections()

    def _on_connections(self, conns: dict) -> None:
        self._conn_err.setText("")
        while self._conn_wrap.count():
            item = self._conn_wrap.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        descs = {
            "google": "Let Aries read and summarise your email.",
            "spotify": "Let Aries play your music.",
            "whatsapp": "Send and read your WhatsApp messages.",
        }
        for key in ("google", "spotify", "whatsapp"):
            row = conns.get(key) or {}
            box = QFrame(self)
            box.setStyleSheet(
                f"border: 1px solid {HAIRLINE}; border-radius: 10px; "
                f"padding: 8px;")
            box_layout = QVBoxLayout(box)
            box_layout.setSpacing(4)
            name = QLabel(row.get("name", key), self)
            name.setStyleSheet(
                f"font-family: {SANS_SEMI}; font-size: 14px; "
                f"font-weight: bold;")
            box_layout.addWidget(name)
            status = ("Connected" + (f" — {row['account']}"
                                     if row.get("account") else "")
                      if row.get("connected") else "Not connected")
            status_label = QLabel(status, self)
            status_label.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
            box_layout.addWidget(status_label)
            box_layout.addWidget(QLabel(descs.get(key, ""), self))
            if row.get("note"):
                note = QLabel(row["note"], self)
                note.setWordWrap(True)
                note.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
                box_layout.addWidget(note)
            if not row.get("connected") and not row.get("note") \
                    and not row.get("configured"):
                box_layout.addWidget(QLabel(
                    "Not set up on the server yet — add the "
                    f"{'Google' if key == 'google' else 'Spotify'} "
                    "client in Render first (see below).", self))
            btn_row = QHBoxLayout()
            if row.get("connected"):
                disconnect = self._ghost_button("Disconnect")
                disconnect.clicked.connect(
                    lambda _c, k=key: self._on_disconnect(k))
                btn_row.addWidget(disconnect)
            elif not row.get("note"):
                connect = self._styled_button("Connect")
                connect.setEnabled(bool(row.get("configured")))
                connect.clicked.connect(
                    lambda _c, k=key: self._on_connect(k))
                btn_row.addWidget(connect)
            btn_row.addStretch(1)
            box_layout.addLayout(btn_row)
            self._conn_wrap.addWidget(box)

    def _on_connect(self, service: str) -> None:
        """Open the provider's OAuth sign-in in the system browser.
        The server stores the tokens; nothing is faked here."""
        def _ok(url):
            if url:
                webbrowser.open(url)
                self._say("Sign-in opened in your browser — finish it "
                          "there, then press Check again.")
            else:
                self._say("No sign-in URL came back from the server.",
                          error=True)

        self._run_api(self._client.connect_url, on_ok=_ok,
                      service=service)

    def _on_disconnect(self, service: str) -> None:
        label = "Gmail" if service == "google" else "Spotify"
        answer = QMessageBox.question(
            self, f"Disconnect {label}?",
            f"Disconnect {label}? Aries will no longer be able to use "
            "that account.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        self._run_api(self._client.disconnect,
                      on_ok=lambda _r: self._refresh_connections(),
                      service=service)



# --- Face lock screen ---------------------------------------------------------
class LockScreen(QDialog):
    """Shown at startup when a face is enrolled and face login is on.

    Unlock with your face (webcam or a photo file) or with the PIN.
    Face login is a convenience lock (OpenCV LBPH), not biometric-grade
    security — the PIN is the real fallback."""

    def __init__(self, client: BackendClient, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._client = client
        self.setWindowTitle("Aries is locked")
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setStyleSheet(
            f"background: {BG}; color: {TEXT}; font-family: {SANS};")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)
        title = QLabel("ARIES", self)
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            f"color: {CREAM}; font-family: {SANS_SEMI}; font-size: 28px;"
            "font-weight: bold; letter-spacing: 6px;")
        layout.addWidget(title)
        self._msg = QLabel(
            "Aries is locked. Show your face, or enter your PIN.", self)
        self._msg.setWordWrap(True)
        self._msg.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._msg)
        self._photo_bytes: bytes | None = None
        photo_row = QHBoxLayout()
        pick = QPushButton("Choose a photo…", self)
        pick.setStyleSheet(
            f"background: transparent; color: {TEXT}; padding: 8px 14px;"
            f"border: 1px solid {ACCENT_SOFT}; border-radius: 16px;")
        pick.clicked.connect(self._on_pick_photo)
        photo_row.addWidget(pick)
        webcam = QPushButton("Use webcam", self)
        webcam.setStyleSheet(
            f"background: transparent; color: {TEXT}; padding: 8px 14px;"
            f"border: 1px solid {ACCENT_SOFT}; border-radius: 16px;")
        webcam.clicked.connect(self._on_webcam)
        photo_row.addWidget(webcam)
        layout.addLayout(photo_row)
        verify = QPushButton("Unlock with my face", self)
        verify.setMinimumHeight(44)
        verify.setStyleSheet(
            f"background: {ACCENT}; color: white; padding: 8px 20px;"
            f"border: none; border-radius: 22px; font-weight: bold;"
            f"font-family: {SANS_SEMI};")
        verify.clicked.connect(self._on_verify)
        layout.addWidget(verify)
        pin_row = QHBoxLayout()
        self._pin = QLineEdit(self)
        self._pin.setEchoMode(QLineEdit.Password)
        self._pin.setPlaceholderText("Unlock PIN")
        pin_row.addWidget(self._pin, stretch=1)
        unlock_pin = QPushButton("Unlock with PIN", self)
        unlock_pin.setMinimumHeight(44)
        unlock_pin.setStyleSheet(
            f"background: {ACCENT}; color: white; padding: 8px 20px;"
            f"border: none; border-radius: 22px; font-weight: bold;"
            f"font-family: {SANS_SEMI};")
        unlock_pin.clicked.connect(self._on_unlock_pin)
        pin_row.addWidget(unlock_pin)
        layout.addLayout(pin_row)
        fine = QLabel("Face login is a convenience lock (OpenCV LBPH), "
                      "not biometric-grade security.", self)
        fine.setWordWrap(True)
        fine.setAlignment(Qt.AlignCenter)
        fine.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        layout.addWidget(fine)

    def _say(self, text: str) -> None:
        self._msg.setText(text)

    def _on_pick_photo(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a photo of your face", "",
            "Images (*.png *.jpg *.jpeg)")
        if not path:
            return
        try:
            with open(path, "rb") as f:
                self._photo_bytes = f.read()
            self._say("Photo chosen — press Unlock with my face.")
        except OSError as exc:
            self._say(f"Could not read that photo: {exc}")

    def _on_webcam(self) -> None:
        try:
            import cv2
        except ImportError:
            self._say("OpenCV is not installed — choose a photo file "
                      "instead.")
            return
        try:
            cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                self._say("Could not open the webcam.")
                return
            ok, frame = cap.read()
            cap.release()
            if not ok or frame is None:
                self._say("The webcam did not return a photo.")
                return
            ok, buf = cv2.imencode(".jpg", frame)
            if not ok:
                self._say("Could not encode the webcam photo.")
                return
            self._photo_bytes = bytes(buf.tobytes())
            self._say("Webcam photo captured — press Unlock with my face.")
        except Exception as exc:  # noqa: BLE001 - plain words
            self._say(f"Webcam capture failed: {exc}")

    def _on_verify(self) -> None:
        if not self._photo_bytes:
            self._say("Choose a photo of your face first.")
            return
        self._say("Checking…")

        def _ok(result):
            if result.get("match"):
                self._say("Welcome back.")
                self.accept()
            else:
                self._say("That face doesn't match. Try again, or use "
                          "your PIN.")

        def _err(message):
            self._say(message)

        worker = ApiCallWorker(
            self._client.verify_face, self._photo_bytes, "face.jpg")
        run_worker(worker, _ok, _err, self)

    def _on_unlock_pin(self) -> None:
        pin = self._pin.text().strip()
        if not pin:
            self._say("Enter your PIN first.")
            return

        def _ok(result):
            if result.get("ok"):
                self.accept()
            else:
                self._say("Wrong PIN.")

        worker = ApiCallWorker(self._client.unlock_pin, pin)
        run_worker(worker, _ok, lambda m: self._say(m), self)


# --- First-run setup wizard ---------------------------------------------------
class SetupWizard(QDialog):
    """First run: name, voice, permission defaults. Never asks for an
    API key — the engine key stays server-side only."""

    def __init__(self, client: BackendClient, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._client = client
        self.setWindowTitle("Welcome to Aries")
        self.setModal(True)
        self.setMinimumWidth(460)
        self.setStyleSheet(
            f"background: {BG}; color: {TEXT}; font-family: {SANS};")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)
        title = QLabel("Welcome to Aries", self)
        title.setStyleSheet(
            f"font-family: {SANS_SEMI}; font-size: 20px; font-weight: bold;")
        layout.addWidget(title)
        self._note = QLabel(
            "One thing setup never asks for: an API key. The engine key "
            "stays server-side only, never in the app and never on your "
            "device.", self)
        self._note.setWordWrap(True)
        self._note.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        layout.addWidget(self._note)
        form = QFormLayout()
        form.setSpacing(10)
        self._name = QLineEdit(self)
        self._name.setPlaceholderText("Your name")
        form.addRow("What should Aries call you?", self._name)
        self._voice = QComboBox(self)
        self._voice.addItems(["companion", "mentor", "hype", "calm"])
        form.addRow("Pick a voice", self._voice)
        layout.addLayout(form)
        self._perm_combos: dict[str, QComboBox] = {}
        perm_form = QFormLayout()
        perm_form.setSpacing(6)
        for cap in ("web_search", "save_memory", "microphone"):
            combo = QComboBox(self)
            combo.addItems(["Ask me", "Always allow", "Never"])
            self._perm_combos[cap] = combo
            perm_form.addRow(
                SettingsDialog.PERM_LABELS.get(cap, cap), combo)
        layout.addLayout(perm_form)
        self._msg = QLabel("", self)
        self._msg.setWordWrap(True)
        self._msg.setStyleSheet(f"color: {ACCENT_SOFT}; font-size: 12px;")
        layout.addWidget(self._msg)
        row = QHBoxLayout()
        skip = QPushButton("Skip setup", self)
        skip.setStyleSheet(
            f"background: transparent; color: {MUTED}; padding: 8px 16px;"
            f"border: 1px solid {ACCENT_SOFT}; border-radius: 16px;")
        skip.clicked.connect(self.reject)
        row.addWidget(skip)
        row.addStretch(1)
        finish = QPushButton("Finish — Aries active. Ready to cook.", self)
        finish.setMinimumHeight(44)
        finish.setStyleSheet(
            f"background: {ACCENT}; color: white; padding: 8px 20px;"
            f"border: none; border-radius: 22px; font-weight: bold;"
            f"font-family: {SANS_SEMI};")
        finish.clicked.connect(self._on_finish)
        row.addWidget(finish)
        layout.addLayout(row)

    def _on_finish(self) -> None:
        name = self._name.text().strip()
        voice = self._voice.currentText()
        index_to_value = {0: "ask", 1: "always", 2: "never"}
        perms = {cap: index_to_value.get(combo.currentIndex(), "ask")
                 for cap, combo in self._perm_combos.items()}
        self._msg.setText("Saving…")

        def _done(_result=None):
            self.accept()

        def _fail(message):
            self._msg.setText(f"Setup hit a problem: {message}. You can "
                              "skip and set these in Settings later.")
            self._msg.setStyleSheet(
                f"color: {ERROR_RED}; font-size: 12px;")

        def _run():
            try:
                self._client.save_profile(
                    name=name, call_name=name, about="")
                self._client.save_personality(
                    voice=voice, warmth=70, directness=60, humour=50,
                    notes="")
                for cap, setting in perms.items():
                    self._client.save_permission(cap, setting)
            except BackendError as exc:
                return exc
            return None

        worker = ApiCallWorker(_run)
        run_worker(worker,
                   lambda r: _done() if r is None else _fail(str(r)),
                   _fail, self)


# --- PC-command detection -------------------------------------------------------
_PC_PATTERNS = (
    re.compile(r"\b(open|launch|start|run)\s+(file\s+explorer|explorer|"
               r"notepad|calculator|paint|word|excel|powerpoint|chrome|"
               r"edge|firefox|spotify|vlc|settings|control\s+panel|"
               r"task\s+manager|cmd|command\s+prompt|terminal|powershell|"
               r"the\s+(file|folder|app|application|program))\b", re.I),
    re.compile(r"\b(open|close)\s+(a\s+|the\s+)?(file|folder|app|application|"
               r"program|window)\b", re.I),
    re.compile(r"\b(shutdown|restart|reboot|sleep|hibernate|log\s+off|"
               r"lock\s+(the\s+|my\s+)?(pc|computer|screen))\b", re.I),
    re.compile(r"\b(take\s+a\s+screenshot|screenshot)\b", re.I),
    re.compile(r"\b(volume\s+(up|down)|mute|unmute|brightness\s+(up|down))\b",
               re.I),
    re.compile(r"\b(type|click|press|move\s+the\s+mouse|scroll)\b", re.I),
    re.compile(r"\b(delete|create|rename|move|copy)\s+(a\s+|the\s+)?"
               r"(file|folder)\b", re.I),
    re.compile(r"[A-Za-z]:\\[^\s]+|\.exe\b|\.txt\b", re.I),
)


def looks_like_pc_command(text: str) -> bool:
    """True when a message looks like a PC action (open an app, type,
    click, shutdown, screenshot, ...). Those are routed to the local
    NVIDIA LLM with the PC-control instructions, because the backend
    does not do PC actions. Everything else goes to the backend."""
    lowered = (text or "").lower()
    return any(p.search(lowered) for p in _PC_PATTERNS)


def strip_greeting_prefix(reply: str) -> str:
    """Remove a leading 'Aries active. Ready to cook.' from a reply
    (the greeting is one-time-only on wake-up, never a reply opener).
    Mirrors the backend's deterministic guard."""
    if not reply:
        return reply
    pattern = re.compile(
        r"^\s*[\"'*\u201c\u201d]*\s*aries[\s,]+active[\s.]*"
        r"ready\s+to\s+cook[\s.!]*[\"'*\u201d]*\s*",
        re.IGNORECASE)
    out = reply
    for _ in range(3):
        new = pattern.sub("", out, count=1)
        if new == out:
            break
        out = new
    return out.strip() or "Hey."


# --- Main window ---------------------------------------------------------------
class AriesWindow(QMainWindow):
    """Main Aries window: the web front's look on the desktop.

    Fullscreen like VLC by default (F11 toggles, Esc exits). One
    vertical column, minimal: a small gear top-right, the web-front
    visor face centred and first, the stacked greeting lockup
    (ARIES / active / Ready to cook.), one tiny status line, then the
    chat (cream text straight on the real grain veil, with a Sources
    button under replies that carry sources), and the composer pill
    at the bottom (text input, mic, Send). No Pause-background button,
    no Mini-mode button — pressing the face opens mini mode.

    Chat goes to the Render backend first (live info, memory, tasks,
    ElevenLabs voice). PC-action requests go to the local NVIDIA LLM
    instead (the backend does not do PC actions). When the backend
    cannot be reached, everything falls back to the local key.
    """

    wake_heard = pyqtSignal()
    speak_done = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Aries")
        self.setMinimumSize(800, 600)
        self.resize(1280, 800)
        self._was_fullscreen = False
        self._client = BackendClient(BACKEND_URL)
        self._voice = VoiceEngine(self._client)
        self._settings = QSettings("Aries", "AriesDesktop")
        self._backend_ok = False
        self._state: dict = {}
        self._local_history: list[dict[str, str]] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
        self._thread: QThread | None = None
        self._mini: MiniWindow | None = None
        self._key_ok = True
        self._pc_control_enabled = bool(
            self._settings.value("pc_control_enabled", False, type=bool))
        self._pc_round_used = False
        self._pc_followup_pending = False
        self._listening = False
        self._wake_on = bool(
            self._settings.value("wake_on", False, type=bool))
        self._talk_back = bool(
            self._settings.value("talk_back", True, type=bool))
        self._web_search = bool(
            self._settings.value("web_search", False, type=bool))
        self._grain = self._settings.value("grain", "fine", type=str)
        self._eleven_voice_id = self._settings.value(
            "eleven_voice_id", "", type=str) or None
        self._device_voice_name = self._settings.value(
            "device_voice_name", "", type=str) or None
        if self._device_voice_name:
            self._voice.set_device_voice(self._device_voice_name)
        self._build_ui()
        self.wake_heard.connect(self._on_wake_heard)
        self.speak_done.connect(self._on_speak_done)
        self._load_state()
        self._check_key()
        self._refresh_system_prompt()
        self._apply_wake()
        # Due-task reminders, every 30 s, like the web app.
        self._due_timer = QTimer(self)
        self._due_timer.setInterval(30000)
        self._due_timer.timeout.connect(self._check_due_tasks)
        self._due_timer.start()

    # -- layout ------------------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        # The real veil (baked grain + artwork reveal) fills the window.
        self._bg = VeilWidget(central)
        self._bg.setGeometry(central.rect())
        self._bg.set_grain(self._grain)
        # Overlay: everything the user touches, on top of the veil.
        overlay = QWidget(central)
        overlay.setGeometry(central.rect())
        overlay.setStyleSheet("background: transparent;")
        root = QVBoxLayout(overlay)
        root.setContentsMargins(28, 16, 28, 28)
        root.setSpacing(12)
        # Tiny gear + fullscreen buttons, top-right.
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
            f"border: 1px solid {HAIRLINE}; border-radius: 19px;")
        top.addWidget(self._settings_btn)
        self._fs_btn = QPushButton("⛶", self)
        self._fs_btn.setObjectName("fullscreen_btn")
        self._fs_btn.setToolTip("Fullscreen (F11)")
        self._fs_btn.clicked.connect(self._toggle_fullscreen)
        self._fs_btn.setFixedSize(38, 38)
        self._fs_btn.setStyleSheet(
            f"background: {PANEL_SEMI}; color: {TEXT}; "
            f"font-family: {SANS}; font-size: 15px;"
            f"border: 1px solid {HAIRLINE}; border-radius: 19px;")
        top.addWidget(self._fs_btn)
        root.addLayout(top)
        # ---- The face: the web front's visor, first thing you see -----
        self._core = AriesFaceWidget(280, self)
        self._core.setToolTip("Click the face for mini mode")
        self._core.clicked.connect(self._open_mini)
        root.addWidget(self._core, alignment=Qt.AlignCenter)
        # ---- Greeting lockup, exactly like the web front --------------
        greet_wrap = QVBoxLayout()
        greet_wrap.setSpacing(2)
        greet_wrap.setContentsMargins(0, 0, 0, 0)
        word_aries = QLabel("ARIES", self)
        word_aries.setAlignment(Qt.AlignCenter)
        word_aries.setStyleSheet(
            f"color: {CREAM}; font-family: {SANS_SEMI}; font-size: 56px;"
            "font-weight: bold; letter-spacing: 9px;")
        greet_wrap.addWidget(word_aries)
        word_active = QLabel("active", self)
        word_active.setAlignment(Qt.AlignCenter)
        word_active.setStyleSheet(
            f"color: {CREAM}; font-family: {SERIF}; font-size: 30px;"
            "font-style: italic;")
        greet_wrap.addWidget(word_active)
        word_cook = QLabel("Ready to cook.", self)
        word_cook.setAlignment(Qt.AlignCenter)
        word_cook.setStyleSheet(
            f"color: {CREAM_DIM}; font-family: {SERIF}; font-size: 20px;"
            "font-style: italic;")
        greet_wrap.addWidget(word_cook)
        root.addLayout(greet_wrap)
        # ---- One tiny status line ---------------------------------------
        self._status = QLabel("READY", self)
        self._status.setAlignment(Qt.AlignCenter)
        self._status.setStyleSheet(
            f"color: {MUTED}; font-size: 11px; font-family: {SANS};"
            "letter-spacing: 3px;")
        root.addWidget(self._status)
        # ---- Due-task reminder card (hidden until a task is due) -------
        self._due_card = QWidget(self)
        self._due_card.setStyleSheet(
            f"background: {PANEL_SEMI}; border: 1px solid {ACCENT_SOFT};"
            "border-radius: 12px;")
        due_layout = QHBoxLayout(self._due_card)
        due_layout.setContentsMargins(14, 8, 14, 8)
        self._due_label = QLabel(self._due_card)
        self._due_label.setWordWrap(True)
        due_layout.addWidget(self._due_label, stretch=1)
        due_done = QPushButton("Done", self._due_card)
        due_done.clicked.connect(self._on_due_done)
        due_layout.addWidget(due_done)
        due_dismiss = QPushButton("Dismiss", self._due_card)
        due_dismiss.clicked.connect(self._on_due_dismiss)
        due_layout.addWidget(due_dismiss)
        self._due_card.hide()
        root.addWidget(self._due_card)
        self._due_task: dict | None = None
        # ---- Chat: cream text straight on the veil, with a Sources
        # button under replies that carry sources.
        self._chat_scroll = QScrollArea(self)
        self._chat_scroll.setWidgetResizable(True)
        self._chat_scroll.setStyleSheet(
            "background: transparent; border: none;")
        self._chat = ChatLog(self)
        self._chat.set_scroll(self._chat_scroll)
        self._chat_scroll.setWidget(self._chat)
        root.addWidget(self._chat_scroll, stretch=1)
        # ---- Composer: the web front's dock pill -------------------------
        bottom = QHBoxLayout()
        bottom.setSpacing(10)
        composer = QWidget(self)
        composer.setStyleSheet(
            f"background: {PANEL_SEMI}; border: 1px solid {HAIRLINE};"
            "border-radius: 24px;")
        composer_row = QHBoxLayout(composer)
        composer_row.setContentsMargins(6, 4, 6, 4)
        composer_row.setSpacing(6)
        self._input = QLineEdit(self)
        self._input.setObjectName("input")
        self._input.setPlaceholderText("Type a message…")
        self._input.setMinimumHeight(40)
        self._input.setStyleSheet(
            f"background: transparent; color: {TEXT}; border: none;"
            f"font-family: {SANS}; font-size: 14px; padding: 8px 12px;")
        self._input.returnPressed.connect(self._on_send)
        composer_row.addWidget(self._input, stretch=1)
        self._mic_btn = QPushButton("🎙", self)
        self._mic_btn.setObjectName("mic_btn")
        self._mic_btn.setToolTip("Voice input — tap and talk")
        self._mic_btn.setFixedSize(40, 40)
        self._mic_btn.setStyleSheet(
            f"background: transparent; color: {TEXT}; "
            f"border: 1px solid {HAIRLINE}; border-radius: 20px;"
            f"font-family: {SANS}; font-size: 16px;")
        self._mic_btn.clicked.connect(self._on_mic)
        composer_row.addWidget(self._mic_btn)
        self._send_btn = QPushButton("Send", self)
        self._send_btn.setObjectName("send_btn")
        self._send_btn.setMinimumHeight(40)
        self._send_btn.setStyleSheet(
            f"background: {ACCENT}; color: white; padding: 8px 22px;"
            f"border: none; border-radius: 20px; font-weight: bold;"
            f"font-family: {SANS_SEMI}; font-size: 14px;")
        self._send_btn.clicked.connect(self._on_send)
        composer_row.addWidget(self._send_btn)
        bottom.addWidget(composer, stretch=1)
        self._retry_btn = QPushButton("Check again", self)
        self._retry_btn.setObjectName("retry_btn")
        self._retry_btn.setMinimumHeight(48)
        self._retry_btn.setStyleSheet(
            f"background: transparent; color: {TEXT}; padding: 8px 18px;"
            f"border: 1px solid {ACCENT_SOFT}; border-radius: 24px;"
            f"font-family: {SANS_SEMI}; font-size: 14px;")
        self._retry_btn.clicked.connect(self._load_state)
        self._retry_btn.hide()
        bottom.addWidget(self._retry_btn)
        root.addLayout(bottom)
        self._overlay = overlay
        self.setMouseTracking(True)
        central.setMouseTracking(True)
        overlay.setMouseTracking(True)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        if hasattr(self, "_bg"):
            rect = self.centralWidget().rect()
            self._bg.setGeometry(rect)
            self._overlay.setGeometry(rect)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        # The veil's reveal follows the pointer, like the web.
        if hasattr(self, "_bg"):
            self._bg.set_pointer(float(event.x()), float(event.y()))
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        # A click on the veil background fires a burst (the web's
        # click burst). Clicks on the face, composer, buttons or chat
        # do not — those have their own jobs.
        if event.button() == Qt.LeftButton and hasattr(self, "_bg"):
            widget = self.childAt(event.pos())
            if widget is self._overlay or widget is self._bg \
                    or widget is None:
                self._bg.burst_at(float(event.x()), float(event.y()))
        super().mousePressEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if hasattr(self, "_bg"):
            self._bg.pointer_left()
        super().leaveEvent(event)

    # -- fullscreen (VLC-style) --------------------------------------------
    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()
        self._update_fs_btn()

    def _update_fs_btn(self) -> None:
        if hasattr(self, "_fs_btn"):
            self._fs_btn.setToolTip(
                "Exit fullscreen (Esc)" if self.isFullScreen()
                else "Fullscreen (F11)")

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.key() == Qt.Key_F11:
            self._toggle_fullscreen()
            return
        if event.key() == Qt.Key_Escape and self.isFullScreen():
            self.showNormal()
            self._update_fs_btn()
            return
        super().keyPressEvent(event)

    # -- backend state ---------------------------------------------------------
    def _load_state(self) -> None:
        """Fetch /api/state off the UI thread; fall back to local mode
        when the backend cannot be reached."""
        self._retry_btn.hide()
        worker = StateWorker(self._client)
        run_worker(worker, self._on_state_loaded, self._on_state_failed,
                   self)

    def _on_state_loaded(self, state: dict) -> None:
        self._backend_ok = True
        self._state = state or {}
        self._set_status("Ready")
        # The server is up, so chat works even without a local key —
        # re-check the key now that we know the backend is reachable
        # (the startup key check ran before this load finished).
        self._check_key()
        # Restore the server's chat history into the log (with Sources
        # buttons where a message carries sources).
        self._chat.clear()
        for msg in (self._state.get("messages") or []):
            if msg.get("role") == "user":
                self._chat.add_user(msg.get("content", ""))
            else:
                self._chat.add_aries(
                    msg.get("content", ""),
                    msg.get("sources") or None)
        # Face lock: enrolled + always -> lock screen.
        face = self._state.get("face") or {}
        perms = self._state.get("permissions") or {}
        if face.get("enrolled") and perms.get("face_login") == "always":
            self._show_lock_screen()
            return
        # First run: no profile name and setup not done -> wizard.
        profile = self._state.get("profile") or {}
        if not self._settings.value("aries_setup_done", False, type=bool) \
                and not profile.get("name"):
            self._show_setup_wizard()

    def _on_state_failed(self, message: str) -> None:
        self._backend_ok = False
        self._set_status("Offline — local mode")
        self._chat.add_note(
            "Could not reach the Aries server — chatting with the local "
            "key instead. Live info, memory, tasks and the ElevenLabs "
            "voice need the server; check the internet connection and "
            "press “Check again”.")
        self._retry_btn.show()

    def _show_lock_screen(self) -> None:
        dialog = LockScreen(self._client, self)
        if dialog.exec_() != QDialog.Accepted:
            # Stay locked: keep the window but do not chat.
            self._input.setEnabled(False)
            self._send_btn.setEnabled(False)
            self._mic_btn.setEnabled(False)
            self._chat.add_note("Aries is locked. Unlock it from Settings "
                                "→ More → Face login.")
            return
        self._input.setEnabled(True)
        self._send_btn.setEnabled(True)
        self._mic_btn.setEnabled(True)

    def _show_setup_wizard(self) -> None:
        dialog = SetupWizard(self._client, self)
        if dialog.exec_() == QDialog.Accepted:
            self._settings.setValue("aries_setup_done", True)
            self._load_state()
        else:
            self._settings.setValue("aries_setup_done", True)

    # -- key check (fallback path) ---------------------------------------------
    def _check_key(self) -> None:
        try:
            get_api_key()
        except RuntimeError:
            self._key_ok = False
            if self._backend_ok:
                # The server is up, so chat works without a local key —
                # make sure the input is enabled (the startup check may
                # have disabled it before the state load finished).
                self._input.setEnabled(True)
                self._send_btn.setEnabled(True)
                return
            self._input.setEnabled(False)
            self._send_btn.setEnabled(False)
            if not getattr(self, "_key_note_shown", False):
                self._key_note_shown = True
                self._chat.add_note(
                    "No NVIDIA API key found and the server is "
                    "unreachable, so I can't chat yet. Save a key in "
                    "Settings (gear icon, top right), or check the "
                    "internet connection.")
            return
        self._key_ok = True
        self._input.setEnabled(True)
        self._send_btn.setEnabled(True)

    # -- settings ------------------------------------------------------------
    def _open_settings(self) -> None:
        dialog = SettingsDialog(self._client, self._voice, self)
        dialog.settings_changed.connect(self._on_settings_changed)
        dialog.grain_changed.connect(self._on_grain_changed)
        dialog.burst_requested.connect(self._on_burst_requested)
        dialog.exec_()
        # Refresh server state after Settings closes (it may have
        # changed memories, tasks, permissions, ...).
        if self._backend_ok:
            self._load_state()

    def _on_settings_changed(self) -> None:
        self._pc_control_enabled = bool(
            self._settings.value("pc_control_enabled", False, type=bool))
        self._talk_back = bool(
            self._settings.value("talk_back", True, type=bool))
        self._wake_on = bool(
            self._settings.value("wake_on", False, type=bool))
        self._web_search = bool(
            self._settings.value("web_search", False, type=bool))
        self._eleven_voice_id = self._settings.value(
            "eleven_voice_id", "", type=str) or None
        self._device_voice_name = self._settings.value(
            "device_voice_name", "", type=str) or None
        if self._device_voice_name:
            self._voice.set_device_voice(self._device_voice_name)
        else:
            self._voice.set_device_voice(None)
        self._refresh_system_prompt()
        self._apply_wake()
        self._check_key()

    def _on_grain_changed(self, name: str) -> None:
        self._grain = name
        if hasattr(self, "_bg"):
            self._bg.set_grain(name)

    def _on_burst_requested(self) -> None:
        if hasattr(self, "_bg"):
            self._bg.burst_from_centre()

    def _refresh_system_prompt(self) -> None:
        prompt = SYSTEM_PROMPT
        if self._pc_control_enabled:
            prompt += "\n\n" + pc_control.PC_CONTROL_INSTRUCTIONS
        self._local_history[0] = {"role": "system", "content": prompt}

    def _apply_wake(self) -> None:
        """Start or stop the 'Hey Aries' listener to match the toggle."""
        if self._wake_on:
            try:
                self._voice.start_wake(
                    lambda: self.wake_heard.emit())
            except VoiceError as exc:
                self._wake_on = False
                self._settings.setValue("wake_on", False)
                self._chat.add_note(
                    f"Wake phrase could not start: {exc}")
        else:
            self._voice.stop_wake()

    def _on_wake_heard(self) -> None:
        """'Hey Aries' was heard: greet, then listen for the command."""
        if self._mini is not None:
            # In mini mode the wake phrase starts a voice round.
            self._mini_listen()
            return
        self._chat.add_aries("Aries active. Ready to cook.")
        self._speak("Aries active. Ready to cook.")
        QTimer.singleShot(900, self._on_mic)

    # -- due tasks ---------------------------------------------------------------
    def _check_due_tasks(self) -> None:
        if not self._backend_ok:
            return

        def _ok(tasks):
            if tasks:
                self._due_task = tasks[0]
                self._due_label.setText(
                    f"Reminder: {tasks[0].get('title', '')}")
                self._due_card.show()

        worker = ApiCallWorker(self._client.due_tasks)
        run_worker(worker, _ok, lambda _m: None, self)

    def _on_due_done(self) -> None:
        if not self._due_task:
            return
        task_id = self._due_task["id"]
        self._due_task = None
        self._due_card.hide()
        worker = ApiCallWorker(self._client.mark_reminded, task_id)
        run_worker(worker, lambda _r: None, lambda _m: None, self)

    def _on_due_dismiss(self) -> None:
        if not self._due_task:
            return
        task_id = self._due_task["id"]
        self._due_task = None
        self._due_card.hide()
        worker = ApiCallWorker(self._client.mark_reminded, task_id)
        run_worker(worker, lambda _r: None, lambda _m: None, self)

    # -- chat ----------------------------------------------------------------------
    def _append_user(self, text: str) -> None:
        self._chat.add_user(text)
        self._local_history.append({"role": "user", "content": text})

    def _append_aries(self, text: str,
                      sources: list[dict] | None = None) -> None:
        self._chat.add_aries(text, sources)
        self._local_history.append({"role": "assistant", "content": text})

    def _set_status(self, text: str) -> None:
        clean = text.replace("…", "").strip().upper()
        self._status.setTextFormat(Qt.RichText)
        self._status.setText(
            f'<span style="color:{ACCENT_SOFT}">●</span>  {clean}')

    def _set_core_state(self, state: str) -> None:
        self._core.set_state(state)
        if self._mini is not None:
            self._mini.set_state(state)

    def _set_busy(self, busy: bool) -> None:
        self._input.setEnabled(not busy)
        self._send_btn.setEnabled(not busy)

    def _speak(self, text: str) -> None:
        """Talk back (chunked, prefetched). The face speaks while the
        audio plays and returns to idle when it ends."""
        if not self._talk_back:
            return
        self._set_core_state("speaking")
        self._set_status("Speaking…")

        def _done():
            self.speak_done.emit()

        started = self._voice.speak(
            text, self._eleven_voice_id, on_done=_done)
        if not started:
            # Nothing could speak (no backend voice, no device voice).
            self._set_core_state("idle")
            self._set_status("Ready")

    def _on_speak_done(self) -> None:
        self._set_core_state("idle")
        self._set_status("Ready")

    def _on_mic(self) -> None:
        """Dictate: listen once, then send what was heard as a message."""
        if self._listening:
            return
        ok, why = self._voice.mic_available()
        if not ok:
            self._chat.add_aries(
                f"The mic isn't available right now — {why} Type your "
                "message for now.")
            return
        self._listening = True
        self._set_core_state("listening")
        self._set_status("Listening…")
        self._mic_btn.setText("●")
        worker = ListenWorker(self._voice)
        run_worker(worker, self._on_dictated, self._on_listen_failed, self)

    def _on_dictated(self, text: str) -> None:
        self._listening = False
        self._mic_btn.setText("🎙")
        self._set_core_state("idle")
        self._set_status("Ready")
        if not text:
            self._chat.add_note("Didn't catch that — try again, or type it.")
            return
        self._input.setText(text)
        self._on_send()

    def _on_listen_failed(self, message: str) -> None:
        self._listening = False
        self._mic_btn.setText("🎙")
        self._set_core_state("idle")
        self._set_status("Ready")
        self._chat.add_aries(f"Sorry — {message}")

    def _on_send(self) -> None:
        text = self._input.text().strip()
        if not text:
            return
        self._input.clear()
        self._send_text(text)

    def _send_text(self, text: str) -> None:
        """Send a message. PC-action requests go to the local NVIDIA
        LLM (the backend does not do PC actions); everything else
        goes to the backend, with the local key as the fallback when
        the backend cannot be reached."""
        self._append_user(text)
        self._pc_round_used = False
        self._set_busy(True)
        self._set_core_state("thinking")
        self._set_status("Thinking…")
        if looks_like_pc_command(text):
            self._send_local(text)
            return
        if self._backend_ok:
            self._send_backend(text)
        else:
            self._send_local(text)

    def _send_backend(self, text: str) -> None:
        use_search = self._web_search or looks_current(text)
        worker = BackendChatWorker(
            self._client, text, use_search, {})
        run_worker(worker, self._on_backend_reply, self._on_backend_failed,
                   self)

    def _on_backend_reply(self, result: dict) -> None:
        # The server asks for a permission first (web_search, ...).
        if result.get("needs_permission"):
            cap = result["needs_permission"]
            label = SettingsDialog.PERM_LABELS.get(cap, cap)
            answer = self._ask_permission(
                f"Aries wants to use {label} to answer this.")
            if answer == "deny":
                self._append_aries(
                    "I haven't checked — that needs your permission first.")
                self._finish_reply("")
                return
            if answer == "always":
                worker = ApiCallWorker(
                    self._client.save_permission, cap, "always")
                run_worker(worker, lambda _r: None, lambda _m: None, self)
            # Retry once with this capability consented.
            use_search = self._web_search or looks_current(
                self._local_history[-1]["content"])
            worker = BackendChatWorker(
                self._client, self._local_history[-1]["content"],
                use_search, {cap: True})
            run_worker(worker, self._on_backend_reply,
                       self._on_backend_failed, self)
            return
        reply = result.get("reply", "")
        sources = result.get("sources") or []
        self._append_aries(reply, sources if sources else None)
        if result.get("pending_memory") \
                and not result["pending_memory"].get("saved"):
            self._chat.add_note(
                f"Aries learned: “{result['pending_memory'].get('text', '')}”"
                " — approve it in Memory.")
        if result.get("task_added"):
            self._chat.add_note(
                f"Task kept: {result['task_added'].get('title', '')}")
        self._finish_reply(reply, sources)
        # Refresh server state quietly (memories/tasks may have changed).
        if self._backend_ok:
            self._load_state_quiet()

    def _load_state_quiet(self) -> None:
        worker = StateWorker(self._client)
        run_worker(worker,
                   lambda s: setattr(self, "_state", s or {}),
                   lambda _m: None, self)

    def _on_backend_failed(self, message: str) -> None:
        # The backend dropped out mid-chat: fall back to the local key
        # for this message instead of failing outright.
        self._chat.add_note(
            f"The server didn't answer ({message}) — answering with the "
            "local key instead.")
        self._send_local(self._local_history[-1]["content"])

    def _ask_permission(self, question: str) -> str:
        """Allow once / Allow always / Deny. Returns one of the three."""
        box = QMessageBox(self)
        box.setWindowTitle("Aries is asking permission")
        box.setText(question)
        once = box.addButton("Allow once", QMessageBox.AcceptRole)
        always = box.addButton("Allow always", QMessageBox.AcceptRole)
        deny = box.addButton("Deny", QMessageBox.RejectRole)
        box.setDefaultButton(once)
        box.exec_()
        clicked = box.clickedButton()
        if clicked is always:
            return "always"
        if clicked is deny:
            return "deny"
        return "once"

    def _send_local(self, text: str) -> None:
        """Direct NVIDIA LLM (fallback, and the PC-control route)."""
        if not self._key_ok:
            self._chat.add_aries(
                "I can't answer that locally — no NVIDIA API key is set. "
                "Save one in Settings (gear icon, top right), or check "
                "the internet connection so I can reach the server.")
            self._finish_reply("")
            return
        self._refresh_system_prompt()
        worker = DirectChatWorker(self._local_history)
        run_worker(worker, self._on_local_reply, self._on_error, self)

    def _finish_reply(self, reply: str,
                      sources: list[dict] | None = None) -> None:
        """Talk the reply back (when Talk back is on), then idle."""
        self._set_busy(False)
        if self._talk_back and reply:
            self._speak(reply)
        else:
            self._set_core_state("idle")
            self._set_status("Ready")

    # -- local (direct-LLM) reply: the PC-control flow (0.4.1) --------------
    def _on_local_reply(self, reply: str) -> None:
        reply = strip_greeting_prefix(reply)
        # A follow-up reply after PC actions ran: display it plainly,
        # never treat it as a new plan — and never show raw JSON.
        if self._pc_followup_pending:
            self._pc_followup_pending = False
            display = pc_control.sanitize_display(reply)
            self._local_history.append(
                {"role": "assistant", "content": display})
            if display:
                self._chat.add_aries(display)
            self._finish_reply(display or reply)
            return
        try:
            actions = pc_control.extract_pc_actions(reply)
        except pc_control.PcControlError as exc:
            display = pc_control.sanitize_display(reply)
            self._local_history.append(
                {"role": "assistant", "content": display})
            if display:
                self._chat.add_aries(display)
            self._chat.add_aries(f"Sorry — {exc}")
            self._finish_reply(display or reply)
            return
        display = pc_control.sanitize_display(reply)
        if actions:
            history_text = display or (
                "I will do this on your PC:\n"
                + "\n".join(
                    f"- {pc_control.describe_action(step)}"
                    for step in actions))
        else:
            history_text = display
        self._local_history.append(
            {"role": "assistant", "content": history_text})
        if display:
            self._chat.add_aries(display)
        if not actions:
            self._finish_reply(display or reply)
            return
        if not self._pc_control_enabled:
            self._chat.add_aries(
                "That would need PC control, which is off. Turn on "
                "“Allow PC control” in Settings (gear icon, top right) "
                "and ask again.")
            self._finish_reply(display or reply)
            return
        if self._pc_round_used:
            self._chat.add_aries(
                "I already ran one PC plan for this message, so I "
                "stopped there — ask again if you want more done.")
            self._finish_reply(display or reply)
            return
        self._pc_round_used = True
        self._handle_pc_actions(actions)

    def _handle_pc_actions(self, actions: list[dict]) -> None:
        """Confirm a PC plan, run it, then ask the model to sum up."""
        try:
            pc_control.validate_steps(actions)
        except pc_control.PcControlError as exc:
            self._chat.add_aries(f"Sorry — {exc}")
            self._finish_reply("")
            return
        lines = []
        destructive = False
        for i, step in enumerate(actions, 1):
            if pc_control.is_destructive(step):
                destructive = True
                lines.append(
                    f"{i}. {pc_control.describe_action(step)}"
                    "  ⚠ DESTRUCTIVE")
            else:
                lines.append(f"{i}. {pc_control.describe_action(step)}")
        plan = "\n".join(lines)
        self._set_core_state("thinking")
        self._set_status("Waiting for your OK…")
        answer = QMessageBox.question(
            self,
            "Aries wants to do this on your PC",
            "Aries is asking to do these steps on your PC:\n\n"
            + plan
            + "\n\nNothing runs until you say yes.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No)
        if answer != QMessageBox.Yes:
            self._chat.add_aries(
                "OK — I didn't do anything. Nothing on the PC was changed.")
            self._local_history.append(
                {"role": "user",
                 "content": "No — do not run that plan."})
            self._finish_reply("")
            return
        if destructive:
            answer2 = QMessageBox.warning(
                self,
                "Destructive step — are you sure?",
                "This plan includes a DESTRUCTIVE step (shutdown, "
                "restart, delete, or overwriting a file). It cannot be "
                "undone.\n\n" + plan + "\n\nAre you really sure?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No)
            if answer2 != QMessageBox.Yes:
                self._chat.add_aries(
                    "OK — I didn't do anything. Nothing on the PC was "
                    "changed.")
                self._local_history.append(
                    {"role": "user",
                     "content": "No — do not run that plan."})
                self._finish_reply("")
                return
        self._set_status("Doing it…")
        results = pc_control.execute_steps(actions)
        report_lines = []
        for step, result in zip(actions, results):
            tick = "✓" if result["ok"] else "✗"
            report_lines.append(
                f"{tick} {pc_control.describe_action(step)}"
                f" — {result['summary']}")
        report = "\n".join(report_lines)
        self._chat.add_aries("Done — here's what happened:\n" + report)
        self._local_history.append(
            {"role": "user",
             "content": ("PC action results (tell me what happened in plain "
                         "words, briefly):\n" + report)})
        self._pc_followup_pending = True
        self._set_busy(True)
        self._set_core_state("thinking")
        self._set_status("Thinking…")
        worker = DirectChatWorker(self._local_history)
        run_worker(worker, self._on_local_reply, self._on_error, self)

    def _on_error(self, message: str) -> None:
        self._pc_followup_pending = False
        self._chat.add_aries(f"Sorry — {message}")
        self._set_core_state("error")
        self._set_busy(False)
        self._set_status("Error — try again")
        QTimer.singleShot(1800, lambda: self._set_core_state("idle"))

    # -- mini mode ---------------------------------------------------------------
    def _open_mini(self) -> None:
        if self._mini is not None:
            return
        self._was_fullscreen = self.isFullScreen()
        self._mini = MiniWindow()
        self._mini.set_state(self._core.state())
        self._mini.closed.connect(self._close_mini)
        self._mini.face_clicked.connect(self._mini_listen)
        self._mini.face_double_clicked.connect(self._close_mini)
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

    def _mini_listen(self) -> None:
        """Mini mode is voice-activated only: tap the face, talk, and
        Aries answers out loud. The reply is always spoken here."""
        if self._listening:
            return
        ok, why = self._voice.mic_available()
        if not ok:
            self._set_core_state("error")
            QTimer.singleShot(1800, lambda: self._set_core_state("idle"))
            return
        self._listening = True
        self._set_core_state("listening")
        worker = ListenWorker(self._voice)
        run_worker(worker, self._on_mini_dictated,
                   self._on_mini_listen_failed, self)

    def _on_mini_dictated(self, text: str) -> None:
        self._listening = False
        if not text:
            self._set_core_state("idle")
            return
        # In mini mode the reply is always spoken, even if Talk back
        # is off in Settings.
        old_talk = self._talk_back
        self._talk_back = True
        self._send_text(text)
        self._talk_back = old_talk

    def _on_mini_listen_failed(self, message: str) -> None:
        self._listening = False
        self._set_core_state("error")
        QTimer.singleShot(1800, lambda: self._set_core_state("idle"))

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._voice.stop_wake()
        self._voice.stop_speaking()
        if self._mini is not None:
            self._mini.close()
        super().closeEvent(event)


def run_ui() -> int:
    """Create the app and show the main window. Returns the exit code.

    Fullscreen by default, like VLC (F11 toggles, Esc exits).
    The app font is Segoe UI throughout — headings use
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

