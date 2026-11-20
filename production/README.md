# Aries — production build

Private SaaS companion for Epsilon and Kelfy until it goes commercial.
FastAPI backend + Vite React frontend, one Docker service on Render,
deployed from this directory. Live site: https://aries-ahvo.onrender.com

## Run it

```bash
# Backend (needs NVIDIA_API_KEY in the environment for chat)
cd server
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000

# Frontend (dev)
cd web
npm install
npm run build   # the backend serves web/dist in production
```

Render deploys from `render.yaml` / `Dockerfile` in this directory.
The NVIDIA key lives server-side only (Render env var
`NVIDIA_API_KEY`) — never in code, never in the repo, never in chat.

## Accounts (2026-11-20)

Everyone signs in with their own account and gets only their own
chats, memories, tasks, profile, personality, permissions, face
enrollment and connections. Kelfy must not use Epsilon's account.

- **Tables:** `users` (username, bcrypt password hash, role,
  created_at) and `sessions` (random token, user, 30-day expiry).
  Passwords are bcrypt hashes only — never plaintext, never logged.
- **Endpoints:** `POST /api/auth/register`, `POST /api/auth/login`,
  `POST /api/auth/logout`, `GET /api/auth/me`. The frontend keeps the
  session token in `localStorage` and sends it as
  `Authorization: Bearer <token>` on every API call.
- **Enforcement:** every data endpoint (chat, memories, tasks,
  personality, permissions, face, screenshot, connections, TTS,
  voices) requires a live session and is scoped to the signed-in
  account's canonical username. No token → `401`. The OAuth callback
  is the one exception — the provider redirects the browser with no
  token, so the user is recovered from the OAuth state row created
  when the connect flow started. The Connect button passes the token
  as `?token=` on that same-origin URL only; it is never sent to
  Google or Spotify.

### Who is admin — by username, NOT by signup order

Epsilon corrected this on 2026-11-20: the "first account created
becomes admin" idea is **rejected and not built**. The rule is:

- Admin status belongs to the account holding the admin username:
  env `ARIES_ADMIN_USERNAME`, default **`epsilon`**, compared
  case-insensitively. Whoever holds that username is admin on **every
  login** — first, last, or middle. If Kelfy signs up first she is a
  normal user; when Epsilon signs up later as "Epsilon", he is admin.
- The admin username is unique — only one account can hold it
  (usernames are unique case-insensitively), so nobody else can take
  it. The admin account cannot be deleted from the admin panel.
- Admin endpoints: `GET /api/admin/users` (list accounts),
  `DELETE /api/admin/users/{username}` (deletes the account **and**
  all of its data). Both return `403` for non-admins. The Admin panel
  in Settings → More is visible only to the admin account.
- The stored `role` on the user row is re-derived from the username
  at every login, so changing `ARIES_ADMIN_USERNAME` takes effect
  without a data migration.

### Migration from the pre-accounts build

Before accounts, every row belonged to `user_key = "default"` — that
was Epsilon's data. The first time the **admin** account registers or
logs in, those rows are claimed for the admin username
(`auth.claim_default_data`, idempotent — later logins move zero rows).
The face-model folder `data/faces/default/` is renamed to the admin's
folder at the same time, so an enrolled face keeps working. A
non-admin registering first claims nothing: the data waits for
Epsilon's account.

**First-signup order after deploy:** Epsilon signs up first as
`Epsilon` (becomes admin, keeps his existing memories/tasks), then
Kelfy signs up as `Kelfy` (fresh, empty account).

## Mini mode (fixed 2026-11-20)

Bug: after switching to mini mode, the app could not be restored to
full screen. Root cause, found by reading the layout (no MiniPanel
or Expand button existed): in mini mode the greeting was only faded
out (`opacity: 0`) — it still occupied ~170px of the flex column —
and the latest reply stayed in the column too. With a long reply the
column grew taller than the viewport, the face was pushed off the top
of the screen, and `body { overflow: hidden }` meant no scrolling
could bring it back. In the desktop shell the same tap was also
swallowed because the face was a `-webkit-app-region: drag` region.

Fix:
- In mini mode the greeting, latest reply, hint and due-card are
  removed from the layout (`display: none`), so the column is only
  the face and always fits on screen.
- A visible **⤢ Full screen** button appears in mini mode, next to
  the face tap, as a second way back.
- Desktop shell: the face is `-webkit-app-region: no-drag` (the stage
  around it stays the drag area), so the tap restores the window.
- `touch-action: manipulation` on the face button.

The veil pointer-reveal/burst behaviour is untouched.

## Voice (fixed 2026-11-20)

Bugs found by auditing every voice path in `web/src/App.jsx`:

1. **Talk-back went silent after `cancel()`.** `speakBrowser`
   called `speechSynthesis.cancel()` then `speak()` immediately —
   Chrome drops a `speak()` that follows `cancel()` too closely,
   with no error. Now the utterance is spoken ~90ms after the
   cancel, guarded by a generation counter so a newer reply wins.
2. **Autoplay blocks were silent.** ElevenLabs `Audio.play()` can
   reject with `NotAllowedError` before the user has interacted with
   the page; the old code returned "didn't speak" and the browser
   fallback failed the same way, quietly. Now that case reports a
   plain notice ("the browser blocks sound until you tap something")
   instead of falling back.
3. **Wake-phrase errors were swallowed.** `rec.onerror = () => {}`
   hid `not-allowed` (mic blocked) and the `onend` handler restarted
   the listener in a silent loop. Fatal errors now turn the toggle
   off and say why; routine errors (`no-speech`, `aborted`) still
   restart. A 5-second guard stops one "Hey Aries" greeting twice.
4. **Dictation collided with the wake listener.** Starting a second
   `SpeechRecognition` while the wake one is live throws and both go
   silent. Dictation now pauses the wake listener and resumes it
   once when dictation ends (only if the wake phrase is still on).
   Tapping the mic while already listening is ignored instead of
   throwing.
5. **Utterance errors were silent.** `SpeechSynthesisUtterance`
   errors are now surfaced as a visible notice.

**Voice self-test** (Settings → Aries → "Run voice test") checks,
one by one: speech-recognition availability, microphone permission
state, a live microphone open test (`getUserMedia`, stream stopped
immediately), speech-synthesis voices on the device, and ElevenLabs
status — each reported plainly (✓ / ✗ / • with words).

Note: this is about the standalone site in its own tab, where the
mic can work. The embedded Muse preview frame cannot reach the
microphone — nothing here sends the user to browser settings for
that.

## Design (standing decisions)

- Deep-purple theme, baked-grain veil, exact machine visor face.
  Main view is only chat + mic + gear; everything else is in
  Settings (3 tabs: Aries, Memory, More).
- Greeting "Aries active. Ready to cook." is one-time-only on
  wake-up; the backend strips it from replies deterministically.
- Permissions enforced server-side (Allow once / always / Deny;
  "Never" hard-blocks; chat-learned memories wait for approval).
- No PC control in the webapp — that is desktop-only, the last
  roadmap item. The desktop shell (Electron 0.7.0, separate tree)
  bundles this same React app locally with a local Python sidecar.
- `production/MEMORY.md` is a redacted placeholder ("User memory is
  stored in the private vault"), so it is safe to include in prompts.
  Real memories live in the Postgres vault, per account.
