"""Local service for the Aries desktop shell (0.8.0).

The Electron shell renders the SAME React code as the web, bundled
locally. This service is the desktop's own backend: it runs the full
production FastAPI app on 127.0.0.1 (never exposed to the network) —
chat, memory, tasks, personality, permissions, web search, voice
(ElevenLabs), face login and screenshot description — plus the
desktop-only powers a browser cannot do: PC control, microphone
dictation, and the wake phrase. The PC app is self-contained: the
cloud (https://aries-ahvo.onrender.com) is only a fallback the
frontend switches to when this service is down.

The backend code is the production server itself (see
aries.local_backend) with the desktop routes mounted on it (see
aries.desktop_api), so the shell talks to ONE local origin for
everything. Local data lives in ~/.aries/ (aries.db, faces/).

Endpoints: see aries.desktop_api for the desktop-only ones
(/health, /mic/*, /local/*); the /api/* routes are the production
API (auth, chat, memories, tasks, voice, face, screenshot,
connections).

The microphone NEVER opens in this process: capture runs in a
helper child process (aries/mic_worker.py), so a bad audio driver
can only kill the child. The service reports "the mic isn't
available" in plain words and stays up. A global excepthook makes
sure an unexpected error is logged and answered, never a crash.

Run: python -m aries.service [--port 0]
The service prints ARIES_SERVICE_PORT=<port> on stdout once it is
listening, so the Electron shell can find it. When frozen with
PyInstaller (aries-service.exe), the same binary re-invokes itself
for --mic-* flags (see main()).
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request

__version__ = "0.8.0"

# Startup budget: uvicorn is fast, but a cold machine or a slow
# first import can take a few seconds. The shell waits up to 30 s.
_START_TIMEOUT = 30.0


def _pc_enabled() -> bool:
    """PC control master toggle. OFF by default (approved spec)."""
    from aries.desktop_api import _pc_enabled as _impl

    return _impl()


def _set_pc_enabled(enabled: bool) -> None:
    from aries.desktop_api import _set_pc_enabled as _impl

    _impl(enabled)


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


def _wait_ready(port: int, timeout: float = _START_TIMEOUT) -> bool:
    """Poll /health until the service answers (or the budget runs out)."""
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/health"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as res:
                if res.status == 200 and json.loads(res.read().decode()).get("ok"):
                    return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


class _LocalServer:
    """A running local backend.

    Small wrapper so callers (and the old tests) can start, wait on,
    and stop the service without knowing it is uvicorn underneath.
    ``serve()`` only returns once /health answers.
    """

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self._uvicorn = None
        self._thread: threading.Thread | None = None
        self._stopped = threading.Event()

    @property
    def server_address(self) -> tuple[str, int]:
        return (self.host, self.port)

    def serve_forever(self) -> None:
        """Block until shutdown() is called (or the server dies)."""
        while not self._stopped.is_set():
            if self._thread is not None and not self._thread.is_alive():
                break
            self._stopped.wait(0.5)

    def shutdown(self) -> None:
        """Ask uvicorn to stop, then release serve_forever()."""
        if self._uvicorn is not None:
            self._uvicorn.should_exit = True
        self._stopped.set()
        if self._thread is not None:
            self._thread.join(timeout=10)

    def server_close(self) -> None:
        self.shutdown()


def serve(port: int = 0, host: str = "127.0.0.1") -> _LocalServer:
    """Start the local backend. Prints nothing; main() prints the port.

    Returns only once /health answers. Raises RuntimeError when the
    backend does not come up inside the startup budget.
    """
    import uvicorn

    from aries.local_backend import build_app

    app = build_app()
    for _attempt in range(3):
        actual = port or _free_port()
        config = uvicorn.Config(
            app,
            host=host,
            port=actual,
            log_level="warning",
            # Pure-Python loop/http: no uvloop/httptools needed in the
            # frozen bundle, and plenty fast for a single-user backend.
            loop="asyncio",
            http="h11",
            ws="none",
        )
        server = _LocalServer(host, actual)
        uvicorn_server = uvicorn.Server(config)
        server._uvicorn = uvicorn_server
        # uvicorn.Server.run() installs signal handlers, which only
        # work in the main thread — so run serve() directly in a
        # worker thread instead (it installs no handlers).
        thread = threading.Thread(
            target=lambda: asyncio.run(uvicorn_server.serve()),
            name="aries-uvicorn",
            daemon=True,
        )
        server._thread = thread
        thread.start()
        if _wait_ready(actual):
            return server
        # Did not come up (port taken in the gap, or a startup error):
        # stop this attempt and try another port.
        uvicorn_server.should_exit = True
        thread.join(timeout=5)
        if port:
            break
    raise RuntimeError(
        "The Aries local backend did not start. "
        "Check the service output for the error and try again."
    )


def _install_crash_guard() -> None:
    """An unexpected error is logged, never a crash."""

    def _hook(exc_type, exc_value, tb) -> None:
        try:
            traceback.print_exception(exc_type, exc_value, tb)
        except Exception:
            pass

    sys.excepthook = _hook


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    # Frozen sidecar re-invoking itself for mic work: the mic helper
    # must run before anything else, in a child process.
    if args and args[0].startswith("--mic-"):
        from aries import mic_worker

        return mic_worker.main(args)
    _install_crash_guard()
    port = 0
    for i, arg in enumerate(args):
        if arg == "--port" and i + 1 < len(args):
            try:
                port = int(args[i + 1])
            except ValueError:
                pass
    server = serve(port)
    print(f"ARIES_SERVICE_PORT={server.port}", flush=True)
    print(
        f"Aries local service {__version__} listening on "
        f"http://127.0.0.1:{server.port}",
        file=sys.stderr,
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            from aries.desktop_api import STATE

            if STATE.voice is not None:
                STATE.voice.stop_wake()
        except Exception:
            pass
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
