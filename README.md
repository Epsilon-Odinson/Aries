# Aries

Aries is a Python PC desktop AI companion. The web front's look
on the desktop — deep purple, the exact visor face, the stacked
greeting lockup, a minimal main view with only the composer —
voice chat, face-recognition login, a permissions system for
sensitive actions, and a mini mode — built one module at a time.

**Greeting:** "Aries active. Ready to cook."

**0.8.0: the desktop is now self-contained — the local service runs
the FULL backend on the PC** (the same FastAPI app the website runs:
chat, web search, memory, tasks, ElevenLabs voice, face login,
screenshot description), against a local SQLite database in
`~/.aries`, so the app works without the live website. The cloud is
only a fallback when the local service is down or has no engine key.
Accounts work locally too, with the same rule: Epsilon (username
"Epsilon") is admin by username, and Kelfy gets her own login on
that PC with her own chats, memories and tasks. The shell
(`aries-shell/`, Electron) renders the SAME React code as the web,
bundled locally into the installed app, never the live site in a
window — pixel-identical to the web by construction: same veil (real
WebGL), same pointer orb, same fonts, same Settings, same Sources
button. A local Python service (`aries/service.py`, on 127.0.0.1)
also gives the frontend the desktop-only powers: full PC control
(25 actions, max 5 steps, confirmation before acting, twice for
destructive), the microphone (capture in a helper subprocess, so a
bad audio driver can never close the app), and the "Hey Aries" wake
phrase. The PyQt UI (`aries/ui.py`) is legacy and no longer used by
the shell.

**0.7.0: the desktop is now an Electron shell around the SAME React
code as the web** — bundled locally into the installed app, never
the live site in a window. The shell (`aries-shell/`, Electron)
renders the React app from local files, so the desktop is
pixel-identical to the web by construction: same veil (real WebGL),
same pointer orb, same fonts, same Settings, same Sources button.
A local Python service (`aries/service.py`, on 127.0.0.1) gives the
frontend the desktop-only powers: full PC control (25 actions, max
5 steps, confirmation before acting, twice for destructive), the
microphone (capture in a helper subprocess, so a bad audio driver
can never close the app), and the "Hey Aries" wake phrase. Chat,
web search, memory, tasks, ElevenLabs voice and face login go to
the Render backend directly, exactly like the web. The PyQt UI
(`aries/ui.py`) is legacy and no longer used by the shell.

**0.5.0: the desktop is now a client of the same Render backend
the web app uses** — so it has ALL the web app's abilities:
voice chat, all four Settings tabs, live info, the web front's
real baked-grain background and artwork, and a Sources button
under replies that carry sources. The NVIDIA key (keyring) stays
as a fallback when the server cannot be reached.

## What works so far (Module 1)

- Project skeleton
- NVIDIA LLM client (`nvidia/nemotron-3.5-lightning-30b-a3b`) via
  `https://integrate.api.nvidia.com/v1/chat/completions`
- CLI chat loop (`python -m aries.main --cli`)

## What works so far (Module 2) — desktop UI (web-front look, 0.4.1)

- PyQt5 main window in the web front's deep purple
  (`#0d0010` / `#7b2fff` / `#f6ecd9` cream), greeting lockup
  "ARIES / active / Ready to cook." on launch
- Veil background instead of code rain: the web front's real
  baked-grain background and artwork, decoded from the base64
  text in `aries/veil_images.py` (the same images as the web
  front's `veilImages.js` — grain cover-fit as the base, the
  artwork revealed through a radial mask with a purple rim,
  a slow idle drift, and a 750 ms burst on tap). Deep purple
  `#0d0010` sits underneath so any uncovered area still matches.
  The old green code rain is gone — the user said the background
  sucked, and he was right.
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
  front's smooth WebGL veil — the desktop paints the same grain
  and artwork with QPainter (a slow lissajous drift, not the
  web's exact shader) instead. Everything else — chat, mini mode,
  Settings, fullscreen — works as before.

## What changed in 0.5.0 — full web parity (backend client)

The desktop is now a client of the SAME Render backend the web
app uses (`https://aries-ahvo.onrender.com`, `aries/api_client.py`).
No API key paste is needed on first run — the engine key stays
server-side only. The direct NVIDIA key (keyring) stays as a
fallback when the server cannot be reached, and PC-action requests
are routed to the local LLM (the backend does not do PC actions).

- **Voice chat** (`aries/voice.py`): mic dictation via
  SpeechRecognition + Google Speech Recognition (free, no key),
  and talk-back via the backend `/api/tts` (ElevenLabs, turbo
  model). The reply is spoken **sentence by sentence**: the first
  chunk plays as soon as it is ready while the next chunk is
  prefetched, so the voice starts fast and keeps flowing — the
  whole reply is never synthesised before anything plays. If the
  server voice is down, the device voice (pyttsx3 / Windows SAPI)
  takes over, also chunk by chunk. The **Talk back** toggle is
  honoured, and the **Wake phrase** toggle listens for "Hey Aries"
  in the background.
- **Settings parity** — all four web gear tabs, nothing dropped:
  **Aries** (profile, personality preset + warmth/directness/humour
  sliders + notes, ElevenLabs voice picker with Hear/Use/default,
  device voices with Hear/Use/default, Talk back / Wake phrase /
  Search the web toggles, Desktop section with the NVIDIA key
  field + PC control toggle), **Memory** (vault list/search/add/
  delete + chat history), **More** (veil grain + burst, tasks,
  permissions, face login, screenshot, installer), **Connections**
  (Gmail/Spotify/WhatsApp status + Connect/Disconnect — Connect
  opens the system browser to the OAuth URL, never a fake login).
- **Live info**: chat sends `web_search=true` when the question
  needs live/current info (mirroring the web toggle; on by default
  for current questions). Replies that carry sources show the full
  answer text first, then a **"Sources (n)"** pill button under it
  that expands/collapses the source links.
- **The real veil**: the web front's exact baked grain and artwork
  (`aries/veil_images.py`, base64 text copied from
  `veilImages.js` — ~2.8 MB of text, snapshot-pushable), painted
  by `VeilWidget`: grain cover-fit, artwork through a radial mask
  with a purple rim, slow idle drift, 750 ms click burst.
- **Mini mode, voice-activated only**: a frameless, transparent,
  borderless window showing ONLY the visor face, floating
  bottom-right with a clear gap above the taskbar (never touching
  it), no title bar, no border, no text box. Tap the face (or say
  "Hey Aries") to start voice listening; the reply is spoken.
  Double-click the face to expand back to full mode.
- **Permission prompts**: when the server asks for a permission
  first (web search, ...), the app asks Allow once / Allow always /
  Deny, like the web.
- **First-run setup wizard** (name, voice, permission defaults —
  never an API key) and a **face lock screen** (face or PIN) when
  face login is on. Due-task reminders pop up as a card.
- PC control stays exactly as in 0.4.1 (tolerant parser, plain-
  words results, never raw JSON), and the desktop keeps the web-
  front look, fullscreen/resizable, screenshot, installer and
  face login.

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
latest one. Run it — no install needed. On first run the setup
wizard asks for your name and voice — no API key is needed, the
engine key stays server-side. (A key in Settings is only the
fallback for when the server cannot be reached.)

Still to come: more of the free skills (study, CODM, Spotify,
money trackers, routine trackers, Bible verse, French practice —
build order still to be decided).

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

## Set your NVIDIA API key (fallback only)

Since 0.5.0 the desktop talks to the Render backend first, so
**no API key is needed on first run** — the engine key stays
server-side only. The key below is only the **fallback** for when
the server cannot be reached, and for PC-action requests (which
the backend does not do). If you want that fallback, Aries reads
the key from the `NVIDIA_API_KEY` environment variable first,
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

If the key is not set and the server cannot be reached, the UI
does not crash — it shows the setup steps in the chat pane. Set
the key (see above), then press **Check key again** in the UI.

## Project layout

```
aries/
  __init__.py    Package marker, version (0.5.0)
  api_client.py  Render backend client: chat, web search, TTS,
                 state, memories, tasks, permissions, connections,
                 voices, face login, screenshot
  config.py      API key loading (env, then keyring) and saving,
                 never prints the key
  llm.py         NVIDIA chat client, clear errors on 401 / 429 / 410
  main.py        Entry point: UI by default, --cli for the text loop
  pc_control.py  PC control (desktop only): defined command set,
                 max 5 steps, confirmation before acting
  ui.py          PyQt5 window: real veil background (baked grain +
                 artwork), exact visor face, chat with Sources
                 buttons, worker threads, voice-only mini mode,
                 Settings dialog (Aries / Memory / More / Connections),
                 lock screen, setup wizard
  veil_images.py The web front's grain + artwork as base64 text
  voice.py       Voice chat: SpeechRecognition dictation, wake
                 phrase, chunked + prefetched ElevenLabs talk-back
                 (pygame), device-voice fallback (pyttsx3)
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
