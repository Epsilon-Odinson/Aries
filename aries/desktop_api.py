"""Desktop-only routes for the Aries local backend (0.8.0).

The Electron shell's React app talks to ONE local origin
(http://127.0.0.1:<port>) for everything: the full FastAPI backend
(chat, memory, tasks, voice, face, screenshot — see
``aries.local_backend``) plus the routes only a PC can serve, which
live here as a FastAPI router:

- GET  /health                    merged health (shell + app status)
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
- GET  /local/elevenlabs/key/status
- POST /local/elevenlabs/key     {"key": "..."} -> save to the OS store

Every handler returns plain JSON with an "ok" flag and never raises
into the server: a failure is {"ok": False, "error": "<plain words>"}.
The microphone NEVER opens in this process: capture runs in a helper
child process (aries/mic_worker.py), so a bad audio driver can only
kill the child. Blocking work (mic probe, LLM calls) runs in a
thread via ``asyncio.to_thread`` so one slow request can never stall
the event loop for the rest of the app.

Keys saved here are also put into this process's environment, so the
running backend picks them up at once — no restart needed after
adding a key in Settings.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
from pathlib import Path

from fastapi import APIRouter, Request

__version__ = "0.8.0"

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


def _display_home(path: Path) -> str:
    """Show a home-dir path with ~, so the UI never prints a username path."""
    try:
        return "~/" + path.relative_to(Path.home()).as_posix()
    except Exception:
        return path.as_posix()


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
# Handlers (sync; the router runs them in a thread)
# ---------------------------------------------------------------------------


def _health() -> dict:
    from aries.config import elevenlabs_key_source, key_source

    db_path = Path.home() / ".aries" / "aries.db"
    return {
        "ok": True,
        "version": __version__,
        "backend": "local",
        "pc_enabled": _pc_enabled(),
        "key_configured": key_source() is not None,
        "elevenlabs_configured": elevenlabs_key_source() is not None,
        "db": "sqlite",
        "db_path": _display_home(db_path),
    }


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
    # The running backend reads the key from the environment at call
    # time, so put it there too — no restart needed after saving.
    os.environ["NVIDIA_API_KEY"] = key
    return {"ok": True}


def _elevenlabs_key_status() -> dict:
    from aries.config import elevenlabs_key_source

    source = elevenlabs_key_source()
    return {"ok": True, "has_key": source is not None, "source": source}


def _elevenlabs_key_save(body: dict) -> dict:
    from aries.config import set_elevenlabs_key

    key = str(body.get("key", "")).strip()
    if not key:
        return {"ok": False, "error": "The key is empty — paste a key first."}
    try:
        set_elevenlabs_key(key)
    except (ValueError, RuntimeError) as exc:
        return {"ok": False, "error": str(exc)}
    os.environ["ELEVENLABS_API_KEY"] = key
    return {"ok": True}


# ---------------------------------------------------------------------------
# Router — one wrapper per route so a handler can never take the server
# down or answer with a traceback. Blocking handlers run in a thread.
# ---------------------------------------------------------------------------

router = APIRouter()


async def _read_body(request: Request) -> dict:
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _route(method: str, path: str, fn, need_body: bool = False):
    async def endpoint(request: Request):
        try:
            body = await _read_body(request) if need_body else {}
            return await asyncio.to_thread(fn, body)
        except Exception as exc:  # a handler must never take the server down
            return {"ok": False, "error": f"Local service error: {exc}"}

    router.add_api_route(path, endpoint, methods=[method])


_route("GET", "/health", lambda b: _health())
_route("GET", "/mic/available", lambda b: _mic_available())
_route("POST", "/mic/listen", _mic_listen, need_body=True)
_route("POST", "/mic/wake/start", lambda b: _wake_start())
_route("POST", "/mic/wake/stop", lambda b: _wake_stop())
_route("GET", "/mic/wake/poll", lambda b: _wake_poll())
_route("POST", "/local/chat", _local_chat, need_body=True)
_route("POST", "/local/pc/describe", _pc_describe, need_body=True)
_route("POST", "/local/pc/execute", _pc_execute, need_body=True)
_route("GET", "/local/pc/enabled", lambda b: {"ok": True, "enabled": _pc_enabled()})
_route("POST", "/local/pc/enabled", lambda b: (_set_pc_enabled(bool(b.get("enabled", False))), {"ok": True, "enabled": _pc_enabled()})[1], need_body=True)
_route("GET", "/local/key/status", lambda b: _key_status())
_route("POST", "/local/key", _key_save, need_body=True)
_route("GET", "/local/elevenlabs/key/status", lambda b: _elevenlabs_key_status())
_route("POST", "/local/elevenlabs/key", _elevenlabs_key_save, need_body=True)
