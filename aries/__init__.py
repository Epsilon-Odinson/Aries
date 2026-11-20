"""Aries — Python PC desktop AI companion.

Module 1: project skeleton, NVIDIA LLM client, and CLI entry point.
Module 2: PyQt5 desktop UI — JARVIS-style HUD, chat pane (0.2.0 was
the first UI with code rain and a smiley face; 0.2.1 replaced both
with the JARVIS design at the user's call; 0.2.2 made the core a
living head — breathing, blinking, looking around, following the
cursor — and made mini mode dock bottom-right, drag smoothly, and
stay always-on-top; 0.3.0 replaced the radar-like core with a REAL
robot face — big rounded head, large glowing eyes with pupils,
a mouth that smiles, thinks and moves when it speaks — because
the user said the core did not look like a face at all; 0.3.1
kept the purple and the robot face but restyled the whole UI
with the makoma.io design language at the user's call — restraint,
centred layout, huge whitespace, bold sans + italic serif greeting,
tiny letterspaced state labels, pill controls, calm motion;
0.3.2 (user: "we are getting there") removed the Pause-background
and Mini-mode buttons (mini opens by pressing the face), made the
face a machine visor — no smile, no eyebrows, no human mouth —
launched fullscreen like VLC (F11 toggles, Esc exits), cut the
text down to one greeting line + one tiny status line with the
face as the hero, and switched all fonts to Segoe UI (Georgia
italic for the greeting word only).
Later modules add voice, face-recognition login,
the permissions system, and full mini mode.
0.4.0 adds PC control (the operating agent: open apps/files,
type, click, clipboard, shell, read/write files, system info —
max 5 steps, confirmation before acting, twice for destructive
actions, master toggle in Settings, OFF by default), an API-key
field in Settings (saved via keyring), and the Windows .exe
build (GitHub Actions, tag v*-desktop).
0.4.1 fixes PC control so raw JSON never reaches the chat (the
pc-actions block is parsed tolerantly, executed, and replaced by
a plain-words result), and restyles the desktop to the current
web front — deep purple, the exact visor SVG, the stacked
greeting lockup, and the minimal main view.
0.6.0 fixes the bugs the user reported on 0.5.0 — the mic crash
(the mic is now probed/captured in a helper subprocess, so a
PortAudio fault can never kill the app; a ListenWorker that raises
anything now reports it in plain words), mini mode (a press on
the face expands back to full scale; voice is the wake phrase
only), exact web parity (topline, greeting sizes, latest-reply-
only chat, notice bar, pill tabs, pill toggles, "Settings / More"
header), a 60fps pointer orb on a cached veil, the "© Made by
Epsilon" stamp, and full PC control (25 actions: open URL, window
minimise/maximise/restore/close, volume, media keys, screenshot,
lock PC, open a Settings page — still max 5 steps, confirmation
before acting, twice for destructive actions).
0.7.0 replaces the PyQt desktop with an Electron shell that runs
the SAME React code as the web, bundled locally (never the live
site in a window) — pixel-identical UI by construction — plus a
local Python service (aries/service.py) on 127.0.0.1 for PC control,
microphone and wake phrase. The PyQt UI (aries/ui.py) is legacy
and no longer used by the shell.
"""

__version__ = "0.7.0"
__all__ = ["__version__"]
