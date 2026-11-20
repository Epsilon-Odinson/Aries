"""Automatic memory learning from ordinary chat.

Aries used to learn only when the user said "remember ..." — nobody
talks like that. Like Muse, Aries now notices durable facts about the
user in normal conversation and keeps them itself.

Pipeline, in order:
1. ``save_memory`` set to "never" → learn nothing at all.
2. Cheap heuristic gate (no LLM call): the message must look like it
   carries a fact about the user, and must not be a question, a
   command, or a do-not-remember signal.
3. Credential guard: anything that looks like a password, API key,
   token or one-time code is never learned, however it is phrased.
4. A small, strict LLM extractor call: list durable facts about the
   user, one per line, or NONE. Capped at 3 facts per turn.
5. Deduplicate against the vault (normalised text), so the vault does
   not fill with repeats.
6. Facts go in through the EXISTING pending flow (Memory tab approval
   + the "Aries learned: ..." notice), unless the user already gave
   save_memory consent — in which case they are saved directly.

Only facts about the user themself are learned in v1. Facts about
other people are not learned as if they were the user. The extractor
runs AFTER the chat reply is produced, so it never delays the answer
itself — and only on turns the gate lets through. Any failure in the
extractor is swallowed: learning is a bonus, never a reason to break
a chat reply.
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from .db import Memory
from .llm import chat as llm_chat
from .permissions import PermissionDenied, get_setting, require

# ---------------------------------------------------------------- gate

# Phrasing that carries a durable fact about the speaker. Kept tight:
# "I have a question" is transient, so "I have" is deliberately out.
_FACT_PHRASES = (
    r"\bi am\b",
    r"\bi'm\b",
    r"\bmy\b",
    r"\bi like\b",
    r"\bi love\b",
    r"\bi want\b",
    r"\bi need\b",
    r"\bi study\b",
    r"\bi'm studying\b",
    r"\bi'm learning\b",
    r"\bi live\b",
    r"\bi work\b",
    r"\bi play\b",
    r"\bi prefer\b",
    r"\bi hate\b",
    r"\bi don't like\b",
    r"\bi plan\b",
    r"\bi'm going to\b",
    r"\bi go to\b",
    r"\bmy favourite\b",
    r"\bmy favorite\b",
)
_FACT_RE = re.compile("|".join(_FACT_PHRASES), re.IGNORECASE)

_QUESTION_START = re.compile(
    r"^(what|when|where|who|whom|whose|why|how|is|are|can|could|do|does|did|"
    r"will|would|should|have|has|am|tell me|show me|explain|give me)\b",
    re.IGNORECASE,
)

_DECLINE_RE = re.compile(
    r"\b(don't|do not|never)\s+(remember|save|keep|store)\b"
    r"|\bforget (that|this|it)\b",
    re.IGNORECASE,
)

_MIN_LEN = 10
_MAX_LEN = 500


def _might_carry_fact(text: str) -> bool:
    """Cheap gate: does this message look like it states a fact about
    the user? No LLM call — just phrasing and shape."""
    stripped = (text or "").strip()
    if len(stripped) < _MIN_LEN or len(stripped) > _MAX_LEN:
        return False
    lowered = stripped.lower()
    if lowered.startswith("remember ") or lowered.startswith("remind me"):
        return False  # explicit teach / task paths already handle these
    if stripped.endswith("?"):
        return False
    if _QUESTION_START.match(stripped):
        return False
    if _DECLINE_RE.search(stripped):
        return False
    return bool(_FACT_RE.search(stripped))


# ---------------------------------------------------------------- credential guard

# Anything matching one of these is never learned, and never sent to
# the extractor. Patterns are deliberately broad: a missed fact is
# cheap, a learned secret is not.
_CREDENTIAL_RES = (
    re.compile(r"nvapi-[A-Za-z0-9_\-]{8,}", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9]{8,}", re.IGNORECASE),
    re.compile(r"\b(ghp_|gho_|ghu_|ghs_|ghr_|github_pat_)[A-Za-z0-9_]{8,}", re.IGNORECASE),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{8,}", re.IGNORECASE),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bBearer\s+\S+", re.IGNORECASE),
    re.compile(
        r"\b(api[_-]?key|password|passwd|secret|token|otp|one[- ]time code|"
        r"pin\s*(?:code|number)?)\s*(?:is|:|=)\s*\S+",
        re.IGNORECASE,
    ),
    # A long random-looking run (32+ chars, mixed letters/digits) is
    # shaped like a key or token, not like a sentence.
    re.compile(r"\b[A-Za-z0-9_\-]{32,}\b"),
)


def _looks_like_credential(text: str) -> bool:
    return any(rx.search(text or "") for rx in _CREDENTIAL_RES)


# ---------------------------------------------------------------- extractor

_EXTRACTOR_SYSTEM = (
    "You extract durable facts about the user from a chat message. "
    "Reply with one fact per line, each a short plain sentence starting "
    "with 'The user '. Only facts that would still be true next month: "
    "what they study, like, want, do, plan, or prefer, and who matters "
    "to them. If there is no durable fact, reply with the single word "
    "NONE. Never invent anything. Never include passwords, API keys, "
    "tokens, codes, or anything secret. Never include facts about other "
    "people — only about the user themself."
)

_MAX_FACTS_PER_TURN = 3
_MAX_FACT_LEN = 200


def _extract_facts(text: str, reply: str) -> list[str]:
    """One small, strict LLM call. Returns at most 3 facts; [] on any
    failure or when the model says NONE."""
    try:
        out = llm_chat(
            [
                {"role": "system", "content": _EXTRACTOR_SYSTEM},
                {
                    "role": "user",
                    "content": f"User said: {text}\nAries replied: {reply}",
                },
            ],
            temperature=0.1,
            max_tokens=160,
            timeout=20,
        )
    except Exception:
        return []
    facts = []
    for line in (out or "").splitlines():
        line = line.strip().lstrip("-•*0123456789. ").strip()
        if not line or line.upper() == "NONE":
            continue
        if len(line) > _MAX_FACT_LEN:
            continue
        if _looks_like_credential(line):
            continue
        facts.append(line)
        if len(facts) >= _MAX_FACTS_PER_TURN:
            break
    return facts


# ---------------------------------------------------------------- dedup

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", (text or "").lower())).strip()


def _already_known(db: Session, user_key: str, fact: str) -> bool:
    """True when the vault already holds this fact (exact normalised
    match, or one side contains the other)."""
    needle = _norm(fact)
    if not needle:
        return True
    for mem in db.query(Memory).filter_by(user_key=user_key).all():
        hay = _norm(mem.text)
        if not hay:
            continue
        if needle == hay or needle in hay or hay in needle:
            return True
    return False


# ---------------------------------------------------------------- entry point

def learn_from_chat(
    db: Session,
    user_key: str,
    text: str,
    reply: str,
    consent: bool = False,
) -> dict | None:
    """Learn durable facts from one ordinary chat turn.

    Returns a ``pending_memory`` dict for the first fact learned (in
    the same shape as the explicit "remember ..." path), or None when
    nothing was learned. Never raises.
    """
    try:
        if get_setting(db, user_key, "save_memory") == "never":
            return None
        if not _might_carry_fact(text):
            return None
        if _looks_like_credential(text):
            return None
        facts = _extract_facts(text, reply)
        if not facts:
            return None
        # Permission: consent given → saved straight away; otherwise
        # held pending approval, exactly like the explicit path.
        try:
            require(db, user_key, "save_memory", consent=consent)
            save_now = True
        except PermissionDenied:
            save_now = False
        first = None
        for fact in facts:
            if _already_known(db, user_key, fact):
                continue
            mem = Memory(
                user_key=user_key,
                text=fact,
                category="general",
                pending=not save_now,
            )
            db.add(mem)
            db.commit()
            if first is None:
                first = {"id": mem.id, "text": fact, "saved": save_now}
        return first
    except Exception:
        # Learning is a bonus, never a reason to break a reply.
        try:
            db.rollback()
        except Exception:
            pass
        return None
