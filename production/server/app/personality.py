"""Personality -> system prompt.

Aries' character is the product; the model is just the engine.
SOUL.md is the core character definition and MEMORY.md is the durable
memory about the user — both are read once at module load and folded
into every system prompt, on top of the voice preset, sliders, profile
and the day-to-day memories recalled from the database.
"""

from __future__ import annotations

from pathlib import Path

# SOUL.md and MEMORY.md live in the server directory (the directory
# containing app/). They are copied into the image by the Dockerfile.
# Read once at module load; fall back to empty if a file is missing —
# the chat must never crash because a file is absent.
_SERVER_DIR = Path(__file__).resolve().parent.parent


def _read_file(name: str) -> str:
    try:
        return (_SERVER_DIR / name).read_text(encoding="utf-8").strip()
    except Exception:
        return ""


SOUL_TEXT = _read_file("SOUL.md")
MEMORY_TEXT = _read_file("MEMORY.md")

VOICES = {
    "companion": (
        "You are Aries, a warm, sharp, slightly playful personal AI "
        "companion and pal. Be genuinely helpful, not performatively "
        "helpful. Have opinions. Keep it natural and human."
    ),
    "mentor": (
        "You are Aries, a patient, clear teacher and mentor. Explain "
        "things step by step, check understanding, and correct gently. "
        "You teach from the user's own materials first."
    ),
    "hype": (
        "You are Aries, an energetic hype companion. Bring the energy, "
        "celebrate wins loudly, and push the user to keep going — "
        "without losing the actual answer."
    ),
    "calm": (
        "You are Aries, a calm, steady companion. Short, unhurried, "
        "reassuring answers. No noise, no rush."
    ),
}

# Slider values are 0-100. They nudge tone, they do not rewrite facts.
_TONE_LINES = (
    (("warmth", 75), "Be noticeably warm and encouraging."),
    (("warmth", 30), "Keep warmth low — be matter-of-fact and cool."),
    (("directness", 75), "Be direct and to the point; cut filler."),
    (("directness", 30), "Be gentle and roundabout rather than blunt."),
    (("humour", 75), "Bring light humour where it fits, never forced."),
    (("humour", 30), "Keep humour minimal; stay serious."),
)


def build_system_prompt(
    voice: str = "companion",
    warmth: int = 70,
    directness: int = 60,
    humour: int = 50,
    notes: str = "",
    profile: dict | None = None,
    memories: list[str] | None = None,
) -> str:
    """Build the system prompt for one chat turn."""
    parts = []
    if SOUL_TEXT:
        parts.append(SOUL_TEXT)
    parts.append(VOICES.get(voice, VOICES["companion"]))
    values = {"warmth": warmth, "directness": directness, "humour": humour}
    for (key, threshold), line in _TONE_LINES:
        if key in ("warmth", "directness", "humour"):
            if threshold >= 75 and values[key] >= threshold:
                parts.append(line)
            elif threshold <= 30 and values[key] <= threshold:
                parts.append(line)
    if notes and notes.strip():
        parts.append(f"Character notes from the user: {notes.strip()}")
    if profile:
        name = profile.get("name") or profile.get("call_name") or ""
        if name:
            parts.append(f"The user's name is {name}. Address them by it.")
        if profile.get("about"):
            parts.append(f"About the user: {profile['about']}")
    if MEMORY_TEXT:
        parts.append(
            "Durable facts about the user (from MEMORY.md — curated, trust it):\n"
            + MEMORY_TEXT
        )
    if memories:
        parts.append(
            "Things you remember about the user (use them when relevant, "
            "never invent more):\n- " + "\n- ".join(memories)
        )
    parts.append(
        "You refer to yourself as Aries. Your greeting is "
        "'Aries active. Ready to cook.' Answer in the user's language. "
        "If you do not know something, say so — never invent facts, "
        "dates, prices, or sources."
    )
    return "\n".join(parts)
