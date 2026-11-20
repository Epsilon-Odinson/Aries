"""Full local backend for the Aries desktop shell (0.8.0).

The PC app is its own thing now: it runs the SAME FastAPI backend as
the website, on this PC, against a local SQLite database — so chat,
memory, tasks, personality, permissions, web search, voice (ElevenLabs),
face login and screenshot description all work without the live website.
The cloud (https://aries-ahvo.onrender.com) is only a fallback the
frontend switches to when this service is down.

How it works:
- The production server code lives in ``production/server/`` (the repo)
  and is bundled into the frozen sidecar as ``prod_server/`` data files
  (see aries-shell/sidecar.spec). At runtime that directory is put on
  ``sys.path`` and ``app.main:app`` is imported — the very same app
  Render runs, so the desktop and the web can never drift apart.
- Local data lives in ``~/.aries/`` (``aries.db``, ``faces/``), never
  in the install dir (which may be read-only) and never in the cloud.
- Keys come from the environment first, then the OS credential store
  (the same keyring entries Settings writes to) — never from code.
- The desktop-only routes (/health, /mic/*, /local/*) are mounted on
  the same app (see aries.desktop_api), so the shell talks to ONE
  local origin for everything.

Nothing here touches the live Render service or its environment.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

__version__ = "0.8.0"

# The app reads these at import/call time, so they must be set BEFORE
# ``app.main`` is imported.
_ENV_READY = False


def aries_home() -> Path:
    """Where this PC's Aries data lives (DB, faces, local settings)."""
    return Path.home() / ".aries"


def find_prod_server() -> Path:
    """Locate the production server directory (the one with app/main.py).

    Tried in order: ARIES_PROD_SERVER (explicit override, used by
    tests and dev), the frozen bundle (PyInstaller _MEIPASS), the repo
    layout (aries/ next to production/), and the local dev staging
    (aries-desktop-staging next to aries-prod-staging).
    """
    override = os.getenv("ARIES_PROD_SERVER", "").strip()
    if override:
        cand = Path(override)
        if (cand / "app" / "main.py").is_file():
            return cand
    here = Path(__file__).resolve()
    candidates = []
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidates.append(Path(sys._MEIPASS) / "prod_server")
    candidates.append(here.parent.parent / "production" / "server")
    candidates.append(here.parent.parent.parent / "aries-prod-staging" / "production" / "server")
    for cand in candidates:
        if (cand / "app" / "main.py").is_file():
            return cand
    raise RuntimeError(
        "Could not find the production server code (production/server). "
        "Set ARIES_PROD_SERVER to its location."
    )


def prepare_environment(server_dir: Path) -> None:
    """Set the local backend's environment. Idempotent; existing env wins.

    - DATABASE_URL: local SQLite at ~/.aries/aries.db
    - FACES_DIR: ~/.aries/faces (face models, per account)
    - CORS_ORIGINS: * — the shell loads the app from a local file, so
      its fetch origin is "null"; the cloud's localhost-only list
      would block it. This service only listens on 127.0.0.1.
    - NVIDIA_API_KEY / ELEVENLABS_API_KEY: from the OS credential
      store when not already in the environment. The key values are
      never printed or logged.
    """
    global _ENV_READY
    if _ENV_READY:
        return
    home = aries_home()
    # SQLite cannot create the file when the folder is missing, so
    # make the home first (harmless when it already exists).
    home.mkdir(parents=True, exist_ok=True)
    (home / "faces").mkdir(parents=True, exist_ok=True)
    db_path = home / "aries.db"
    os.environ.setdefault("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    os.environ.setdefault("FACES_DIR", str(home / "faces"))
    os.environ.setdefault("CORS_ORIGINS", "*")
    # This process IS the local backend. The shared backend code
    # (llm.py, voice.py) reads this flag so key errors point at this
    # PC's Settings instead of the Render dashboard. Cloud behaviour
    # is unchanged when the flag is unset.
    os.environ["ARIES_LOCAL_BACKEND"] = "1"
    if not os.getenv("NVIDIA_API_KEY"):
        try:
            from aries.config import get_api_key

            os.environ["NVIDIA_API_KEY"] = get_api_key()
        except Exception:
            pass
    if not os.getenv("ELEVENLABS_API_KEY"):
        try:
            from aries.config import get_elevenlabs_key

            os.environ["ELEVENLABS_API_KEY"] = get_elevenlabs_key()
        except Exception:
            pass
    _ENV_READY = True


def load_cloud_app():
    """Import the production FastAPI app (after preparing the env)."""
    server_dir = find_prod_server()
    prepare_environment(server_dir)
    s = str(server_dir)
    if s not in sys.path:
        sys.path.insert(0, s)
    from app.main import app  # noqa: PLC0415 - path set up just above

    return app


def build_app():
    """The full local backend: production app + desktop-only routes."""
    from fastapi.responses import JSONResponse

    app = load_cloud_app()
    from aries.desktop_api import router as desktop_router

    app.include_router(desktop_router)

    @app.exception_handler(404)
    async def _not_found(request, exc):  # noqa: ANN001, ANN202
        return JSONResponse(
            {"ok": False, "error": f"No such route: {request.method} {request.url.path}"},
            status_code=404,
        )

    return app
