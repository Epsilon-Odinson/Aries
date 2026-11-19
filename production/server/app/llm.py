"""NVIDIA LLM client for the Aries production backend.

Same contract as the desktop ``aries/llm.py``: OpenAI-compatible
endpoint, model ``nvidia/nemotron-3.5-lightning-30b-a3b`` (the spec's
Llama 3.1 70B was retired by NVIDIA in August 2026 — HTTP 410).

The API key comes ONLY from the ``NVIDIA_API_KEY`` environment
variable (set in the Render dashboard). It is never read from the
client, never logged, never printed, never stored in the database.
"""

from __future__ import annotations

import base64
import os

import requests

API_BASE_URL = "https://integrate.api.nvidia.com/v1"
CHAT_MODEL = os.getenv("CHAT_MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b")
# Vision model is UNCONFIRMED on NVIDIA hosting as of 2026-11-18.
# Set VISION_MODEL only after verifying the model ID is live; the
# screenshot endpoint falls back honestly when it is not set.
VISION_MODEL = os.getenv("VISION_MODEL", "").strip()

DEFAULT_TIMEOUT = 60


class AriesLLMError(RuntimeError):
    """A clear, user-facing error from the LLM call."""


def _api_key() -> str:
    key = os.getenv("NVIDIA_API_KEY", "").strip()
    if not key:
        raise AriesLLMError(
            "NVIDIA_API_KEY is not set on the server. "
            "Set it in the Render dashboard (Environment), never in code."
        )
    return key


def chat(
    messages: list[dict],
    model: str = CHAT_MODEL,
    temperature: float = 0.7,
    max_tokens: int = 2048,
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """Send a chat conversation to the model and return the reply text."""
    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    try:
        response = requests.post(
            f"{API_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
            timeout=timeout,
        )
    except requests.exceptions.Timeout as exc:
        raise AriesLLMError(
            "The NVIDIA API did not answer in time. Try again."
        ) from exc
    except requests.exceptions.ConnectionError as exc:
        raise AriesLLMError(
            "Could not reach the NVIDIA API. Check the server connection."
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise AriesLLMError(f"Network error talking to the NVIDIA API: {exc}") from exc

    if response.status_code == 401:
        raise AriesLLMError(
            "NVIDIA rejected the server API key (401). "
            "The key may be wrong, expired, or revoked — "
            "rotate it in the Render dashboard."
        )
    if response.status_code == 410:
        raise AriesLLMError(
            f"NVIDIA says this model is gone (410): '{model}' may have "
            "reached end of life. Update CHAT_MODEL to a supported model."
        )
    if response.status_code == 429:
        raise AriesLLMError(
            "NVIDIA rate limit hit (429). Wait a minute and try again."
        )
    if not response.ok:
        raise AriesLLMError(
            f"NVIDIA API error {response.status_code}: {response.text[:300]}"
        )
    try:
        data = response.json()
        return data["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError) as exc:
        raise AriesLLMError(
            f"Unexpected reply from the NVIDIA API: {response.text[:300]}"
        ) from exc


def describe_image(image_bytes: bytes, mime: str, question: str = "") -> str:
    """Describe an image with the vision model, if one is configured.

    Falls back honestly when no vision model is configured or the
    model call fails — the caller reports this instead of inventing
    a description of an image it cannot see.
    """
    if not VISION_MODEL:
        raise AriesLLMError(
            "VISION_MODEL is not configured on the server, so I cannot "
            "see this image yet. The vision model "
            "(meta/llama-3.2-11b-vision-instruct) is unconfirmed on "
            "NVIDIA hosting — set VISION_MODEL only after verifying "
            "the model ID is live."
        )
    b64 = base64.b64encode(image_bytes).decode("ascii")
    prompt = (
        "Describe this image in detail: layout, windows, readable text "
        "quoted exactly, colours, and anything notable. Only describe "
        "what is actually visible. "
        + (f"Also answer this question about it: {question}" if question else "")
    )
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                },
            ],
        }
    ]
    return chat(messages, model=VISION_MODEL, max_tokens=1024)
