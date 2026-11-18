"""Configuration for Aries.

The NVIDIA API key is NEVER stored in code, in a document, or in
this repository. It is read, in order, from:

1. The ``NVIDIA_API_KEY`` environment variable (Windows: set it as a
   user environment variable — see README.md).
2. The OS credential store via ``keyring`` (service ``"Aries"``,
   username ``"nvidia_api_key"``), if keyring is installed.

If no key is found, a clear error is raised telling the user how to
set one. The key value is never printed or logged anywhere.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()  # Loads a local .env file if present (.env is gitignored).

API_BASE_URL = "https://integrate.api.nvidia.com/v1"
# CHAT_MODEL was meta/llama-3.1-70b-instruct in the original spec, but
# NVIDIA retired it (HTTP 410, end of life 2026-08-26). Switched
# 2026-11-18, at the user's choice, to Nemotron 3.5 Lightning,
# on NVIDIA's free tier (verified 2026-11-16).
CHAT_MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"
# Vision model for screenshot description (Module 1 does not use it yet).
# Hosted availability of meta/llama-3.2-11b-vision-instruct is unconfirmed
# as of 2026-11-18 — verify before wiring it in.
VISION_MODEL = "meta/llama-3.2-11b-vision-instruct"

KEYRING_SERVICE = "Aries"
KEYRING_USERNAME = "nvidia_api_key"


def _key_from_keyring() -> str | None:
    """Return the key from the OS credential store, or None."""
    try:
        import keyring
    except ImportError:
        return None
    try:
        return keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
    except Exception:
        return None


def get_api_key() -> str:
    """Return the NVIDIA API key, or raise a helpful error."""
    key = os.getenv("NVIDIA_API_KEY")
    if key:
        return key.strip()

    key = _key_from_keyring()
    if key:
        return key.strip()

    raise RuntimeError(
        "NVIDIA_API_KEY is not set.\n"
        "Set it as a Windows user environment variable:\n"
        "  1. Search for 'Edit environment variables for your account'.\n"
        "  2. Under User variables, add NVIDIA_API_KEY with your key as the value.\n"
        "  3. Close and reopen your terminal / VS Code, then run Aries again.\n"
        "Or store it with keyring (service 'Aries', username 'nvidia_api_key').\n"
        "Never paste the key into code, a document, a chat, or GitHub."
    )


def has_api_key() -> bool:
    """True if a key is available, without revealing it."""
    try:
        get_api_key()
    except RuntimeError:
        return False
    return True
