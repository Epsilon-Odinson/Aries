"""ElevenLabs text-to-speech for Aries talk-back.

The key lives ONLY in the ELEVENLABS_API_KEY env var on the server
(Render dashboard). It is never accepted from, or sent to, any client,
never logged, never printed. When the key is not set, the endpoints
answer 503 and the frontend falls back to the device's built-in
speechSynthesis voices — chat never breaks because the voice is off.

Default voice is Adam, the premade masculine narrator voice
(voice ID confirmed against ElevenLabs' published voice lists).
The user can pick any voice from his own ElevenLabs account in
Settings, and that pick is saved per device in localStorage.
"""

from __future__ import annotations

import os

import requests

API_BASE = "https://api.elevenlabs.io/v1"
# Adam — premade masculine narrator voice.
DEFAULT_VOICE_ID = "pNInz6obpgDQGcFmaJgB"
DEFAULT_VOICE_NAME = "Adam"
MODEL_ID = "eleven_multilingual_v2"
TIMEOUT = 60
# One reply is trimmed to this many characters so a long answer does
# not eat a big slice of the free-tier quota in a single call.
MAX_CHARS = 5000


class VoiceNotConfigured(RuntimeError):
    """No ELEVENLABS_API_KEY on the server."""


class VoiceError(RuntimeError):
    """ElevenLabs answered with an error."""


def _api_key() -> str:
    return os.getenv("ELEVENLABS_API_KEY", "").strip()


def configured() -> bool:
    return bool(_api_key())


def list_voices() -> list[dict]:
    """Voices on the ElevenLabs account, trimmed to what the UI needs."""
    key = _api_key()
    if not key:
        raise VoiceNotConfigured(
            "ELEVENLABS_API_KEY is not set on the server. "
            "Add it in the Render dashboard (Environment), never in code."
        )
    try:
        resp = requests.get(
            f"{API_BASE}/voices",
            headers={"xi-api-key": key},
            timeout=30,
        )
    except requests.RequestException as exc:
        raise VoiceError(f"Could not reach ElevenLabs: {exc}") from exc
    if resp.status_code == 401:
        raise VoiceError(
            "ElevenLabs rejected the server voice key (401). "
            "Check ELEVENLABS_API_KEY in the Render dashboard."
        )
    if not resp.ok:
        raise VoiceError(
            f"ElevenLabs voices error {resp.status_code}: {resp.text[:200]}"
        )
    voices = resp.json().get("voices", [])
    return [
        {
            "voice_id": v.get("voice_id", ""),
            "name": v.get("name", ""),
            "category": v.get("category", ""),
            "labels": v.get("labels", {}) or {},
            "preview_url": v.get("preview_url", ""),
        }
        for v in voices
    ]


def synthesize(text: str, voice_id: str | None = None) -> bytes:
    """Turn text into MP3 audio bytes with the chosen ElevenLabs voice."""
    key = _api_key()
    if not key:
        raise VoiceNotConfigured(
            "ELEVENLABS_API_KEY is not set on the server. "
            "Add it in the Render dashboard (Environment), never in code."
        )
    vid = (voice_id or "").strip() or DEFAULT_VOICE_ID
    text = (text or "").strip()
    if not text:
        raise VoiceError("Nothing to say.")
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]
    try:
        resp = requests.post(
            f"{API_BASE}/text-to-speech/{vid}",
            headers={
                "xi-api-key": key,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
            json={
                "text": text,
                "model_id": MODEL_ID,
                "voice_settings": {
                    "stability": 0.5,
                    "similarity_boost": 0.75,
                },
            },
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise VoiceError(f"Could not reach ElevenLabs: {exc}") from exc
    if resp.status_code == 401:
        raise VoiceError(
            "ElevenLabs rejected the server voice key (401). "
            "Check ELEVENLABS_API_KEY in the Render dashboard."
        )
    if resp.status_code == 402:
        raise VoiceError(
            "ElevenLabs voice quota is used up (402). "
            "Check the ElevenLabs dashboard — the free tier is limited."
        )
    if resp.status_code == 404:
        raise VoiceError(
            f"ElevenLabs has no voice '{vid}' on this account (404). "
            "Pick another voice in Settings."
        )
    if not resp.ok:
        raise VoiceError(
            f"ElevenLabs speech error {resp.status_code}: {resp.text[:200]}"
        )
    return resp.content
