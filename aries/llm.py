"""NVIDIA LLM client for Aries.

Talks to the NVIDIA hosted API (OpenAI-compatible endpoint) at
https://integrate.api.nvidia.com/v1/chat/completions using the
text model ``nvidia/nemotron-3.5-lightning-30b-a3b`` (see config.py —
the spec's Llama 3.1 70B was retired by NVIDIA in August 2026).

The API key is never logged or printed. HTTP 401 (bad/revoked key)
and 429 (rate limit) get clear, actionable error messages.
"""

from __future__ import annotations

import requests

from .config import API_BASE_URL, CHAT_MODEL, get_api_key

DEFAULT_TIMEOUT = 60  # seconds


class AriesLLMError(RuntimeError):
    """A clear, user-facing error from the LLM call."""


def chat(
    messages: list[dict[str, str]],
    model: str = CHAT_MODEL,
    temperature: float = 0.7,
    max_tokens: int = 1024,
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """Send a chat conversation to the model and return the reply text.

    ``messages`` is a list of ``{"role": ..., "content": ...}`` dicts,
    e.g. ``[{"role": "user", "content": "Hello"}]``.
    """
    api_key = get_api_key()
    headers = {
        "Authorization": f"Bearer {api_key}",
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
            "The NVIDIA API did not answer in time. Check your connection and try again."
        ) from exc
    except requests.exceptions.ConnectionError as exc:
        raise AriesLLMError(
            "Could not reach the NVIDIA API. Check your internet connection and try again."
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise AriesLLMError(f"Network error talking to the NVIDIA API: {exc}") from exc

    if response.status_code == 401:
        raise AriesLLMError(
            "NVIDIA rejected the API key (401 Unauthorized). "
            "The key may be wrong, expired, or revoked. "
            "Generate a fresh key at build.nvidia.com, save it as "
            "NVIDIA_API_KEY, and try again."
        )
    if response.status_code == 410:
        raise AriesLLMError(
            "NVIDIA says this model is gone (410). "
            f"The model '{model}' may have reached end of life. "
            "Check the model list at build.nvidia.com and update "
            "CHAT_MODEL in aries/config.py to a supported model."
        )
    if response.status_code == 429:
        raise AriesLLMError(
            "NVIDIA rate limit hit (429). You are sending requests too fast "
            "for the free tier. Wait a minute and try again."
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


def chat_once(prompt: str, system: str | None = None, **kwargs) -> str:
    """One-shot convenience wrapper around :func:`chat`."""
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return chat(messages, **kwargs)
