# Aries — production webapp (Render-deployable)

Aries as a real product: a SaaS webapp with a personality and a stable
database, not just a CLI wrapper for an NVIDIA model. One service on
Render serves the React frontend and the FastAPI backend together.

## Architecture (one service, on purpose)

```
browser ──► Render web service (Docker, single container)
               ├── FastAPI backend  (/api/*)
               │     ├── NVIDIA chat (Nemotron 3.5 Lightning, server-side key)
               │     ├── memory vault, personality, tasks, permissions
               │     ├── DuckDuckGo web search (no Google scraping)
               │     ├── screenshot description (optional vision model)
               │     └── face login (OpenCV LBPH + PIN fallback)
               ├── SQLite (local) or Render Postgres (DATABASE_URL)
               └── built React frontend (DitherVeil hero, visor face, mini mode)
```

One service instead of two because it removes CORS, removes a second
service to keep awake on the free tier, and keeps the API and the app
on the same origin. If traffic ever outgrows it, split the frontend
into a Render static site and point `VITE_API_URL` at the API service.

## What is in this build

- **Chat** with the NVIDIA model, personality-driven system prompt
  (Companion / Mentor / Hype / Calm + warmth / directness / humour
  sliders + character notes), and keyword memory recall.
- **Memory vault** — teach, pin, edit (via delete + re-add), filter,
  forget; chat-learned facts are held **pending approval**.
- **Web search** (DuckDuckGo only) — explicit globe toggle or a
  question that needs fresh info; replies grounded in results with
  named, linked sources; honest when search fails.
- **Permissions** — Ask / Always / Never per capability, enforced
  **server-side** (`never` hard-blocks; `ask` needs per-request consent).
- **Tasks & reminders** — chat ("remind me to … tomorrow at 9am")
  or the Tasks panel; due tasks surface via `/api/tasks/due`.
- **Voice** — browser dictation (SpeechRecognition), talk-back via
  speech synthesis, "Hey Aries" wake phrase. All client-side; see the
  mic note below.
- **Face login** — OpenCV LBPH enrollment/verification (the spec's
  fallback when dlib will not build) + unlock PIN (SHA-256 hash only).
  A convenience lock, not biometric-grade security.
- **Screenshot description** — upload an image; described by the
  vision model **only if `VISION_MODEL` is set and live**. Otherwise
  it says so honestly and gives image metadata instead of inventing
  a description. (Vision model `meta/llama-3.2-11b-vision-instruct`
  is UNCONFIRMED on NVIDIA hosting as of 2026-11-18.)
- **Setup wizard** — first launch: who you are → voice →
  permission defaults. No API-key step on purpose: the key is
  server-side only.
- **First view** — small floating composer + mic, face first;
  everything else (personality, memory, tasks, permissions, face,
  screenshot, history, installer) lives in Settings / More.
- **Mini mode** — tap the face: floating bottom-right panel with
  face, composer, mic, talk-back and wake toggles.

## What is NOT in this build

- **PC control** — deliberately excluded. No pyautogui, no shell
  execution, no mouse/keyboard control, no file execution on the
  user's machine. That is the LAST roadmap item and lives in the
  desktop PyQt app (`aries/` in the repo root), not in this webapp.
- **Public sign-up / billing** — Aries is private to Epsilon and
  a friend until commercial. There is a single household user
  (`USER = "default"` in `server/app/main.py`). Add accounts before
  opening it up.

## Security rules

- `NVIDIA_API_KEY` lives **only** in the Render dashboard
  (Environment). Never in code, never in the repo, never in any
  installer, never accepted from a client.
- The frontend never sees the key. The backend never logs it.
- Face data (LBPH model) and the SQLite file live under
  `server/data/` (gitignored). On Render, attach the Postgres DB
  from `render.yaml` for durable data — the free web service disk
  is ephemeral.

## Deploy to Render (exact steps)

1. Push this `production/` directory to the repo (see "Push" below).
2. Render dashboard → **New → Blueprint** → connect the repo →
   select it. Render reads `production/render.yaml` and creates the
   `aries` web service + `aries-db` Postgres database.
   (If the blueprint path is not picked up, create the web service
   manually: New → Web Service → Docker, root directory `production`,
   Dockerfile `production/Dockerfile`.)
3. **Before the first real chat:** in the service → **Environment**,
   set `NVIDIA_API_KEY` to your NVIDIA key. (Render marks it
   `sync: false` — it is not stored in the repo.)
4. Deploy. Open the service URL → `/api/health` should return
   `{"ok": true, ...}` with `key_configured: true`.
5. First visit runs the setup wizard. Aries greets:
   "Aries active. Ready to cook."

## Run locally

```bash
# backend
cd production/server
python3 -m venv /tmp/aries-venv && /tmp/aries-venv/bin/pip install -r requirements.txt
cp ../.env.example ../.env   # then put a key in .env (local only, never commit)
 /tmp/aries-venv/bin/uvicorn app.main:app --reload --port 8000

# frontend (second terminal)
cd production/web
npm install
npm run dev        # http://localhost:5173 (proxies /api to :8000)
npm run build      # output: web/dist (served by the backend as server/static)
```

## Tests (run before pushing)

```bash
cd production/server
python3 -m venv /tmp/aries-venv && /tmp/aries-venv/bin/pip install -r requirements.txt
/tmp/aries-venv/bin/uvicorn app.main:app --port 8000 &
curl localhost:8000/api/health
# chat with a DUMMY key: expect a clear 401-style error, never a real key
NVIDIA_API_KEY=dummy-key-123 /tmp/aries-venv/bin/python - <<'EOF'
import requests
r = requests.post("http://localhost:8000/api/chat", json={"message": "hello"})
print(r.status_code, r.json()["reply"][:120])
EOF
cd ../web && npm install && npm run build
```

## Mic note (known limit)

Dictation, talk-back and the wake phrase use the browser's speech
services. In embedded webviews the frame may not delegate the
microphone — the app then says so plainly and keyboard dictation
(tap the message box, then the keyboard's mic key) is the fallback.
Full mic support needs Aries open in its own browser tab.

## File map

```
production/
├── Dockerfile            # multi-stage: Node builds web, Python serves all
├── render.yaml           # Render blueprint: 1 web service + Postgres
├── .env.example          # placeholders only, never real secrets
├── README.md             # this file
├── server/
│   ├── requirements.txt
│   └── app/
│       ├── main.py       # FastAPI app: all /api routes + static serving
│       ├── db.py         # SQLAlchemy models (SQLite ↔ Postgres)
│       ├── llm.py        # NVIDIA client (env key only)
│       ├── personality.py# voice presets + sliders → system prompt
│       ├── search.py     # DuckDuckGo search (ddgs, Lite fallback)
│       ├── permissions.py# server-side permission enforcement
│       └── face.py       # OpenCV LBPH enrollment / verification
└── web/                  # Vite + React 18 (DitherVeil hero base kept)
    ├── src/
    │   ├── App.jsx       # hero, composer, mini mode, Settings/More, wizard
    │   ├── Face.jsx      # machine visor face (light/motion only)
    │   ├── DitherVeil.jsx# React Bits veil (from the showcase build)
    │   └── api.js        # backend client
    └── vite.config.js    # dev proxy /api → :8000
```
