"""Local service for the Aries desktop shell (0.7.0).

The Electron shell renders the SAME React code as the web, bundled
locally. This service is the desktop-only half: it runs on
127.0.0.1 (never exposed to the network) and gives the React
frontend the things a browser cannot do — PC control, microphone
dictation, and the wake phrase. Chat, web search, memory, tasks,
voice (ElevenLabs) and face login keep going to the Render backend
directly from the frontend; this service never sees them.

Endpoints (all JSON):
- GET  /health                    {ok, version, pc_enabled}
- GET  /mic/available            probe the mic (child process)
- POST /mic/listen               {"timeout": 12} -> {"text"} / {"heard": false}
- POST /mic/wake/start           start the "Hey Aries" listener
- POST /mic/wake/stop            stop it
- GET  /mic/wake/poll            {"wakes": n} (count since last poll)
- POST /local/chat               {"message"} -> PC flow (local LLM)
- POST /local/pc/describe        {"steps"} -> plain lines + destructive flag
- POST /local/pc/execute         {"steps"} -> run a confirmed plan
- GET  /local/pc/enabled         {"enabled": bool}
- POST /local/pc/enabled         {"enabled": bool} -> master toggle
- GET  /local/key/status         {"has_key": bool, "source": str|null}
- POST /local/key                {"key": "..."} -> save to the OS store

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

import json
import os
import re
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

__version__ = "0.7.0"

# Where the desktop-only settings live (PC-control toggle). The
# installed app dir may be read-only, so this is in the user's home.
_LOCAL_STATE = Path.home() / ".aries" / "local.json"

_GREETING_STRIP = re.compile(
    r"^\s*[\"'*\u201c\u201d]*\s*aries[\s,]+active[\s.]*"
    r"ready\s+to\s+cook[\s.!]*[\"'*\u201d]*\s*",
    re.IGNORECASE,
)


def _strip_greeting(reply: str) -> str:
    """Remove a leading greeting opener (one-time-only on wake-up)."""
    out = reply or ""
    for _ in range(3):
        new = _GREETING_STRIP.sub("", out, count=1)
        if new == out:
            break
        out = new
    return out.strip() or "Hey."


def _load_state() -> dict:
    try:
        return json.loads(_LOCAL_STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        _LOCAL_STATE.parent.mkdir(parents=True, exist_ok=True)
        _LOCAL_STATE.write_text(json.dumps(state), encoding="utf-8")
    except Exception:
        pass


class _State:
    """Process-wide state: voice engine, local chat history, wake count."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.voice = None  # VoiceEngine, created lazily (no audio until needed)
        self.wake_count = 0
        self.wake_on = False
        self.history: list[dict[str, str]] = []

    def get_voice(self):
        if self.voice is None:
            from aries.voice import VoiceEngine

            self.voice = VoiceEngine(None)
        return self.voice

    def on_wake(self) -> None:
        with self.lock:
            self.wake_count += 1

    def take_wakes(self) -> int:
        with self.lock:
            n = self.wake_count
            self.wake_count = 0
            return n


STATE = _State()


def _pc_enabled() -> bool:
    """PC control master toggle. OFF by default (approved spec)."""
    return bool(_load_state().get("pc_enabled", False))


def _set_pc_enabled(enabled: bool) -> None:
    state = _load_state()
    state["pc_enabled"] = bool(enabled)
    _save_state(state)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _health() -> dict:
    return {"ok": True, "version": __version__, "pc_enabled": _pc_enabled()}


def _mic_available() -> dict:
    try:
        voice = STATE.get_voice()
        ok, why = voice.mic_available()
    except Exception as exc:  # never let a probe crash the service
        return {"ok": False, "why": f"Microphone check failed: {exc}"}
    return {"ok": ok, "why": why or ""}


def _mic_listen(body: dict) -> dict:
    try:
        voice = STATE.get_voice()
        text = voice.listen_once(
            timeout=float(body.get("timeout", 12.0)),
            phrase_time_limit=float(body.get("phrase_time_limit", 20.0)),
        )
    except Exception as exc:
        # VoiceError and anything else: plain words, service stays up.
        return {"ok": False, "why": str(exc)}
    if not text:
        return {"ok": True, "heard": False, "text": ""}
    return {"ok": True, "heard": True, "text": text}


def _wake_start() -> dict:
    try:
        voice = STATE.get_voice()
        voice.start_wake(STATE.on_wake)
    except Exception as exc:
        return {"ok": False, "why": str(exc)}
    STATE.wake_on = True
    return {"ok": True}


def _wake_stop() -> dict:
    try:
        if STATE.voice is not None:
            STATE.voice.stop_wake()
    except Exception:
        pass
    STATE.wake_on = False
    return {"ok": True}


def _wake_poll() -> dict:
    return {"ok": True, "wakes": STATE.take_wakes()}


def _local_chat(body: dict) -> dict:
    """PC flow: local NVIDIA LLM with the PC-control instructions.

    Returns {"is_pc": bool, "reply": str, "steps": [...],
    "destructive": bool, "lines": [...]}. When the message is not a
    PC command after all (no pc-actions block), is_pc is False and
    the caller shows the plain reply. When no NVIDIA key is set,
    needs_key is True and the caller points at Settings.
    """
    from aries import pc_control
    from aries.config import get_api_key
    from aries.llm import AriesLLMError, chat as llm_chat
    from aries.main import SYSTEM_PROMPT

    message = str(body.get("message", "")).strip()
    if not message:
        return {"ok": False, "error": "The message is empty."}
    if not _pc_enabled():
        return {
            "ok": True,
            "is_pc": False,
            "needs_pc": True,
            "reply": "PC control is off. Turn it on in Settings first.",
        }
    try:
        get_api_key()
    except RuntimeError:
        return {
            "ok": True,
            "is_pc": False,
            "needs_key": True,
            "reply": (
                "PC actions need the NVIDIA engine key. Add it in "
                "Settings (Desktop → Engine key) and try again."
            ),
        }
    system = SYSTEM_PROMPT + "\n\n" + pc_control.PC_CONTROL_INSTRUCTIONS
    history = list(STATE.history)
    messages = [{"role": "system", "content": system}] + history + [
        {"role": "user", "content": message}
    ]
    try:
        raw = llm_chat(messages, max_tokens=1024)
    except AriesLLMError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": f"Local chat failed: {exc}"}
    try:
        steps = pc_control.extract_pc_actions(raw)
    except pc_control.PcControlError as exc:
        return {"ok": True, "is_pc": True, "reply": str(exc), "steps": [],
                "destructive": False, "lines": []}
    reply = _strip_greeting(pc_control.sanitize_display(raw))
    if not steps:
        STATE.history.append({"role": "user", "content": message})
        STATE.history.append({"role": "assistant", "content": reply})
        STATE.history = STATE.history[-12:]
        return {"ok": True, "is_pc": False, "reply": reply}
    lines = [pc_control.describe_action(s) for s in steps]
    destructive = any(pc_control.is_destructive(s) for s in steps)
    STATE.history.append({"role": "user", "content": message})
    STATE.history.append({"role": "assistant", "content": reply})
    STATE.history = STATE.history[-12:]
    return {
        "ok": True,
        "is_pc": True,
        "reply": reply,
        "steps": steps,
        "destructive": destructive,
        "lines": lines,
    }


def _pc_describe(body: dict) -> dict:
    from aries import pc_control

    steps = body.get("steps")
    try:
        pc_control.validate_steps(steps)
    except pc_control.PcControlError as exc:
        return {"ok": False, "error": str(exc)}
    lines = [pc_control.describe_action(s) for s in steps]
    destructive = any(pc_control.is_destructive(s) for s in steps)
    return {"ok": True, "lines": lines, "destructive": destructive}


def _pc_execute(body: dict) -> dict:
    from aries import pc_control

    if not _pc_enabled():
        return {"ok": False, "error": "PC control is off. Turn it on in Settings first."}
    steps = body.get("steps")
    try:
        results = pc_control.execute_steps(steps)
    except pc_control.PcControlError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": f"PC action failed: {exc}"}
    done = [r["summary"] for r in results if r["ok"]]
    failed = [r["summary"] for r in results if not r["ok"]]
    if failed:
        return {"ok": False, "error": failed[0],
                "done": done, "results": results}
    return {"ok": True, "done": done, "results": results}


def _key_status() -> dict:
    from aries.config import key_source

    source = key_source()
    return {"ok": True, "has_key": source is not None, "source": source}


def _key_save(body: dict) -> dict:
    from aries.config import set_api_key

    key = str(body.get("key", "")).strip()
    if not key:
        return {"ok": False, "error": "The key is empty — paste a key first."}
    try:
        set_api_key(key)
    except (ValueError, RuntimeError) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True}


_ROUTES = {
    ("GET", "/health"): lambda b: _health(),
    ("GET", "/mic/available"): lambda b: _mic_available(),
    ("POST", "/mic/listen"): _mic_listen,
    ("POST", "/mic/wake/start"): lambda b: _wake_start(),
    ("POST", "/mic/wake/stop"): lambda b: _wake_stop(),
    ("GET", "/mic/wake/poll"): lambda b: _wake_poll(),
    ("POST", "/local/chat"): _local_chat,
    ("POST", "/local/pc/describe"): _pc_describe,
    ("POST", "/local/pc/execute"): _pc_execute,
    ("GET", "/local/pc/enabled"): lambda b: {"ok": True, "enabled": _pc_enabled()},
    ("POST", "/local/pc/enabled"): lambda b: (
        _set_pc_enabled(bool(b.get("enabled", False))),
        {"ok": True, "enabled": _pc_enabled()},
    )[1],
    ("GET", "/local/key/status"): lambda b: _key_status(),
    ("POST", "/local/key"): _key_save,
}


class _Handler(BaseHTTPRequestHandler):
    """JSON-only handler. Never crashes on a bad request."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # keep stdout clean for the port line
        pass

    def _send_json(self, data: dict, status: int = 200) -> None:
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(payload)

    def do_OPTIONS(self):  # noqa: N802 - BaseHTTPRequestHandler name
        self._send_json({})

    def _read_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        try:
            raw = self.rfile.read(length)
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _dispatch(self, method: str) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"
        fn = _ROUTES.get((method, path))
        if fn is None:
            self._send_json({"ok": False, "error": f"No such route: {method} {path}"}, 404)
            return
        try:
            body = self._read_body() if method == "POST" else {}
            result = fn(body)
        except Exception as exc:  # a handler must never take the server down
            traceback.print_exc()
            self._send_json({"ok": False, "error": f"Local service error: {exc}"}, 500)
            return
        self._send_json(result if isinstance(result, dict) else {"ok": True, "result": result})

    def do_GET(self):  # noqa: N802
        self._dispatch("GET")

    def do_POST(self):  # noqa: N802
        self._dispatch("POST")


def serve(port: int = 0, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    """Start the service. Prints ARIES_SERVICE_PORT=<port> on stdout."""
    server = ThreadingHTTPServer((host, port), _Handler)
    actual = server.server_address[1]
    print(f"ARIES_SERVICE_PORT={actual}", flush=True)
    print(f"Aries local service 0.7.0 listening on http://{host}:{actual}",
          file=sys.stderr, flush=True)
    return server


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
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            STATE.voice.stop_wake() if STATE.voice is not None else None
        except Exception:
            pass
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
