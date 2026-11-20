"""Client for the Aries Render backend — the SAME backend the web app uses.

The desktop talks to https://aries-ahvo.onrender.com instead of
reimplementing the whole backend locally. That is how it gets full
parity with the web app: chat (with web search + live info), ElevenLabs
talk-back, the memory vault, tasks, permissions, connections, face
login and screenshots all live on the server. The NVIDIA key stays
server-side; the desktop never needs one for the primary path.

The direct NVIDIA key (aries/config.py + aries/llm.py) is kept only
as a fallback for when the backend cannot be reached at all.

Render's free tier cold-starts: the first request after idle can take
50+ seconds, so the timeouts here are generous and GETs retry once.
"""

from __future__ import annotations

import base64
import os

import requests

DEFAULT_BASE_URL = "https://aries-ahvo.onrender.com"

# Render free tier: first request after idle can take 50+ s.
CHAT_TIMEOUT = 120
STATE_TIMEOUT = 90
DEFAULT_TIMEOUT = 60
UPLOAD_TIMEOUT = 120


class BackendError(RuntimeError):
    """A clean, user-facing error from the backend call."""


class BackendUnreachable(BackendError):
    """The backend could not be reached at all (no network / down)."""


class VoiceNotConfigured(BackendError):
    """The server has no ElevenLabs key (HTTP 503 from /api/tts)."""


# The backend's own current-question heuristic (search.py), mirrored
# here so the desktop can ask for live info before it sends a message.
# The server re-checks anyway, so this only decides the toggle.
_CURRENT_TRIGGERS = (
    "today", "yesterday", "this week", "latest", "news", "price",
    "how much", "weather", "score", "release", "when is", "when does",
    "current", "now", "2026", "2027",
)


def looks_current(text: str) -> bool:
    """True when a message looks like it needs fresh / live info."""
    lowered = (text or "").lower()
    return any(t in lowered for t in _CURRENT_TRIGGERS)


class BackendClient:
    """Thin requests wrapper over the Aries backend API.

    Every method returns the parsed JSON (or raw bytes for /api/tts).
    Network failures raise BackendUnreachable; HTTP errors raise
    BackendError with the server's message. Nothing here ever raises
    a raw requests exception to the UI.
    """

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or os.getenv(
            "ARIES_BACKEND_URL", DEFAULT_BASE_URL)).rstrip("/")
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})

    # -- low level --------------------------------------------------------
    def _request(self, method: str, path: str, timeout: int = DEFAULT_TIMEOUT,
                 retry: bool = False, **kwargs):
        url = f"{self.base_url}{path}"
        try:
            resp = self._session.request(
                method, url, timeout=timeout, **kwargs)
        except requests.exceptions.Timeout as exc:
            raise BackendUnreachable(
                "The Aries server did not answer in time. It may be "
                "waking up (first request after idle takes a while) — "
                "try again in a moment."
            ) from exc
        except requests.exceptions.ConnectionError as exc:
            if retry:
                try:
                    resp = self._session.request(
                        method, url, timeout=timeout, **kwargs)
                except requests.exceptions.RequestException as exc2:
                    raise BackendUnreachable(
                        "Could not reach the Aries server. Check your "
                        "internet connection and try again."
                    ) from exc2
            else:
                raise BackendUnreachable(
                    "Could not reach the Aries server. Check your "
                    "internet connection and try again."
                ) from exc
        except requests.exceptions.RequestException as exc:
            raise BackendUnreachable(
                f"Network error talking to the Aries server: {exc}"
            ) from exc
        return resp

    def _json(self, method: str, path: str, timeout: int = DEFAULT_TIMEOUT,
              retry: bool = False, **kwargs):
        resp = self._request(method, path, timeout=timeout, retry=retry,
                             **kwargs)
        try:
            data = resp.json()
        except ValueError:
            data = {"detail": resp.text[:300]}
        if not resp.ok:
            raise BackendError(
                data.get("detail") or f"Server error {resp.status_code}")
        return data

    # -- health / state ----------------------------------------------------
    def health(self) -> dict:
        return self._json("GET", "/api/health", retry=True)

    def is_reachable(self) -> bool:
        """True when the backend answers /api/health."""
        try:
            self.health()
            return True
        except BackendError:
            return False

    def state(self) -> dict:
        return self._json("GET", "/api/state", timeout=STATE_TIMEOUT,
                          retry=True)

    # -- chat ---------------------------------------------------------------
    def chat(self, message: str, use_search: bool = False,
             consent: dict | None = None) -> dict:
        """POST /api/chat. Returns {reply, sources, searched,
        pending_memory, task_added, needs_permission}."""
        return self._json(
            "POST", "/api/chat", timeout=CHAT_TIMEOUT,
            json={"message": message, "use_search": use_search,
                  "consent": consent or {}})

    def clear_chat(self, consent: bool = False) -> dict:
        return self._json("POST", "/api/chat/clear",
                          json={"consent": consent})

    # -- profile / personality ----------------------------------------------
    def save_profile(self, name: str = "", call_name: str = "",
                     about: str = "") -> dict:
        return self._json("POST", "/api/profile",
                          json={"name": name, "call_name": call_name,
                                "about": about})

    def save_personality(self, voice: str = "companion", warmth: int = 70,
                         directness: int = 60, humour: int = 50,
                         notes: str = "") -> dict:
        return self._json("POST", "/api/personality",
                          json={"voice": voice, "warmth": warmth,
                                "directness": directness, "humour": humour,
                                "notes": notes})

    # -- memories -------------------------------------------------------------
    def add_memory(self, text: str, category: str = "general") -> dict:
        return self._json("POST", "/api/memories",
                          json={"text": text, "category": category})

    def approve_memory(self, memory_id: int) -> dict:
        return self._json("POST", f"/api/memories/{memory_id}/approve")

    def pin_memory(self, memory_id: int) -> dict:
        return self._json("POST", f"/api/memories/{memory_id}/pin")

    def delete_memory(self, memory_id: int) -> dict:
        return self._json("DELETE", f"/api/memories/{memory_id}")

    # -- tasks -----------------------------------------------------------------
    def add_task(self, title: str, notes: str = "",
                 due_at: str | None = None) -> dict:
        return self._json("POST", "/api/tasks",
                          json={"title": title, "notes": notes,
                                "due_at": due_at})

    def toggle_task(self, task_id: int) -> dict:
        return self._json("POST", f"/api/tasks/{task_id}/done")

    def delete_task(self, task_id: int) -> dict:
        return self._json("DELETE", f"/api/tasks/{task_id}")

    def mark_reminded(self, task_id: int) -> dict:
        return self._json("POST", f"/api/tasks/{task_id}/reminded")

    def due_tasks(self) -> list:
        return self._json("GET", "/api/tasks/due")

    # -- permissions ------------------------------------------------------------
    def permissions(self) -> dict:
        return self._json("GET", "/api/permissions", retry=True)

    def save_permission(self, capability: str, setting: str) -> dict:
        return self._json("POST", "/api/permissions",
                          json={"capability": capability, "setting": setting})

    # -- voice (ElevenLabs) ------------------------------------------------------
    def voices(self) -> dict:
        """GET /api/voices. 503 when the server has no voice key."""
        try:
            return self._json("GET", "/api/voices", retry=True)
        except BackendError as exc:
            if "503" in str(exc) or "not configured" in str(exc).lower():
                raise VoiceNotConfigured(
                    "The ElevenLabs voice is not set up on the server yet."
                ) from exc
            raise

    def tts(self, text: str, voice_id: str | None = None) -> bytes:
        """POST /api/tts. Returns MP3 bytes. Raises VoiceNotConfigured
        (503) when the server has no voice key."""
        resp = self._request(
            "POST", "/api/tts", timeout=DEFAULT_TIMEOUT,
            json={"text": text, "voice_id": voice_id})
        if resp.status_code == 503:
            raise VoiceNotConfigured(
                "The ElevenLabs voice is not set up on the server yet.")
        if not resp.ok:
            try:
                detail = resp.json().get("detail", "")
            except ValueError:
                detail = resp.text[:200]
            raise BackendError(detail or f"Voice failed ({resp.status_code})")
        return resp.content

    # -- connections ---------------------------------------------------------------
    def connections(self) -> dict:
        """GET /api/connections. Returns {google, spotify, whatsapp} rows
        with name / connected / account / configured (+ note for WhatsApp)."""
        return self._json("GET", "/api/connections", retry=True)

    def connect_url(self, service: str) -> str:
        """The OAuth start URL for a service. GET with redirects off so
        we get the provider's sign-in URL from the Location header."""
        resp = self._request("GET", f"/api/connections/{service}/connect",
                             timeout=DEFAULT_TIMEOUT, allow_redirects=False)
        if resp.status_code in (301, 302, 303, 307, 308):
            return resp.headers.get("Location", "")
        if not resp.ok:
            try:
                detail = resp.json().get("detail", "")
            except ValueError:
                detail = resp.text[:200]
            raise BackendError(detail or f"Connect failed ({resp.status_code})")
        return resp.url

    def disconnect(self, service: str) -> dict:
        return self._json("POST", f"/api/connections/{service}/disconnect")

    # -- face login ------------------------------------------------------------------
    def enroll_face(self, image_bytes: bytes, filename: str = "face.jpg",
                    name: str = "", pin: str = "") -> dict:
        return self._upload("/api/face/enroll", image_bytes, filename,
                            {"name": name, "pin": pin})

    def verify_face(self, image_bytes: bytes,
                    filename: str = "face.jpg") -> dict:
        return self._upload("/api/face/verify", image_bytes, filename, {})

    def remove_face(self) -> dict:
        return self._json("POST", "/api/face/remove")

    def set_pin(self, pin: str) -> dict:
        return self._json("POST", "/api/face/set-pin", json={"pin": pin})

    def unlock_pin(self, pin: str) -> dict:
        return self._json("POST", "/api/face/unlock-pin", json={"pin": pin})

    # -- screenshot ---------------------------------------------------------------------
    def describe_screenshot(self, image_bytes: bytes, filename: str = "shot.png",
                            question: str = "", consent: bool = True) -> dict:
        return self._upload("/api/screenshot", image_bytes, filename,
                            {"question": question,
                             "consent": "true" if consent else "false"})

    # -- multipart helper ------------------------------------------------------------------
    def _upload(self, path: str, image_bytes: bytes, filename: str,
                fields: dict) -> dict:
        url = f"{self.base_url}{path}"
        files = {"file": (filename, image_bytes)}
        try:
            resp = self._session.post(url, files=files, data=fields,
                                      timeout=UPLOAD_TIMEOUT)
        except requests.exceptions.Timeout as exc:
            raise BackendUnreachable(
                "The upload did not finish in time — try again."
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise BackendUnreachable(
                "Could not reach the Aries server. Check your internet "
                "connection and try again."
            ) from exc
        try:
            data = resp.json()
        except ValueError:
            data = {"detail": resp.text[:300]}
        if not resp.ok:
            raise BackendError(
                data.get("detail") or f"Upload failed ({resp.status_code})")
        return data


def decode_image_b64(b64: str) -> bytes:
    """Decode a base64 image string (with or without a data: URI prefix)."""
    if "," in b64 and b64.startswith("data:"):
        b64 = b64.split(",", 1)[1]
    return base64.b64decode(b64)
