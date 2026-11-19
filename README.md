# Aries

Aries is a Python PC desktop AI companion. The web front's look
on the desktop — deep purple, the exact visor face, the stacked
greeting lockup, a minimal main view with only the composer —
voice and face-recognition login, a permissions system for
sensitive actions, and a mini mode — built one module at a time.

**Greeting:** "Aries active. Ready to cook."

## What works so far (Module 1)

- Project skeleton
- NVIDIA LLM client (`nvidia/nemotron-3.5-lightning-30b-a3b`) via
  `https://integrate.api.nvidia.com/v1/chat/completions`
- CLI chat loop (`python -m aries.main --cli`)

## What works so far (Module 2) — desktop UI (web-front look, 0.4.1)

- PyQt5 main window in the web front's deep purple
  (`#0d0010` / `#7b2fff` / `#f6ecd9` cream), greeting lockup
  "ARIES / active / Ready to cook." on launch
- Veil background instead of code rain: deep purple with a
  vignette and a soft purple glow behind the face (~16 fps,
  no pause control — the animation just runs). The old green
  code rain is gone — the user said the background sucked,
  and he was right. (The web front's baked grain texture and
  artwork image cannot ship in the desktop build — the
  snapshot push cannot carry binary image files — so the
  desktop uses flat deep purple instead.)
- The exact visor face from the web front (drawn with
  QPainter, no image files): an angular hexagonal shell, one
  eye band, a chin light strip, faint circuitry and a scanning
  light — no smile, no eyebrows, no human mouth, because the
  robot face looked too humany. The face is big and central —
  280 px in fullscreen, the first thing you see.
- Minimal main view: the face, the greeting lockup, one tiny
  status line, then chat as cream text straight on the veil
  (no panel, no bubble), and the composer pill at the bottom
  (text input, mic, Send) with a small round gear for
  Settings. No fake CPU / RAM numbers — every readout is real.
  (0.3.2 cut the old HUD down: the face is now centred and
  first, with one greeting line and one tiny status line, and
  chat under it — 0.4.1 finished the job to match the web front.)
- Chat pane wired to the NVIDIA LLM, with conversation history.
  LLM calls run in a worker thread, so the UI never freezes;
  401 / 429 / 410 errors show in-chat in plain words, not as a crash
- Startup key check: if `NVIDIA_API_KEY` is missing, the UI shows
  the setup steps instead of crashing, with a **Check key again**
  button (no need to restart)
- **Mini mode**: press the face to switch to a small
  always-on-top window with the SAME machine face (0.3.2 —
  the Mini-mode button is gone, the face click replaces it),
  the greeting, and a status line; the mini face mirrors the
  main face's states (idle / thinking / speaking). **Back to
  full Aries** restores the main window (stub — full mini mode
  is a later module)

## What changed in 0.2.2 — lively core, smooth mini mode

- The core is now a living head, in both the main window and mini
  mode: it breathes (gentle swell and bob, always), blinks (a quick
  dimming pulse every few seconds), and looks around on its own
  (its gaze drifts to random points, faster while thinking).
  When the mouse is near, the core looks toward the cursor and
  brightens when hovered — like it noticed you. While speaking it
  shows waveform bars and bobs slightly. All of it is capped at
  ~16 fps, so it stays light on a ThinkPad T14.
- Mini mode now **docks itself bottom-right** (16 px from the
  right and bottom edges, above the taskbar) when it opens, and
  it stays draggable from there.
- Mini mode **drags smoothly, no stutter**: dragging is handled
  directly (press anywhere, even on the core, and the window
  follows the mouse offset), and while dragging the core drops
  to a cheap paint path. The old version had no drag handling,
  so a drag fell back to the OS moving a window that repainted
  heavily on every move.
- Mini mode stays **always-on-top and active** (it raises and
  takes focus when shown).
- Note: no specific figure/image was supplied with this request,
  so the core is the lively JARVIS core described above. If a
  specific figure is sent later, the face can be reworked to
  match it.

## What changed in 0.3.0 — a real robot face

- The user watched the 0.2.2 UI on video and said the core/face
  is the problem: it was a small circle (~150–250 px) tucked in
  a narrow left sidebar, looking like a radar / loading spinner
  — concentric arcs and a dot, not a face at all — with a huge
  empty black chat panel and empty space under the left panel.
  Chat itself worked fine, so the LLM, model and key handling
  were not touched.
- The core is now a **real animated robot face** (`AriesFaceWidget`,
  `HudCoreWidget` kept as an alias): a big rounded robot head with
  glowing purple edges, an antenna with a glowing tip, side ear
  plates, two LARGE glowing cyan eyes with pupils, eyebrows, and
  a mouth that smiles when idle, makes a small "o" when thinking,
  and opens/closes when speaking (with equaliser bars). Eyes dim
  and the mouth goes flat on error.
- The face is **big and central**: 320 px in a 400 px left panel
  (window 1040×700), with the ARIES title, greeting and status
  readouts around it, so the left side is filled and the chat
  panel no longer feels like a void.
- It stays a living head: breathing (gentle swell and bob), blink
  every few seconds, idle look-around, pupils following the mouse
  cursor (eased, clamped, never snapping), hover brightens it,
  faster look-around while thinking. Same face in mini mode
  (180 px), which keeps its 0.2.2 behaviour: auto-dock
  bottom-right, smooth dragging, always-on-top and active.

## What changed in 0.3.1 — makoma.io styling, purple kept

- The user chose "Keep my purple, add makoma's layout and style"
  after pointing at https://makoma.io/ ("it looks cool asf").
  Makoma's language, applied here: restraint — one object, one
  idea per screen, huge whitespace, almost no chrome; centred
  layout with generous padding (28 px margins, 24 px gaps);
  bold geometric sans paired with an italic serif in cream for
  the emotional word — "Aries active." in bold sans, "Ready to
  cook." in italic serif; tiny uppercase letterspaced labels
  for every state (READY / THINKING / LISTENING / SPEAKING,
  TIME / MODEL / KEY); pill controls — fully rounded input,
  Send and other buttons with thin 1px hairline borders and
  soft 18 px panel corners, no heavy shadows, no corner
  brackets; calm motion — background rings/particles slowed
  and softened, glow/drift/fade only, nothing bounces or snaps.
- Deliberately NOT changed: the purple palette (background
  #0d0010, accent #7b2fff, cream-white text) and the v0.3.0
  robot face — big rounded head, large glowing eyes, moving
  mouth, lively breathing/blink/look-around/cursor gaze.
  The only gold is one tiny rationed dot next to the status
  label; purple stays dominant.
- Same behaviour as 0.2.2/0.3.0: worker thread, key check,
  401/429/410 in-chat errors, Pause background, mini mode
  (same robot face, auto-dock bottom-right, smooth drag,
  always-on-top) — mini mode got the same makoma treatment:
  centred, more padding, the sans+serif greeting, pill button.

## What changed in 0.3.2 — machine face, fullscreen, face-first

- The user tested 0.3.1 and said "nice, we are getting there",
  with a list of fixes — all of them are in this version:
- **No Pause-background button, no Mini-mode button.** The
  background animation just runs (calm, ~15 fps). **Pressing the
  face opens mini mode** — click the face in the main window and
  it docks bottom-right; "Back to full Aries" restores it.
- **The face is a machine now, not a human.** The 0.3.0 robot
  face looked too humany, so the smile, the eyebrows and the
  human mouth are gone. The new face (`AriesFaceWidget`) is an
  angular machine head with a single glowing visor: a sharp
  hexagonal visor bar holding two slanted eye-slits whose bright
  cores shift with the gaze. Expression comes from light and
  motion only — visor width/brightness, a visor dimming pulse
  instead of a blink, waveform bars under the visor when
  speaking, a top vent and a status LED. It still breathes,
  looks around on its own, follows the cursor, and brightens
  on hover.
- **Fullscreen like VLC.** The main window launches fullscreen,
  covering the whole screen including the taskbar. **F11**
  toggles fullscreen, **Esc** exits it, and there is a tiny
  "Fullscreen (F11)" button top-right. Mini mode never goes
  fullscreen — it stays a small always-on-top window.
- **The big text section is gone.** No TIME / MODEL / KEY
  readout block, no ARIES title block, no CONVERSATION header.
  The screen is now: the face — big (360 px), centred, the
  first thing you see — one short greeting line ("Aries
  active." bold sans + "Ready to cook." italic serif cream),
  one tiny status line (READY / THINKING / …), then chat.
  Sleek, like a clean chat app.
- **Fonts fixed.** The font sucked too, per the user — so the
  whole UI is now **Segoe UI** ("Segoe UI Semibold" for
  headings), 13–15 px with generous line spacing, Georgia
  italic for the serif greeting word only. No default Qt
  font, no monospace for main text anywhere — title, greeting,
  chat, input, buttons and status all use Segoe UI, falling
  back to Arial / sans-serif if Segoe UI is missing.

## What changed in 0.4.1 — PC control never shows raw JSON, web-front look

- **PC control replies are sanitised** (`aries/pc_control.py`,
  `aries/main.py`, `aries/ui.py`): the `pc-actions` JSON block
  is now found anywhere in the reply (case/whitespace tolerant),
  parsed, executed, and **replaced in the chat by a plain-words
  result** ("Opened File Explorer", "Done") — the user never
  sees raw JSON, a JSON fence, or field names like `appname`.
  A malformed block or an unknown action gives a clean one-line
  error, never the raw text. The system prompt now tells the
  model to output ONLY the `pc-actions` block (no extra prose)
  when doing a PC action. The first PC action still asks for
  permission; later ones run and show the result.
- **Web-front look** (`aries/ui.py`): the desktop now matches
  the current web front — deep purple (`#0d0010` background,
  `#7b2fff` purple, `#f6ecd9` cream), the **exact visor SVG**
  from the web front (angular hexagonal shell, one eye band,
  chin light strip, faint circuitry, scanning light — no human
  features), the stacked greeting lockup (**ARIES / active /
  Ready to cook.**), one tiny status line with a purple dot, and
  a minimal main view: only the composer (text input, mic,
  Send) and a small round gear for Settings. The face still
  breathes, blinks, looks around, follows the cursor, and its
  eye band pulses and scans like the web front. The mic button
  says plainly that voice input is not in the desktop build yet
  (it works on the website). What PyQt5 cannot do: the web
  front's baked grain texture and its exact artwork image (the
  snapshot push cannot ship binary image files), and the web
  front's smooth WebGL veil — the desktop uses a flat deep
  purple with a vignette and a soft purple glow behind the face
  instead. Everything else — chat, mini mode, Settings,
  fullscreen — works as before.

## What changed in 0.4.0 — PC control, API-key Settings, Windows .exe

- **PC control** (`aries/pc_control.py`, desktop only): ask Aries
  to do things on the PC — open apps by name, open files/folders,
  type text, move/click/scroll the mouse, copy/read the clipboard,
  run shell commands, read/write files, check system info
  (CPU, memory, disk, battery). The defined command set from the
  fixed spec, nothing else. **Max 5 steps per command.** Aries
  plans the steps, the app shows the plan in plain words and
  asks before anything runs; **destructive actions (shutdown,
  restart, delete, overwriting a file) ask a second time, with
  a warning.** The master toggle is in Settings ("Allow PC
  control") and is **OFF by default**. The webapp never gets this.
- **API key in Settings** (gear icon, top right): paste the
  NVIDIA API key and press "Save key" — it goes to the OS
  credential store (Windows Credential Manager) via keyring, the
  same entry the app already reads. The .exe then works
  standalone, no environment variable needed. The key is never
  shown back, printed, or logged. If `NVIDIA_API_KEY` is set as
  an environment variable, that one still wins (Settings says so).
- **Windows .exe** (`.github/workflows/build-windows.yml`):
  push a tag like `v0.4.0-desktop` (or run it by hand from the
  Actions tab) and GitHub Actions builds a single-file
  `Aries.exe` with PyInstaller and publishes it as a GitHub
  Release asset. Download it from the Releases page and run it
  — no install needed.

## Download the Windows .exe

Every `v*-desktop` tag publishes a Release with `Aries.exe`
attached: open the repo's **Releases** page and download the
latest one. Run it — no install needed. On first run, open
Settings (gear icon, top right) and paste your NVIDIA API key.

Still to come: voice (Module 3), face-recognition login (Module 4),
permissions system (Module 5), full mini mode (Module 6).

## Setup on Windows

1. Install Python 3.11 or newer from python.org. Tick
   "Add python.exe to PATH" during install.
2. Clone this repo, then open a terminal in the project folder.
3. Create and activate a virtual environment:
   ```bat
   python -m venv .venv
   .venv\Scripts\activate
   ```
4. Install the dependencies:
   ```bat
   pip install -r requirements.txt
   ```

## Set your NVIDIA API key

The key is **never** stored in code, in a document, or in this repo.
Aries reads it from the `NVIDIA_API_KEY` environment variable first,
then from the OS credential store (keyring) if available.

To set it as a Windows user environment variable:

1. Get a key at https://build.nvidia.com (sign in, Settings → API Keys,
   Generate API Key). Copy it — you may not see it again.
2. Press the Windows key, search for
   **"Edit environment variables for your account"**, and open it.
3. Under **User variables**, click **New**.
   - Variable name: `NVIDIA_API_KEY`
   - Variable value: paste your key.
4. Click OK, then close and reopen your terminal / VS Code so the
   new variable is picked up.

Check it without ever showing the key, in PowerShell:

```powershell
if ($env:NVIDIA_API_KEY) { "Key is set" } else { "Not set" }
```

If a key is ever pasted into a chat, a document, or GitHub, treat it
as exposed: delete / revoke it at build.nvidia.com and generate a
fresh one.

## How to run

From the project folder, with the virtual environment activated:

```bat
python -m aries.main
```

That opens the desktop UI, fullscreen by default. You will see
the big machine face first, then `Aries active. Ready to cook.`
in one line, then the chat. Press **F11** to leave fullscreen,
**Esc** does the same; click the face to open mini mode.
Type in the box at the bottom and press Send (or Enter).

For the old text-only chat loop instead:

```bat
python -m aries.main --cli
```

Type `quit` to exit the CLI loop.

If the key is not set, the UI does not crash — it shows the setup
steps in the chat pane. Set the key (see above), then press
**Check key again** in the UI.

## Project layout

```
aries/
  __init__.py    Package marker, version
  config.py      API key loading (env, then keyring) and saving,
                 never prints the key
  llm.py         NVIDIA chat client, clear errors on 401 / 429 / 410
  main.py        Entry point: UI by default, --cli for the text loop
  pc_control.py  PC control (desktop only): defined command set,
                 max 5 steps, confirmation before acting
  ui.py          PyQt5 window: JARVIS HUD background, animated
                 robot face, chat pane, worker thread, mini-mode stub,
                 Settings dialog (API key + PC control toggle)
run_aries.py     Entry point for the PyInstaller .exe build
.github/workflows/build-windows.yml   Windows .exe build + Release
requirements.txt
README.md
.gitignore       Secrets, virtual envs, face/voice data, build output
```

## Model note — Llama 3.1 70B is retired

The original spec named `meta/llama-3.1-70b-instruct`, but
live-tested 2026-11-18 the NVIDIA hosted API returns **410 Gone** for
it — "has reached its end of life on 2026-08-26T09:00:00Z and is no
longer available." NVIDIA's own lifecycle page lists it as End of
Support (Model Replaced), July 2026. So `CHAT_MODEL` was switched
2026-11-18 to `nvidia/nemotron-3.5-lightning-30b-a3b`, which is on
NVIDIA's free tier (verified 2026-11-16). If NVIDIA retires that
model too, `llm.py` surfaces a 410 with advice to pick a still-hosted
model at build.nvidia.com and update `CHAT_MODEL` in `aries/config.py`.

## Design rules (from the fixed spec)

- API key via environment / keyring only — never in code or docs.
- Screenshots are described with the vision model
  `meta/llama-3.2-11b-vision-instruct`, never the text-only 70B model.
- Web search is DuckDuckGo only (no Google scraping).
- Autonomy is scoped to a defined command set, max 5 steps, with
  confirmation before any destructive action.
