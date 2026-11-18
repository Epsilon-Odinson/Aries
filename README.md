# Aries

Aries is a Python PC desktop AI companion. JARVIS-style HUD in deep
purple, a glowing arc-reactor core, voice and face-recognition
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
- Glowing arc-reactor core instead of a smiley face (drawn with
  QPainter, no image files): concentric rotating arcs, pulsing
  core. States: idle (slow pulse, slow rings), thinking /
  listening (faster spin, brighter core, expanding ripple),
  speaking (waveform bars pulse around the core)
- HUD layout: core + real status readouts on the left (current
  time, model name, key set / not set, status), chat as a clean
  HUD panel with corner brackets on the right. No fake CPU / RAM
  numbers — every readout is real.
- Chat pane wired to the NVIDIA LLM, with conversation history.
  LLM calls run in a worker thread, so the UI never freezes;
  401 / 429 / 410 errors show in-chat in plain words, not as a crash
- Startup key check: if `NVIDIA_API_KEY` is missing, the UI shows
  the setup steps instead of crashing, with a **Check key again**
  button (no need to restart)
- **Mini mode** button: switches to a small always-on-top window
  with the SAME JARVIS core (not a smiley face), the greeting, and
  a status line; the mini core mirrors the main core's states
  (idle / thinking / speaking). **Back to full Aries** restores
  the main window (stub — full mini mode is a later module)

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

That opens the desktop UI. You will see the JARVIS HUD, the glowing
core, and `Aries active. Ready to cook.` in the chat pane.
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
  ui.py          PyQt5 window: JARVIS HUD background, arc-reactor
                 core, chat pane, worker thread, mini-mode stub
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
