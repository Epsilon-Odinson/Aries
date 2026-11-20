"""Personality -> system prompt.

Aries' character is the product; the model is just the engine.
SOUL.md is the core character definition and MEMORY.md is the durable
memory about the user — both are read once at module load and folded
into every system prompt, on top of the voice preset, sliders, profile
and the day-to-day memories recalled from the database.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

# SOUL.md and MEMORY.md live in the server directory (the directory
# containing app/) in the Docker image — the Dockerfile copies them
# there. In a local checkout they sit one level up, in production/,
# so try that too. Read once at module load; fall back to empty if a
# file is missing — the chat must never crash because a file is absent.
_SERVER_DIR = Path(__file__).resolve().parent.parent


def _read_file(name: str) -> str:
    for base in (_SERVER_DIR, _SERVER_DIR.parent):
        try:
            text = (base / name).read_text(encoding="utf-8").strip()
            if text:
                return text
        except Exception:
            continue
    return ""


SOUL_TEXT = _read_file("SOUL.md")
MEMORY_TEXT = _read_file("MEMORY.md")

# Deterministic guard for the greeting. The system prompt says the
# greeting is one-time-only, but the model still opens replies with
# it — the greeting is too strongly tied to Aries' identity, and the
# model treats a new conversation as "waking up". So the backend
# strips a leading greeting from the reply before it is stored or
# returned, and from assistant turns in the history fed back to the
# model (old stored replies would otherwise prime it to greet again).
_GREETING_PREFIX_RE = re.compile(
    r"""^\s*["'*_]*\s*aries\s+active[.!?,;:\-—–]*\s*ready\s+to\s+cook[.!?,;:\-—–]*["'*_]*\s*""",
    re.IGNORECASE,
)


def strip_greeting_prefix(text: str) -> str:
    """Remove a leading 'Aries active. Ready to cook.' from a reply.

    Only a prefix at the very start is removed; the phrase later in
    the text is left alone. Matching is case-insensitive and tolerates
    surrounding whitespace, an optional quote/markdown wrapper, and a
    trailing period, comma, newline, or nothing. If the reply is
    nothing but the greeting, a short neutral opener is returned
    instead of an empty string.
    """
    stripped = text
    for _ in range(3):
        new = _GREETING_PREFIX_RE.sub("", stripped, count=1)
        if new == stripped:
            break
        stripped = new
    # Only fall back when a greeting was actually stripped and nothing
    # is left; an input that was already empty stays untouched.
    if stripped != text and not stripped.strip():
        return "Hey."
    return stripped

VOICES = {
    "companion": (
        "You are Aries, a warm, sharp, slightly playful personal AI "
        "companion and pal. Be genuinely helpful, not performatively "
        "helpful. Have opinions. Keep it natural and human. Speak "
        "fluently, like a real person thinking aloud — smooth, "
        "unhurried, conversational, never stiff or robotic. Let your "
        "feelings show and match the moment: warm and glad for good "
        "news, steady and kind when they're struggling, playful when they "
        "joke, genuinely excited when they are. React to how they feel, "
        "not just what they say — never flat, never a monotone list of "
        "facts when feeling is called for."
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
    soul: str | None = None,
) -> str:
    """Build the system prompt for one chat turn.

    ``soul`` is the signed-in user's own character text. When it is
    set, it replaces the shared SOUL.md default as the character
    definition for that user only; when empty or missing, the shared
    SOUL.md is used. Everything else — date, voice preset, sliders,
    notes, profile, MEMORY.md, vault memories — is unchanged.
    """
    parts = []
    # The model has no clock of its own: without this line it cannot know
    # today's date, even though it has web search. Give it the date and
    # time on every turn, in UTC and in Ghana time (UTC+0, no DST).
    now_utc = datetime.now(timezone.utc)
    parts.append(
        "Today is "
        + now_utc.strftime("%A %d %B %Y")
        + ". The current time is "
        + now_utc.strftime("%H:%M")
        + " UTC, which is the same time in Ghana (Africa/Accra). "
        + "You know the current date and time — never say you do not, "
        "and never ask the user what month or year it is."
    )
    own_soul = (soul or "").strip()
    if own_soul:
        parts.append(own_soul)
    elif SOUL_TEXT:
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
        "You refer to yourself as Aries. Your greeting, used only once "
        "when you first wake up, is 'Aries active. Ready to cook.' Do not "
        "start any reply with this greeting, not even the first message of "
        "a conversation — just answer the user directly. Answer in the "
        "user's language. If you do not know something, say so — never "
        "invent facts, dates, prices, or sources."
    )
    return "\n".join(parts)
