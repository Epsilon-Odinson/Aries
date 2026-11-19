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
"""

__version__ = "0.4.1"
__all__ = ["__version__"]
