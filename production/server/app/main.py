"""Aries production backend — FastAPI.

Single service: serves the JSON API under /api and the built Vite
frontend as static files, so Render runs one web service and there
is no CORS problem in production.

Security rules (from the product decisions):
- The NVIDIA API key lives ONLY in the NVIDIA_API_KEY env var on the
  server. It is never accepted from, or sent to, any client.
- Permissions are enforced here, server-side; the UI is only a prompt.
- Aries is private to Epsilon and Kelfy until it goes commercial.
  Accounts: everyone signs in with their own account and gets only
  their own chat, memories, tasks and profile. Admin is whoever holds
  the admin username (env ARIES_ADMIN_USERNAME, default "epsilon") —
  by username, never by who signed up first. See auth.py.
- No PC control in this build (no shell, no mouse/keyboard, no
  pyautogui). That is the LAST roadmap item and is not included here.

Run locally:  uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import io
import json
import os
import re
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from . import auth as auth_mod
from . import connections as conn_mod
from . import face as face_mod
from . import voice as voice_mod
from .db import (
    Connection,
    FaceEnrollment,
    IssueReport,
    Memory,
    Message,
    Permission,
    Personality,
    Profile,
    Screenshot,
    Task,
    User,
    get_db,
    init_db,
)
from .auth import get_current_user, require_admin
from .llm import AriesLLMError, chat as llm_chat, describe_image
from .memory_file import memory_file_path, read_memory_file, refresh_memory_file
from .memory_learn import learn_from_chat
from .permissions import (
    CAPABILITIES,
    PermissionDenied,
    get_setting,
    require,
    set_setting,
)
from .personality import build_system_prompt, strip_greeting_prefix
from .search import search_web

app = FastAPI(title="Aries", version="0.5.0")

# Local dev only: the Vite dev server runs on :5173. In production the
# backend serves the built frontend itself, so no CORS is needed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

init_db()

def current_key(user: User = Depends(get_current_user)) -> str:
    """Dependency: the signed-in user's data key (canonical username).

    Every data endpoint depends on this, so all rows are scoped to the
    logged-in account and a request without a live session gets 401.
    """
    return user.username_lower


def get_connect_user(
    request: Request,
    db: Session = Depends(get_db),
    token: str | None = Query(None),
) -> User:
    """Who is starting an OAuth connect flow.

    The Connect button opens a new tab (window.open), which cannot
    send an Authorization header — so the frontend appends the
    session token as ?token=. The token stays on our own domain (it
    is never forwarded to Google/Spotify); the provider only ever
    sees the OAuth state value. A header Bearer token works too.
    """
    tok = token
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        tok = auth_header[7:].strip()
    user = auth_mod.user_for_token(db, tok) if tok else None
    if not user:
        raise HTTPException(401, "Sign in to use Aries.")
    return user

# ---------------------------------------------------------------- health


@app.get("/api/health")
def health():
    # DATABASE_URL is set for Postgres (Render) AND for a local SQLite
    # file (the desktop app sets it to ~/.aries/aries.db), so the
    # scheme — not just "is it set" — decides the label.
    _db_url = os.getenv("DATABASE_URL", "")
    return {
        "ok": True,
        "service": "aries",
        "version": "0.5.0",
        "key_configured": bool(os.getenv("NVIDIA_API_KEY")),
        "vision_configured": bool(os.getenv("VISION_MODEL")),
        "elevenlabs_configured": voice_mod.configured(),
        "db": "postgres" if _db_url.startswith(("postgres://", "postgresql://")) else "sqlite",
    }


# ---------------------------------------------------------------- accounts

# Sign-in, sign-up, and admin. Sessions are random tokens stored
# server-side; the frontend sends them as Authorization: Bearer and
# keeps them in localStorage. See auth.py for the rules — in short:
# admin is the account holding ARIES_ADMIN_USERNAME (default
# "epsilon"), by username on every login, never by signup order.


class AuthIn(BaseModel):
    username: str
    password: str


@app.post("/api/auth/register")
def register(body: AuthIn, db: Session = Depends(get_db)):
    # NOTE: no auth dependency here on purpose — this is how the
    # first account (and every later one) is created.
    try:
        user = auth_mod.create_user(db, body.username, body.password)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    token = auth_mod.create_session(db, user)
    return {"ok": True, "token": token, "user": auth_mod.public_user(user)}


@app.post("/api/auth/login")
def login(body: AuthIn, db: Session = Depends(get_db)):
    # NOTE: no auth dependency here on purpose — this is sign-in.
    user = auth_mod.authenticate(db, body.username, body.password)
    if not user:
        raise HTTPException(401, "Wrong username or password.")
    token = auth_mod.create_session(db, user)
    return {"ok": True, "token": token, "user": auth_mod.public_user(user)}


@app.post("/api/auth/logout")
def logout(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Delete this session server-side; the frontend drops its token
    # either way, so a logout always ends signed-in here too.
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        auth_mod.delete_session(db, auth_header[7:].strip())
    return {"ok": True}


@app.get("/api/auth/me")
def me(user: User = Depends(get_current_user)):
    return {"ok": True, "user": auth_mod.public_user(user)}


@app.get("/api/admin/users")
def admin_list_users(admin: User = Depends(require_admin), db: Session = Depends(get_db), user_key: str = Depends(current_key)):
    users = db.query(User).order_by(User.created_at.asc()).all()
    return {"users": [auth_mod.public_user(u) for u in users]}


@app.delete("/api/admin/users/{username}")
def admin_delete_user(
    username: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    user_key: str = Depends(current_key),
):
    target = auth_mod.get_user_by_name(db, username)
    if not target:
        raise HTTPException(404, "No such user.")
    if auth_mod.is_admin_username(target.username_lower):
        raise HTTPException(400, "The admin account cannot be deleted.")
    if target.id == admin.id:
        raise HTTPException(400, "You cannot delete your own account.")
    auth_mod.delete_user_data(db, target)
    return {"ok": True, "deleted": target.username}

# ---------------------------------------------------------------- voice (ElevenLabs)

# Talk-back speaks with a real narrator voice when the server has an
# ELEVENLABS_API_KEY. No key -> clean 503, and the frontend falls back
# to the device's built-in voices. The key is never accepted from, or
# sent to, any client.


class TTSIn(BaseModel):
    text: str
    voice_id: str | None = None


@app.post("/api/tts")
def tts(body: TTSIn, user: User = Depends(get_current_user)):
    try:
        audio = voice_mod.synthesize(body.text, body.voice_id)
    except voice_mod.VoiceNotConfigured:
        raise HTTPException(503, "voice service not configured") from None
    except voice_mod.VoiceError as exc:
        raise HTTPException(502, str(exc)) from exc
    return Response(content=audio, media_type="audio/mpeg")


@app.get("/api/voices")
def voices(user: User = Depends(get_current_user)):
    try:
        vs = voice_mod.list_voices()
    except voice_mod.VoiceNotConfigured:
        raise HTTPException(503, "voice service not configured") from None
    except voice_mod.VoiceError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {
        "configured": True,
        "default_voice_id": voice_mod.DEFAULT_VOICE_ID,
        "default_voice_name": voice_mod.DEFAULT_VOICE_NAME,
        "voices": vs,
    }


# ---------------------------------------------------------------- helpers


def _profile_dict(db: Session, user_key: str) -> dict:
    row = db.query(Profile).filter_by(user_key=user_key).first()
    if not row:
        return {"name": "", "call_name": "", "about": ""}
    return {"name": row.name, "call_name": row.call_name, "about": row.about}


def _personality_row(db: Session, user_key: str) -> Personality:
    row = db.query(Personality).filter_by(user_key=user_key).first()
    if not row:
        row = Personality(user_key=user_key)
        db.add(row)
        db.commit()
    return row


def _personality_dict(db: Session, user_key: str) -> dict:
    row = _personality_row(db, user_key)
    return {
        "voice": row.voice,
        "warmth": row.warmth,
        "directness": row.directness,
        "humour": row.humour,
        "notes": row.notes,
        "soul": row.soul or "",
    }


def _memories(db: Session, user_key: str, include_pending: bool = False) -> list[Memory]:
    q = db.query(Memory).filter_by(user_key=user_key)
    if not include_pending:
        q = q.filter_by(pending=False)
    return q.order_by(Memory.pinned.desc(), Memory.created_at.desc()).all()


def _recall_memories(db: Session, user_key: str, text: str, limit: int = 5) -> list[str]:
    """Simple keyword recall: memories sharing a word with the message."""
    words = {w for w in re.findall(r"[a-z]{4,}", text.lower())}
    picked = []
    for mem in _memories(db, user_key):
        mem_words = set(re.findall(r"[a-z]{4,}", mem.text.lower()))
        if words & mem_words:
            picked.append(mem.text)
        if len(picked) >= limit:
            break
    return picked


def _sources_json(rows) -> str:
    return json.dumps(
        [
            {
                "title": s.get("title", ""),
                "url": s.get("url", ""),
                "publisher": s.get("publisher", ""),
            }
            for s in rows
        ]
    )


# ---------------------------------------------------------------- state


@app.get("/api/state")
def state(db: Session = Depends(get_db), user_key: str = Depends(current_key)):
    person = _personality_dict(db, user_key)
    face = db.query(FaceEnrollment).filter_by(user_key=user_key).first()
    return {
        "profile": _profile_dict(db, user_key),
        "personality": person,
        "memory_file": read_memory_file(db, user_key),
        "memories": [
            {
                "id": m.id,
                "text": m.text,
                "category": m.category,
                "pinned": m.pinned,
                "pending": m.pending,
            }
            for m in _memories(db, user_key, include_pending=True)
        ],
        "tasks": [
            {
                "id": t.id,
                "title": t.title,
                "notes": t.notes,
                "due_at": t.due_at.isoformat() if t.due_at else None,
                "done": t.done,
                "reminded": t.reminded,
            }
            for t in db.query(Task)
            .filter_by(user_key=user_key)
            .order_by(Task.due_at.asc().nullslast(), Task.created_at.desc())
            .all()
        ],
        "permissions": {
            cap: get_setting(db, user_key, cap) for cap in CAPABILITIES
        },
        "face": {
            "enrolled": bool(face),
            "login_enabled": get_setting(db, user_key, "face_login") == "always",
            "has_pin": bool(face and face.pin_hash),
        },
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "sources": json.loads(m.sources) if m.sources else [],
            }
            for m in db.query(Message)
            .filter_by(user_key=user_key)
            .order_by(Message.created_at.desc())
            .limit(50)
            .all()[::-1]
        ],
    }


# ---------------------------------------------------------------- profile & personality


class ProfileIn(BaseModel):
    name: str = ""
    call_name: str = ""
    about: str = ""


@app.post("/api/profile")
def save_profile(body: ProfileIn, db: Session = Depends(get_db), user_key: str = Depends(current_key)):
    row = db.query(Profile).filter_by(user_key=user_key).first()
    if not row:
        row = Profile(user_key=user_key)
        db.add(row)
    row.name, row.call_name, row.about = body.name, body.call_name, body.about
    row.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "profile": _profile_dict(db, user_key)}


class PersonalityIn(BaseModel):
    voice: str = "companion"
    warmth: int = Field(70, ge=0, le=100)
    directness: int = Field(60, ge=0, le=100)
    humour: int = Field(50, ge=0, le=100)
    notes: str = ""
    # The user's own character text. None means "not sent" (leave the
    # stored soul alone) — the SetupWizard posts without it, so it must
    # never wipe a soul the user already wrote. "" clears it back to
    # the shared default.
    soul: str | None = None


@app.post("/api/personality")
def save_personality(body: PersonalityIn, db: Session = Depends(get_db), user_key: str = Depends(current_key)):
    row = _personality_row(db, user_key)
    if body.voice not in ("companion", "mentor", "hype", "calm"):
        raise HTTPException(400, "Unknown voice")
    row.voice, row.warmth = body.voice, body.warmth
    row.directness, row.humour, row.notes = (
        body.directness,
        body.humour,
        body.notes,
    )
    if body.soul is not None:
        row.soul = body.soul[:8000]
    row.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "personality": _personality_dict(db, user_key)}


# ---------------------------------------------------------------- chat


class ChatIn(BaseModel):
    message: str
    # None = default (search on); False = the user opted out for this turn.
    use_search: bool | None = None
    consent: dict = {}  # e.g. {"web_search": true, "save_memory": true}


class ChatOut(BaseModel):
    reply: str
    sources: list = []
    searched: bool = False
    pending_memory: dict | None = None
    task_added: dict | None = None
    needs_permission: str | None = None


REMIND_RE = re.compile(
    r"remind me to (.+?)(?: (?:tomorrow|today|at|on) (.+))?$", re.IGNORECASE
)


def _parse_reminder(text: str) -> dict | None:
    """Very small reminder parser: 'remind me to <thing> [tomorrow at 9am]'."""
    m = re.match(r"(?i)remind me to (.+)$", text.strip())
    if not m:
        return None
    rest = m.group(1).strip()
    title, when = rest, ""
    for marker in (" tomorrow", " today"):
        if marker in rest.lower():
            idx = rest.lower().index(marker)
            title, when = rest[:idx].strip(), rest[idx + 1 :].strip()
            break
    title = title.strip()
    title = title[:1].upper() + title[1:] if title else title
    due = None
    if when:
        from datetime import timedelta

        base = datetime.utcnow()
        if when.lower().startswith("tomorrow"):
            base = base + timedelta(days=1)
        hm = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", when.lower())
        if hm:
            hour = int(hm.group(1))
            minute = int(hm.group(2) or 0)
            ampm = hm.group(3)
            if ampm == "pm" and hour < 12:
                hour += 12
            if ampm == "am" and hour == 12:
                hour = 0
            due = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        else:
            due = base.replace(hour=9, minute=0, second=0, microsecond=0)
    return {"title": title, "due_at": due}


def _learnable_fact(text: str) -> str | None:
    """Spot an explicit teachable fact: 'remember ...' / 'my name is ...'."""
    m = re.match(r"(?i)remember (?:that )?(.+)", text.strip())
    if m:
        return m.group(1).strip()
    m = re.match(r"(?i)my name is ([A-Za-z' -]+)", text.strip())
    if m:
        return f"User's name is {m.group(1).strip()}"
    return None


# Turns live info cannot help. A small, explicit check — no LLM call.
_GREETING_BARE = {
    "hi", "hey", "hello", "yo", "sup", "hiya", "howdy",
    "good morning", "good afternoon", "good evening", "good day",
    "hey aries", "hi aries", "hello aries", "yo aries",
    "hey how are you", "hi how are you", "hello how are you",
    "hey whats up", "hi whats up", "how are you", "whats up",
    # Informal greetings — the way people actually talk to Aries.
    "bro", "bruh", "broo", "heyy", "heyyy", "hii", "helloo", "heyya",
    "hey bro", "hi bro", "yo bro", "wassup", "wassup bro",
    "whats good", "whats good bro", "what up", "what up bro",
    "bro whats up", "yo bro whats up", "hey bro whats up",
    "hey there", "hello there",
    "hey bro how are you", "hi bro how are you",
    "wassup aries", "whats good aries",
}
_CHITCHAT = {
    "thanks", "thank you", "thank u", "ok", "okay", "lol", "lmao",
    "yes", "no", "yeah", "yep", "nah", "bye", "goodbye",
    "good night", "goodnight", "cool", "nice", "great", "perfect",
}
# Words a message can be made of and still only be a greeting or an
# address — never a topic. The short-message fallback below exempts a
# 1–2 word message built only from these, whatever the slang ("yo
# fam", "sup boss"). Topic words (anatomy, biochemistry, signal, gt,
# pro, ...) are deliberately NOT in here, so a short topic query
# still searches.
_GREETING_WORDS = {
    "hi", "hey", "hello", "yo", "sup", "wassup", "hiya", "howdy",
    "whats", "what", "up", "good", "morning", "afternoon", "evening",
    "day", "there", "bro", "bruh", "broo", "fam", "mate", "pal",
    "boss", "chief", "aries", "heyy", "hii", "helloo", "heyya",
}
_CREATIVE_RE = re.compile(
    r"\b(write|tell|compose|invent|make up)\b.{0,50}\b"
    r"(poem|story|joke|rap|song|letter|essay|tale|haiku|limerick|novel)\b"
    r"|\broleplay\b"
    r"|\bpretend (you are|to be|you're)\b"
    r"|\btell me a joke\b",
    re.IGNORECASE,
)
_PERSONAL_MARKER_RE = re.compile(
    r"\b(my|mine|myself|about me|do you remember|what do you know|who am i|what am i)\b",
    re.IGNORECASE,
)
_QUESTION_START_RE = re.compile(
    r"^(what|when|where|who|whom|whose|why|how|is|are|can|could|do|does|did|"
    r"will|would|should)\b",
    re.IGNORECASE,
)
_SELF_STATEMENT_RE = re.compile(
    r"^(my |i am |i'm |i like |i love |i study |i'm studying |i'm learning |"
    r"i live |i work |i play |i prefer |i hate )",
    re.IGNORECASE,
)
_LIVE_TRIGGER_RE = re.compile(
    r"\b(today|yesterday|latest|news|price|how much|weather|score|release|"
    r"current|now|2026|2027|when)\b",
    re.IGNORECASE,
)
# Time/date questions are answered by the date line in the system
# prompt, not by the web.
_TIME_QUESTION_RE = re.compile(
    r"\bwhat'?s the (time|date|day)\b"
    r"|\bwhat time is it\b"
    r"|\bwhat day is (it|today)\b"
    r"|\bwhat'?s today'?s date\b",
    re.IGNORECASE,
)


def _is_search_exempt(text: str) -> bool:
    """True when a web search cannot help this turn: greetings and
    chit-chat, teach commands (handled by the memory learner), pure
    creative requests, personal questions about the user (answered
    from the user's own vault/profile, or honestly unknown, never
    from the web), and self-statements about the user that carry no
    live-info trigger. A small, explicit check — no LLM call."""
    stripped = (text or "").strip()
    bare = re.sub(r"\s+", " ", re.sub(r"[^a-z ]", "", stripped.lower())).strip()
    # Stretched letters ("brooo", "heyyy", "yooo") are the same greeting.
    bare = re.sub(r"(.)\1{2,}", r"\1", bare)
    if bare in _GREETING_BARE or bare in _CHITCHAT:
        return True
    # Short-message fallback: a 1–2 word message made only of
    # greeting/address words, with no question mark, no question
    # start and no live trigger, is chitchat — even if the exact
    # slang is new. A topic word breaks the rule, so "Anatomy",
    # "Signal 50" or "GT 50 Pro" still search.
    words = bare.split()
    if (
        1 <= len(words) <= 2
        and not stripped.endswith("?")
        and not _QUESTION_START_RE.match(stripped)
        and not _LIVE_TRIGGER_RE.search(stripped)
        and all(w in _GREETING_WORDS for w in words)
    ):
        return True
    if stripped.lower().startswith("remember "):
        return True
    if _TIME_QUESTION_RE.search(stripped):
        return True
    if _CREATIVE_RE.search(stripped):
        return True
    if _PERSONAL_MARKER_RE.search(stripped) and (
        stripped.endswith("?") or _QUESTION_START_RE.match(stripped)
    ):
        return True
    if _SELF_STATEMENT_RE.match(stripped) and not _LIVE_TRIGGER_RE.search(stripped):
        return True
    return False


@app.post("/api/chat", response_model=ChatOut)
def chat_endpoint(body: ChatIn, db: Session = Depends(get_db), user_key: str = Depends(current_key)):
    text = body.message.strip()
    if not text:
        raise HTTPException(400, "Empty message")
    db.add(Message(user_key=user_key, role="user", content=text))
    db.commit()

    # Tasks: "remind me to ..." saves a task, no LLM call needed.
    reminder = _parse_reminder(text)
    task_added = None
    if reminder:
        task = Task(
            user_key=user_key,
            title=reminder["title"],
            due_at=reminder["due_at"],
        )
        db.add(task)
        db.commit()
        task_added = {
            "id": task.id,
            "title": task.title,
            "due_at": task.due_at.isoformat() if task.due_at else None,
        }
        reply = f"Task kept: {task.title}."
        if task.due_at:
            reply += f" I'll remind you at {task.due_at.strftime('%H:%M')}."
        db.add(Message(user_key=user_key, role="aries", content=reply))
        db.commit()
        return ChatOut(reply=reply, task_added=task_added)

    # Memory learning: explicit facts are held pending approval.
    pending_memory = None
    fact = _learnable_fact(text)
    if fact:
        try:
            require(db, user_key, "save_memory", consent=bool(body.consent.get("save_memory")))
            mem = Memory(user_key=user_key, text=fact, pending=False)
            db.add(mem)
            db.commit()
            pending_memory = {"id": mem.id, "text": fact, "saved": True}
        except PermissionDenied:
            if get_setting(db, user_key, "save_memory") == "never":
                pending_memory = None
            else:
                mem = Memory(user_key=user_key, text=fact, pending=True)
                db.add(mem)
                db.commit()
                pending_memory = {"id": mem.id, "text": fact, "saved": False}
        refresh_memory_file(db, user_key)

    # Web search is on by default: Aries checks live info for every
    # answer. The globe toggle is an opt-out for one turn, and the
    # exclusion check skips turns live info cannot help (greetings,
    # vault questions, creative requests). "never" in Permissions
    # means never — not even a prompt.
    opted_out = body.use_search is False
    want_search = (not opted_out) and not _is_search_exempt(text)
    sources: list[dict] = []
    searched = False
    search_context = ""
    if want_search and get_setting(db, user_key, "web_search") == "never":
        want_search = False
    if want_search:
        try:
            require(
                db,
                user_key,
                "web_search",
                consent=bool(body.consent.get("web_search")),
            )
        except PermissionDenied as exc:
            db.add(
                Message(
                    user_key=user_key,
                    role="aries",
                    content="I haven't checked the web — that needs your permission first.",
                )
            )
            db.commit()
            return ChatOut(
                reply="I haven't checked the web — that needs your permission first.",
                needs_permission="web_search",
                pending_memory=pending_memory,
                task_added=task_added,
            )
        try:
            sources = search_web(text, max_results=5)
            searched = True
        except Exception:
            sources = []
            # The disclaimer only leads the reply when this turn
            # genuinely needed current information — a question, or
            # a live trigger like a price or "today". Any other
            # turn gets a conditional note instead, so a greeting
            # that ever slips past the exemption can never open
            # with "I could not check live info".
            if (
                text.endswith("?")
                or _QUESTION_START_RE.match(text)
                or _LIVE_TRIGGER_RE.search(text)
            ):
                search_context = (
                    "(Live web search failed, so I could not check current "
                    "information. Answer from what I already know, and say "
                    "plainly at the start that I could not check live info, "
                    "so this may be out of date.)"
                )
            else:
                search_context = (
                    "(Live web search failed, so I could not check current "
                    "information. Answer from what I already know, and if "
                    "any part of the answer depends on current "
                    "information, say plainly that I could not check live "
                    "info.)"
                )
        if sources:
            lines = [
                f"- {s['title']} ({s['publisher']}): {s['snippet']} [{s['url']}]"
                for s in sources
            ]
            search_context = (
                "Use ONLY these web results to answer. Quote the key facts, "
                "dates, prices and specs from them, and say where sources "
                "disagree. Do not invent anything not in the results.\n"
                + "\n".join(lines)
            )

    person = _personality_dict(db, user_key)
    system = build_system_prompt(
        voice=person["voice"],
        warmth=person["warmth"],
        directness=person["directness"],
        humour=person["humour"],
        notes=person["notes"],
        profile=_profile_dict(db, user_key),
        memories=_recall_memories(db, user_key, text),
        soul=person["soul"],
    )
    history = [
        {
            "role": m.role if m.role in ("user", "assistant") else "assistant",
            # Strip the greeting from old assistant turns too: stored
            # replies that open with it would prime the model to greet
            # again on the next turn.
            "content": strip_greeting_prefix(m.content)
            if m.role != "user"
            else m.content,
        }
        for m in db.query(Message)
        .filter_by(user_key=user_key)
        .order_by(Message.created_at.desc())
        .limit(10)
        .all()[::-1]
    ]
    # The last history row is the user's message just stored; keep the
    # conversation short and append the search context as a user note.
    messages = [{"role": "system", "content": system}]
    if search_context:
        messages.append({"role": "user", "content": search_context})
    messages += history
    try:
        reply = llm_chat(messages)
    except AriesLLMError as exc:
        reply = str(exc)
    # Deterministic guard: the prompt says the greeting is one-time-only,
    # but the model still opens replies with it. Strip it here, before the
    # reply is stored or returned, so it can never open a reply.
    reply = strip_greeting_prefix(reply)
    db.add(Message(user_key=user_key, role="aries", content=reply, sources=_sources_json(sources)))
    db.commit()
    # Auto-learn: notice durable facts about the user in ordinary chat
    # and keep them (pending approval unless consent was given). Runs
    # after the reply is produced, so learning never delays the answer.
    auto_memory = learn_from_chat(
        db, user_key, text, reply,
        consent=bool(body.consent.get("save_memory")),
    )
    if auto_memory is not None:
        refresh_memory_file(db, user_key)
        if pending_memory is None:
            pending_memory = auto_memory
    return ChatOut(
        reply=reply,
        sources=sources,
        searched=searched,
        pending_memory=pending_memory,
        task_added=task_added,
    )


class ClearIn(BaseModel):
    consent: bool = False


@app.post("/api/chat/clear")
def clear_chat(body: ClearIn, db: Session = Depends(get_db), user_key: str = Depends(current_key)):
    try:
        require(db, user_key, "clear_chat", consent=body.consent)
    except PermissionDenied as exc:
        raise HTTPException(403, str(exc)) from exc
    db.query(Message).filter_by(user_key=user_key).delete()
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- memories


class MemoryIn(BaseModel):
    text: str
    category: str = "general"


@app.post("/api/memories")
def add_memory(body: MemoryIn, db: Session = Depends(get_db), user_key: str = Depends(current_key)):
    try:
        require(db, user_key, "save_memory", consent=True)
    except PermissionDenied as exc:
        raise HTTPException(403, str(exc)) from exc
    mem = Memory(user_key=user_key, text=body.text.strip(), category=body.category)
    db.add(mem)
    db.commit()
    refresh_memory_file(db, user_key)
    return {"ok": True, "id": mem.id}


@app.post("/api/memories/{memory_id}/approve")
def approve_memory(memory_id: int, db: Session = Depends(get_db), user_key: str = Depends(current_key)):
    mem = db.query(Memory).filter_by(id=memory_id, user_key=user_key).first()
    if not mem:
        raise HTTPException(404, "Memory not found")
    mem.pending = False
    db.commit()
    refresh_memory_file(db, user_key)
    return {"ok": True}


@app.post("/api/memories/{memory_id}/pin")
def pin_memory(memory_id: int, db: Session = Depends(get_db), user_key: str = Depends(current_key)):
    mem = db.query(Memory).filter_by(id=memory_id, user_key=user_key).first()
    if not mem:
        raise HTTPException(404, "Memory not found")
    mem.pinned = not mem.pinned
    db.commit()
    return {"ok": True, "pinned": mem.pinned}


@app.delete("/api/memories/{memory_id}")
def delete_memory(memory_id: int, db: Session = Depends(get_db), user_key: str = Depends(current_key)):
    try:
        require(db, user_key, "delete_memory", consent=True)
    except PermissionDenied as exc:
        raise HTTPException(403, str(exc)) from exc
    mem = db.query(Memory).filter_by(id=memory_id, user_key=user_key).first()
    if not mem:
        raise HTTPException(404, "Memory not found")
    db.delete(mem)
    db.commit()
    refresh_memory_file(db, user_key)
    return {"ok": True}


@app.post("/api/memories/import")
async def import_memories(
    file: UploadFile = File(...),
    consent: bool = Form(False),
    db: Session = Depends(get_db),
    user_key: str = Depends(current_key),
):
    """Import memories from a Markdown file into the signed-in user's vault.

    Format: "## Heading" lines set the category; "- " bullet lines under
    them become one memory each. Everything else is skipped. The file
    only ever lands in this account's vault — never shared, never in
    the repo. Importing needs the user's explicit consent in the UI.
    """
    try:
        require(db, user_key, "save_memory", consent=consent)
    except PermissionDenied as exc:
        raise HTTPException(403, str(exc)) from exc
    raw = await file.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(400, "That file is not readable text.") from None
    category = "general"
    imported = 0
    skipped = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("## "):
            category = stripped[3:].strip() or "general"
            continue
        if stripped.startswith("#") or stripped.startswith("<!--"):
            continue
        if stripped.startswith("- "):
            fact = stripped[2:].strip()
            if not fact or len(fact) > 1000:
                skipped += 1
                continue
            if imported >= 500:
                skipped += 1
                continue
            db.add(Memory(user_key=user_key, text=fact, category=category))
            imported += 1
        else:
            skipped += 1
    db.commit()
    refresh_memory_file(db, user_key)
    return {"ok": True, "imported": imported, "skipped": skipped}


@app.get("/api/memories/file")
def download_memory_file(db: Session = Depends(get_db), user_key: str = Depends(current_key)):
    """Download the signed-in user's own memory file (.md). The file
    is rendered from the vault, so it is always current — and it
    belongs to this account only, like the vault itself."""
    text = read_memory_file(db, user_key)
    if not text:
        raise HTTPException(404, "No memories yet — nothing to download.")
    return FileResponse(
        memory_file_path(user_key),
        media_type="text/markdown",
        filename="aries-memory.md",
    )


# ---------------------------------------------------------------- tasks


class TaskIn(BaseModel):
    title: str
    notes: str = ""
    due_at: str | None = None


@app.post("/api/tasks")
def add_task(body: TaskIn, db: Session = Depends(get_db), user_key: str = Depends(current_key)):
    due = None
    if body.due_at:
        try:
            due = datetime.fromisoformat(body.due_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(400, "Bad due_at format") from None
    task = Task(user_key=user_key, title=body.title.strip(), notes=body.notes, due_at=due)
    db.add(task)
    db.commit()
    return {"ok": True, "id": task.id}


@app.post("/api/tasks/{task_id}/done")
def done_task(task_id: int, db: Session = Depends(get_db), user_key: str = Depends(current_key)):
    task = db.query(Task).filter_by(id=task_id, user_key=user_key).first()
    if not task:
        raise HTTPException(404, "Task not found")
    task.done = not task.done
    db.commit()
    return {"ok": True, "done": task.done}


@app.post("/api/tasks/{task_id}/reminded")
def reminded_task(task_id: int, db: Session = Depends(get_db), user_key: str = Depends(current_key)):
    task = db.query(Task).filter_by(id=task_id, user_key=user_key).first()
    if not task:
        raise HTTPException(404, "Task not found")
    task.reminded = True
    db.commit()
    return {"ok": True}


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: int, db: Session = Depends(get_db), user_key: str = Depends(current_key)):
    task = db.query(Task).filter_by(id=task_id, user_key=user_key).first()
    if not task:
        raise HTTPException(404, "Task not found")
    db.delete(task)
    db.commit()
    return {"ok": True}


@app.get("/api/tasks/due")
def due_tasks(db: Session = Depends(get_db), user_key: str = Depends(current_key)):
    now = datetime.utcnow()
    rows = (
        db.query(Task)
        .filter_by(user_key=user_key, done=False, reminded=False)
        .filter(Task.due_at.isnot(None), Task.due_at <= now)
        .all()
    )
    return [
        {"id": t.id, "title": t.title, "due_at": t.due_at.isoformat()}
        for t in rows
    ]


# ---------------------------------------------------------------- permissions


@app.get("/api/permissions")
def list_permissions(db: Session = Depends(get_db), user_key: str = Depends(current_key)):
    return {cap: get_setting(db, user_key, cap) for cap in CAPABILITIES}


class PermissionIn(BaseModel):
    capability: str
    setting: str


@app.post("/api/permissions")
def save_permission(body: PermissionIn, db: Session = Depends(get_db), user_key: str = Depends(current_key)):
    try:
        setting = set_setting(db, user_key, body.capability, body.setting)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "capability": body.capability, "setting": setting}


# ---------------------------------------------------------------- issue reports

# Users report problems to the admin (Epsilon) from inside the app —
# no email, no outside service. A report carries the reporter's
# account, the time, and auto-attached context (engine, app version,
# screen) so nobody has to type it. Users only ever see their own
# reports; only the admin sees everyone's.


class ReportIn(BaseModel):
    title: str
    body: str
    context: dict = {}


def _report_dict(r: IssueReport, include_reporter: bool = False) -> dict:
    try:
        ctx = json.loads(r.context) if r.context else {}
    except Exception:
        ctx = {}
    if not isinstance(ctx, dict):
        ctx = {}
    out = {
        "id": r.id,
        "title": r.title,
        "body": r.body,
        "context": ctx,
        "status": r.status,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }
    if include_reporter:
        out["reporter"] = r.reporter_key
    return out


@app.post("/api/reports")
def submit_report(body: ReportIn, db: Session = Depends(get_db), user_key: str = Depends(current_key)):
    title = (body.title or "").strip()
    text = (body.body or "").strip()
    if not title:
        raise HTTPException(400, "Give the issue a short title.")
    if not text:
        raise HTTPException(400, "Say what happened.")
    if len(title) > 200:
        raise HTTPException(400, "Keep the title under 200 characters.")
    if len(text) > 5000:
        raise HTTPException(400, "Keep the description under 5000 characters.")
    ctx = dict(body.context or {})
    ctx.setdefault("app_version", app.version)
    rep = IssueReport(
        reporter_key=user_key,
        title=title,
        body=text,
        context=json.dumps(ctx)[:2000],
        status="open",
    )
    db.add(rep)
    db.commit()
    return {"ok": True, "report": _report_dict(rep)}


@app.get("/api/reports/mine")
def my_reports(db: Session = Depends(get_db), user_key: str = Depends(current_key)):
    rows = (
        db.query(IssueReport)
        .filter_by(reporter_key=user_key)
        .order_by(IssueReport.created_at.desc(), IssueReport.id.desc())
        .all()
    )
    return {"reports": [_report_dict(r) for r in rows]}


@app.get("/api/admin/reports")
def admin_list_reports(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = (
        db.query(IssueReport)
        .order_by(IssueReport.created_at.desc(), IssueReport.id.desc())
        .all()
    )
    return {"reports": [_report_dict(r, include_reporter=True) for r in rows]}


@app.post("/api/admin/reports/{report_id}/resolve")
def admin_resolve_report(
    report_id: int,
    status: str | None = Query(None),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    rep = db.query(IssueReport).filter_by(id=report_id).first()
    if not rep:
        raise HTTPException(404, "No such report.")
    if status is None:
        rep.status = "resolved" if rep.status == "open" else "open"
    elif status in ("open", "resolved"):
        rep.status = status
    else:
        raise HTTPException(400, "Status must be open or resolved.")
    db.commit()
    return {"ok": True, "report": _report_dict(rep, include_reporter=True)}


@app.delete("/api/admin/reports/{report_id}")
def admin_delete_report(
    report_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    rep = db.query(IssueReport).filter_by(id=report_id).first()
    if not rep:
        raise HTTPException(404, "No such report.")
    db.delete(rep)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- connections (Gmail / Spotify OAuth)

# Connect an account once in Settings → Connections; the skills that
# need it (Gmail reading, Spotify playback) use the stored tokens.
# Tokens are never returned to the frontend — /api/connections only
# reports whether a service is connected and which account it is.
# WhatsApp has no one-tap OAuth for a personal account, so it is listed
# with an honest note instead of a fake Connect button (see
# connections.status). The client IDs/secrets come from env vars only:
# GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET, SPOTIFY_CLIENT_ID /
# SPOTIFY_CLIENT_SECRET, plus APP_BASE_URL for the redirect URIs.


@app.get("/api/connections")
def list_connections(db: Session = Depends(get_db), user_key: str = Depends(current_key)):
    return conn_mod.status(db, user_key)


@app.get("/api/connections/{service}/connect")
def start_connect(service: str, request: Request, db: Session = Depends(get_db), user: User = Depends(get_connect_user)):
    if service not in ("google", "spotify"):
        raise HTTPException(400, "Unknown service")
    try:
        if service == "google":
            url = conn_mod.google_auth_url(db, request, user.username_lower)
        else:
            url = conn_mod.spotify_auth_url(db, request, user.username_lower)
    except conn_mod.NotConfigured as exc:
        raise HTTPException(503, str(exc)) from exc
    return RedirectResponse(url, status_code=302)


def _oauth_result_page(title: str, body: str, ok: bool) -> HTMLResponse:
    # Opened in its own tab from Settings. Tells the user what happened,
    # then tries to close itself (works when opened via window.open).
    colour = "#b78bff" if ok else "#ff9d9d"
    return HTMLResponse(
        f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>{title}</title>
<style>body{{background:#0d0010;color:#f6ecd9;font-family:system-ui,sans-serif;
display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}}
main{{text-align:center;max-width:420px;padding:2rem}}
h1{{color:{colour};font-size:1.25rem}}p{{opacity:.8;line-height:1.6}}</style>
</head><body><main><h1>{title}</h1><p>{body}</p>
<p>You can close this tab and go back to Aries.</p></main>
<script>setTimeout(function(){{ try {{ window.close(); }} catch (e) {{}} }}, 1800);</script>
</body></html>"""
    )


@app.get("/api/connections/{service}/callback")
def oauth_callback(
    service: str,
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    # NOTE: no auth dependency here on purpose — the provider redirects
    # the browser back with no Bearer token. The user is recovered
    # from the OAuth state row created at connect time.
    if service not in ("google", "spotify"):
        raise HTTPException(400, "Unknown service")
    name = "Gmail" if service == "google" else "Spotify"
    if error:
        return _oauth_result_page(
            f"{name} not connected",
            "You declined the sign-in, or it was cancelled. Nothing was saved. "
            "You can try again from Settings → Connections.",
            ok=False,
        )
    if not code or not state:
        return _oauth_result_page(
            f"{name} not connected",
            "The sign-in came back without the details Aries needs. Try again from Settings → Connections.",
            ok=False,
        )
    try:
        if service == "google":
            label = conn_mod.handle_google_callback(db, request, code, state)
        else:
            label = conn_mod.handle_spotify_callback(db, request, code, state)
    except conn_mod.NotConfigured as exc:
        return _oauth_result_page(f"{name} not connected", str(exc), ok=False)
    except conn_mod.ConnectionError as exc:
        return _oauth_result_page(f"{name} not connected", str(exc), ok=False)
    return _oauth_result_page(
        f"{name} connected ✓",
        f"Connected as {label}. Aries can now use your {name} account for its skills. "
        "Go back to Aries and reopen Settings → Connections to see it.",
        ok=True,
    )


@app.post("/api/connections/{service}/disconnect")
def disconnect_connection(service: str, db: Session = Depends(get_db), user_key: str = Depends(current_key)):
    if service not in ("google", "spotify"):
        raise HTTPException(400, "Unknown service")
    conn_mod.disconnect(db, user_key, service)
    return {"ok": True, "service": service}


# ---------------------------------------------------------------- face login


@app.post("/api/face/enroll")
async def enroll_face(
    file: UploadFile = File(...),
    name: str = Form(""),
    pin: str = Form(""),
    db: Session = Depends(get_db),
    user_key: str = Depends(current_key),
):
    data = await file.read()
    try:
        result = face_mod.enroll(user_key, data)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "Enrollment failed"))
    row = db.query(FaceEnrollment).filter_by(user_key=user_key).first()
    if not row:
        row = FaceEnrollment(user_key=user_key)
        db.add(row)
    row.name = name or row.name
    if pin:
        row.pin_hash = face_mod.pin_hash(pin)
    db.commit()
    set_setting(db, user_key, "face_login", "always")
    return {"ok": True}


@app.post("/api/face/verify")
async def verify_face(file: UploadFile = File(...), db: Session = Depends(get_db), user_key: str = Depends(current_key)):
    data = await file.read()
    try:
        result = face_mod.verify(user_key, data)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "Verification failed"))
    return result


@app.post("/api/face/remove")
def remove_face(db: Session = Depends(get_db), user_key: str = Depends(current_key)):
    face_mod.remove(user_key)
    db.query(FaceEnrollment).filter_by(user_key=user_key).delete()
    set_setting(db, user_key, "face_login", "ask")
    db.commit()
    return {"ok": True}


class PinIn(BaseModel):
    pin: str


@app.post("/api/face/set-pin")
def set_pin(body: PinIn, db: Session = Depends(get_db), user_key: str = Depends(current_key)):
    row = db.query(FaceEnrollment).filter_by(user_key=user_key).first()
    if not row:
        row = FaceEnrollment(user_key=user_key)
        db.add(row)
    row.pin_hash = face_mod.pin_hash(body.pin)
    db.commit()
    return {"ok": True}


@app.post("/api/face/unlock-pin")
def unlock_pin(body: PinIn, db: Session = Depends(get_db), user_key: str = Depends(current_key)):
    row = db.query(FaceEnrollment).filter_by(user_key=user_key).first()
    if not row or not row.pin_hash:
        raise HTTPException(400, "No PIN set")
    return {"ok": row.pin_hash == face_mod.pin_hash(body.pin)}


# ---------------------------------------------------------------- screenshot


@app.post("/api/screenshot")
async def screenshot(
    file: UploadFile = File(...),
    question: str = Form(""),
    consent: bool = Form(False),
    db: Session = Depends(get_db),
    user_key: str = Depends(current_key),
):
    try:
        require(db, user_key, "screenshot", consent=consent)
    except PermissionDenied as exc:
        raise HTTPException(403, str(exc)) from exc
    data = await file.read()
    try:
        img = Image.open(io.BytesIO(data))
        img.verify()
        width, height = Image.open(io.BytesIO(data)).size
    except Exception:
        raise HTTPException(400, "That file is not a readable image.") from None
    try:
        description = describe_image(data, file.content_type or "image/png", question)
    except AriesLLMError as exc:
        description = (
            f"{exc}\n\nWhat I can tell you without the vision model: "
            f"the image is {width}x{height} px ({file.content_type})."
        )
    shot = Screenshot(
        user_key=user_key,
        filename=file.filename or "screenshot",
        mime=file.content_type or "",
        width=width,
        height=height,
        description=description,
    )
    db.add(shot)
    db.commit()
    return {"ok": True, "id": shot.id, "description": description}


# ---------------------------------------------------------------- frontend (production: one service)

DIST = os.path.join(os.path.dirname(__file__), "..", "static")
DIST = os.path.normpath(DIST)
if os.path.isdir(DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(DIST, "assets")), name="assets")

    @app.get("/{path:path}")
    def serve_frontend(path: str):
        # API routes are registered above and take precedence.
        candidate = os.path.join(DIST, path)
        if path and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(DIST, "index.html"))
