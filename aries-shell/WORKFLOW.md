# Aries desktop 0.9.0 — release workflow (for the parent agent)

0.9.0 is an Electron shell + Python sidecar. Since 0.8.0 the sidecar
runs the FULL production backend on the PC (chat, memory, tasks,
voice, face, screenshot — the same FastAPI app the website runs),
against a local SQLite database in `%USERPROFILE%\.aries`. 0.9.0
catches the desktop up with everything the website gained since
0.8.0 (viewport fix, memory import, per-user soul, issue reporting,
auto-learn + per-user memory file + always-on search, login info
button, "bro" greeting fix, account-name + brevity rules, and the
Teacher skill with its Study tab) and bundles python-pptx + pypdf
so slide uploads work on the PC. The workflow file in this repo
(`.github/workflows/build-windows.yml`, already updated in the
staging tree) builds it. It cannot be edited by snapshot push
— paste it via the browser (GitHub web UI, edit
`.github/workflows/build-windows.yml`, commit to main), then create
the tag `v0.9.0-desktop` (release) to trigger it.

## What the workflow does

1. Checks out the repo on `windows-latest`, sets up Node 20 and
   Python 3.11.
2. Checks Python syntax for `aries/`, `production/server/app/` and
   `run_aries.py`.
3. Builds the React app (`production/web`) and bundles it into
   `aries-shell/app/` — `bash aries-shell/build-web.sh` (Git Bash is
   on the Windows runner; the script finds `production/web` at the
   repo root).
4. Installs the shell's npm deps (`aries-shell`, `npm ci`).
5. Builds the Python sidecar: `pip install -r requirements.txt`
   (now includes the server deps — FastAPI, uvicorn, SQLAlchemy,
   bcrypt, opencv-contrib for face login, python-pptx + pypdf for
   the Teacher skill's slide uploads) and `pip install
   pyinstaller`, then `pyinstaller aries-shell/sidecar.spec` →
   `dist/aries-service.exe`, copied to `aries-shell/sidecar/aries-service.exe`.
   The spec bundles `production/server/` as `prod_server/` data files
   (plus `production/SOUL.md` and `production/MEMORY.md`) so the
   sidecar imports the production app at runtime.
6. Smoke-tests the built sidecar exe: starts it, reads its port from
   stdout, checks `/health` (local backend, version 0.9.0) and
   `/api/health` (the production API on the PC) both answer, and
   that a study route answers 401 without a token (proving the
   Teacher skill's endpoints are registered, not 404). A broken
   bundle fails the build here, before the installer is built.
7. Runs `electron-builder --win --x64` in `aries-shell`, producing
   the installer (`dist-installer/Aries-Setup.exe`) and the portable
   app (`dist-installer/Aries.exe`).
8. Publishes both to the `v0.9.0-desktop` release (idempotent: if
   the release exists it uploads with `--clobber`).

## Notes for the release

- Tag: `v0.9.0-desktop`, target main at the 0.9.0 commit.
- The workflow needs no new hidden-imports beyond the sidecar
  spec (`aries-shell/sidecar.spec` collects FastAPI, uvicorn,
  SQLAlchemy, bcrypt, pydantic, multipart, anyio, httpx, h11,
  dotenv, Pillow, beautifulsoup4, cv2, pptx and pypdf, and bundles
  the production server as data files).
- The `version_info.txt` (0.9.0) is stamped into aries-service.exe.
- electron-builder needs no code signing for this release
  (unsigned, as before).
- After the release succeeds, update the web download button
  (`production/web/src/App.jsx`, Settings → More → Desktop app &
  installer) from `v0.8.0-desktop` to `v0.9.0-desktop` for both
  links, and verify the live bundle. (The 0.9.0 code deliberately
  leaves the links at v0.8.0-desktop until the release exists —
  pointing at a release that does not exist yet would 404.)
- First run on the PC: the app opens the login screen against the
  LOCAL backend. Epsilon registers as "Epsilon" (admin, by username)
  and adds the NVIDIA engine key in Settings → More → Desktop
  (saved to the Windows Credential Manager). Kelfy registers her own
  account on the same PC. Switching the engine to Cloud in Settings
  → More → Desktop uses the website's backend instead (separate
  cloud account, separate login).
