# Aries

Aries is a Python PC desktop AI companion. JARVIS-style HUD in deep
purple, a real animated robot face, voice and face-recognition
login, a permissions system for sensitive actions, and a mini mode —
built one module at a time.

**Greeting:** "Aries active. Ready to cook."

## What works so far (Module 1)

- Project skeleton
- NVIDIA LLM client (`nvidia/nemotron-3.5-lightning-30b-a3b`) via
  `https://integrate.api.nvidia.com/v1/chat/completions`
- CLI chat loop (`python -m aries.main --cli`)

## What works so far (Module 2) — desktop UI (JARVIS redesign, 0.2.1)

- PyQt5 main window, JARVIS-style HUD in deep purple
  (`#0d0010` / `#7b2fff`, a touch of cyan `#35e0ff` on the core
  only), greeting "Aries active. Ready to cook." on launch
- HUD background instead of code rain: very dark purple-black,
  faint grid, slow rotating glowing rings, drifting particles,
  soft radial glow behind the core (~15 fps, **Pause background**
  button to save CPU / battery). The old green code rain is gone —
  the user said the background sucked, and he was right.
- A real robot face instead of a smiley face or a radar-like core
  (drawn with QPainter, no image files): a big rounded robot head
  with glowing purple edges, an antenna, two LARGE glowing cyan
  eyes with pupils, and a mouth that moves. States: idle
  (relaxed eyes, small smile), thinking / listening (eyes narrow
  and look up, mouth a small "o"), speaking (mouth opens and
  closes with the reply, plus equaliser bars), error (eyes dim,
  mouth flat). The face is big and central — 320 px in a 400 px
  left panel — not a small circle tucked in a sidebar.
- HUD layout: face + real status readouts on the left (current
  time, model name, key set / not set, status), chat as a clean
  HUD panel with corner brackets on the right. No fake CPU / RAM
  numbers — every readout is real. The left panel is filled by
  the face now, so there is far less empty black space.
- Chat pane wired to the NVIDIA LLM, with conversation history.
  LLM calls run in a worker thread, so the UI never freezes;
  401 / 429 / 410 errors show in-chat in plain words, not as a crash
- Startup key check: if `NVIDIA_API_KEY` is missing, the UI shows
  the setup steps instead of crashing, with a **Check key again**
  button (no need to restart)
- **Mini mode** button: switches to a small always-on-top window
  with the SAME robot face (not a smiley face, not a radar core),
  the greeting, and a status line; the mini face mirrors the main
  face's states (idle / thinking / speaking). **Back to full Aries**
  restores the main window (stub — full mini mode is a later module)

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

That opens the desktop UI. You will see the JARVIS HUD, the big
robot face, and `Aries active. Ready to cook.` in the chat pane.
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
  config.py      API key loading (env, then keyring), never prints the key
  llm.py         NVIDIA chat client, clear errors on 401 / 429 / 410
  main.py        Entry point: UI by default, --cli for the text loop
  ui.py          PyQt5 window: JARVIS HUD background, animated
                 robot face, chat pane, worker thread, mini-mode stub
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
