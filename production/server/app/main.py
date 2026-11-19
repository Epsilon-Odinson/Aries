"""Aries production backend — FastAPI.

Single service: serves the JSON API under /api and the built Vite
frontend as static files, so Render runs one web service and there
is no CORS problem in production.

Security rules (from the product decisions):
- The NVIDIA API key lives ONLY in the NVIDIA_API_KEY env var on the
  server. It is never accepted from, or sent to, any client.
- Permissions are enforced here, server-side; the UI is only a prompt.
- Aries is private to Epsilon and a friend until it goes commercial.
  This build ships no public sign-up; add accounts before opening it.
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

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from . import face as face_mod
from . import voice as voice_mod
from .db import (
    FaceEnrollment,
    Memory,
    Message,
    Permission,
    Personality,
    Profile,
    Screenshot,
    Task,
    get_db,
    init_db,
)
from .llm import AriesLLMError, chat as llm_chat, describe_image
from .permissions import (
    CAPABILITIES,
    PermissionDenied,
    get_setting,
    require,
    set_setting,
)
from .personality import MEMORY_TEXT, build_system_prompt, strip_greeting_prefix
from .search import looks_like_current_question, search_web

app = FastAPI(title="Aries", version="0.4.0")

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

USER = "default"  # Single-household build: Epsilon + a friend. Accounts land with commercial launch.

# ---------------------------------------------------------------- health


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "service": "aries",
        "version": "0.4.0",
        "key_configured": bool(os.getenv("NVIDIA_API_KEY")),
        "vision_configured": bool(os.getenv("VISION_MODEL")),
        "elevenlabs_configured": voice_mod.configured(),
        "db": "postgres" if os.getenv("DATABASE_URL") else "sqlite",
    }


# ---------------------------------------------------------------- voice (ElevenLabs)

# Talk-back speaks with a real narrator voice when the server has an
# ELEVENLABS_API_KEY. No key -> clean 503, and the frontend falls back
# to the device's built-in voices. The key is never accepted from, or
# sent to, any client.


class TTSIn(BaseModel):
    text: str
    voice_id: str | None = None


@app.post("/api/tts")
def tts(body: TTSIn):
    try:
        audio = voice_mod.synthesize(body.text, body.voice_id)
    except voice_mod.VoiceNotConfigured:
        raise HTTPException(503, "voice service not configured") from None
    except voice_mod.VoiceError as exc:
        raise HTTPException(502, str(exc)) from exc
    return Response(content=audio, media_type="audio/mpeg")


@app.get("/api/voices")
def voices():
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


def _profile_dict(db: Session) -> dict:
    row = db.query(Profile).filter_by(user_key=USER).first()
    if not row:
        return {"name": "", "call_name": "", "about": ""}
    return {"name": row.name, "call_name": row.call_name, "about": row.about}


def _personality_row(db: Session) -> Personality:
    row = db.query(Personality).filter_by(user_key=USER).first()
    if not row:
        row = Personality(user_key=USER)
        db.add(row)
        db.commit()
    return row


def _personality_dict(db: Session) -> dict:
    row = _personality_row(db)
    return {
        "voice": row.voice,
        "warmth": row.warmth,
        "directness": row.directness,
        "humour": row.humour,
        "notes": row.notes,
    }


def _memories(db: Session, include_pending: bool = False) -> list[Memory]:
    q = db.query(Memory).filter_by(user_key=USER)
    if not include_pending:
        q = q.filter_by(pending=False)
    return q.order_by(Memory.pinned.desc(), Memory.created_at.desc()).all()


def _recall_memories(db: Session, text: str, limit: int = 5) -> list[str]:
    """Simple keyword recall: memories sharing a word with the message."""
    words = {w for w in re.findall(r"[a-z]{4,}", text.lower())}
    picked = []
    for mem in _memories(db):
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
def state(db: Session = Depends(get_db)):
    person = _personality_dict(db)
    face = db.query(FaceEnrollment).filter_by(user_key=USER).first()
    return {
        "profile": _profile_dict(db),
        "personality": person,
        "memory_file": MEMORY_TEXT,
        "memories": [
            {
                "id": m.id,
                "text": m.text,
                "category": m.category,
                "pinned": m.pinned,
                "pending": m.pending,
            }
            for m in _memories(db, include_pending=True)
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
            .filter_by(user_key=USER)
            .order_by(Task.due_at.asc().nullslast(), Task.created_at.desc())
            .all()
        ],
        "permissions": {
            cap: get_setting(db, USER, cap) for cap in CAPABILITIES
        },
        "face": {
            "enrolled": bool(face),
            "login_enabled": get_setting(db, USER, "face_login") == "always",
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
            .filter_by(user_key=USER)
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
def save_profile(body: ProfileIn, db: Session = Depends(get_db)):
    row = db.query(Profile).filter_by(user_key=USER).first()
    if not row:
        row = Profile(user_key=USER)
        db.add(row)
    row.name, row.call_name, row.about = body.name, body.call_name, body.about
    row.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "profile": _profile_dict(db)}


class PersonalityIn(BaseModel):
    voice: str = "companion"
    warmth: int = Field(70, ge=0, le=100)
    directness: int = Field(60, ge=0, le=100)
    humour: int = Field(50, ge=0, le=100)
    notes: str = ""


@app.post("/api/personality")
def save_personality(body: PersonalityIn, db: Session = Depends(get_db)):
    row = _personality_row(db)
    if body.voice not in ("companion", "mentor", "hype", "calm"):
        raise HTTPException(400, "Unknown voice")
    row.voice, row.warmth = body.voice, body.warmth
    row.directness, row.humour, row.notes = (
        body.directness,
        body.humour,
        body.notes,
    )
    row.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "personality": _personality_dict(db)}


# ---------------------------------------------------------------- chat


class ChatIn(BaseModel):
    message: str
    use_search: bool = False
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


@app.post("/api/chat", response_model=ChatOut)
def chat_endpoint(body: ChatIn, db: Session = Depends(get_db)):
    text = body.message.strip()
    if not text:
        raise HTTPException(400, "Empty message")
    db.add(Message(user_key=USER, role="user", content=text))
    db.commit()

    # Tasks: "remind me to ..." saves a task, no LLM call needed.
    reminder = _parse_reminder(text)
    task_added = None
    if reminder:
        task = Task(
            user_key=USER,
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
        db.add(Message(user_key=USER, role="aries", content=reply))
        db.commit()
        return ChatOut(reply=reply, task_added=task_added)

    # Memory learning: explicit facts are held pending approval.
    pending_memory = None
    fact = _learnable_fact(text)
    if fact:
        try:
            require(db, USER, "save_memory", consent=bool(body.consent.get("save_memory")))
            mem = Memory(user_key=USER, text=fact, pending=False)
            db.add(mem)
            db.commit()
            pending_memory = {"id": mem.id, "text": fact, "saved": True}
        except PermissionDenied:
            if get_setting(db, USER, "save_memory") == "never":
                pending_memory = None
            else:
                mem = Memory(user_key=USER, text=fact, pending=True)
                db.add(mem)
                db.commit()
                pending_memory = {"id": mem.id, "text": fact, "saved": False}

    # Web search: explicit toggle, or a question that needs fresh info.
    want_search = body.use_search or looks_like_current_question(text)
    sources: list[dict] = []
    searched = False
    search_context = ""
    if want_search:
        try:
            require(
                db,
                USER,
                "web_search",
                consent=bool(body.consent.get("web_search")),
            )
        except PermissionDenied as exc:
            db.add(
                Message(
                    user_key=USER,
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
        except Exception as exc:
            sources = []
            search_context = f"(Web search failed: {exc}. Answer from training only and say so.)"
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

    person = _personality_dict(db)
    system = build_system_prompt(
        voice=person["voice"],
        warmth=person["warmth"],
        directness=person["directness"],
        humour=person["humour"],
        notes=person["notes"],
        profile=_profile_dict(db),
        memories=_recall_memories(db, text),
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
        .filter_by(user_key=USER)
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
    db.add(Message(user_key=USER, role="aries", content=reply, sources=_sources_json(sources)))
    db.commit()
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
def clear_chat(body: ClearIn, db: Session = Depends(get_db)):
    try:
        require(db, USER, "clear_chat", consent=body.consent)
    except PermissionDenied as exc:
        raise HTTPException(403, str(exc)) from exc
    db.query(Message).filter_by(user_key=USER).delete()
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- memories


class MemoryIn(BaseModel):
    text: str
    category: str = "general"


@app.post("/api/memories")
def add_memory(body: MemoryIn, db: Session = Depends(get_db)):
    try:
        require(db, USER, "save_memory", consent=True)
    except PermissionDenied as exc:
        raise HTTPException(403, str(exc)) from exc
    mem = Memory(user_key=USER, text=body.text.strip(), category=body.category)
    db.add(mem)
    db.commit()
    return {"ok": True, "id": mem.id}


@app.post("/api/memories/{memory_id}/approve")
def approve_memory(memory_id: int, db: Session = Depends(get_db)):
    mem = db.query(Memory).filter_by(id=memory_id, user_key=USER).first()
    if not mem:
        raise HTTPException(404, "Memory not found")
    mem.pending = False
    db.commit()
    return {"ok": True}


@app.post("/api/memories/{memory_id}/pin")
def pin_memory(memory_id: int, db: Session = Depends(get_db)):
    mem = db.query(Memory).filter_by(id=memory_id, user_key=USER).first()
    if not mem:
        raise HTTPException(404, "Memory not found")
    mem.pinned = not mem.pinned
    db.commit()
    return {"ok": True, "pinned": mem.pinned}


@app.delete("/api/memories/{memory_id}")
def delete_memory(memory_id: int, db: Session = Depends(get_db)):
    try:
        require(db, USER, "delete_memory", consent=True)
    except PermissionDenied as exc:
        raise HTTPException(403, str(exc)) from exc
    mem = db.query(Memory).filter_by(id=memory_id, user_key=USER).first()
    if not mem:
        raise HTTPException(404, "Memory not found")
    db.delete(mem)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- tasks


class TaskIn(BaseModel):
    title: str
    notes: str = ""
    due_at: str | None = None


@app.post("/api/tasks")
def add_task(body: TaskIn, db: Session = Depends(get_db)):
    due = None
    if body.due_at:
        try:
            due = datetime.fromisoformat(body.due_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(400, "Bad due_at format") from None
    task = Task(user_key=USER, title=body.title.strip(), notes=body.notes, due_at=due)
    db.add(task)
    db.commit()
    return {"ok": True, "id": task.id}


@app.post("/api/tasks/{task_id}/done")
def done_task(task_id: int, db: Session = Depends(get_db)):
    task = db.query(Task).filter_by(id=task_id, user_key=USER).first()
    if not task:
        raise HTTPException(404, "Task not found")
    task.done = not task.done
    db.commit()
    return {"ok": True, "done": task.done}


@app.post("/api/tasks/{task_id}/reminded")
def reminded_task(task_id: int, db: Session = Depends(get_db)):
    task = db.query(Task).filter_by(id=task_id, user_key=USER).first()
    if not task:
        raise HTTPException(404, "Task not found")
    task.reminded = True
    db.commit()
    return {"ok": True}


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: int, db: Session = Depends(get_db)):
    task = db.query(Task).filter_by(id=task_id, user_key=USER).first()
    if not task:
        raise HTTPException(404, "Task not found")
    db.delete(task)
    db.commit()
    return {"ok": True}


@app.get("/api/tasks/due")
def due_tasks(db: Session = Depends(get_db)):
    now = datetime.utcnow()
    rows = (
        db.query(Task)
        .filter_by(user_key=USER, done=False, reminded=False)
        .filter(Task.due_at.isnot(None), Task.due_at <= now)
        .all()
    )
    return [
        {"id": t.id, "title": t.title, "due_at": t.due_at.isoformat()}
        for t in rows
    ]


# ---------------------------------------------------------------- permissions


@app.get("/api/permissions")
def list_permissions(db: Session = Depends(get_db)):
    return {cap: get_setting(db, USER, cap) for cap in CAPABILITIES}


class PermissionIn(BaseModel):
    capability: str
    setting: str


@app.post("/api/permissions")
def save_permission(body: PermissionIn, db: Session = Depends(get_db)):
    try:
        setting = set_setting(db, USER, body.capability, body.setting)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "capability": body.capability, "setting": setting}


# ---------------------------------------------------------------- face login


@app.post("/api/face/enroll")
async def enroll_face(
    file: UploadFile = File(...),
    name: str = Form(""),
    pin: str = Form(""),
    db: Session = Depends(get_db),
):
    data = await file.read()
    try:
        result = face_mod.enroll(USER, data)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "Enrollment failed"))
    row = db.query(FaceEnrollment).filter_by(user_key=USER).first()
    if not row:
        row = FaceEnrollment(user_key=USER)
        db.add(row)
    row.name = name or row.name
    if pin:
        row.pin_hash = face_mod.pin_hash(pin)
    db.commit()
    set_setting(db, USER, "face_login", "always")
    return {"ok": True}


@app.post("/api/face/verify")
async def verify_face(file: UploadFile = File(...), db: Session = Depends(get_db)):
    data = await file.read()
    try:
        result = face_mod.verify(USER, data)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "Verification failed"))
    return result


@app.post("/api/face/remove")
def remove_face(db: Session = Depends(get_db)):
    face_mod.remove(USER)
    db.query(FaceEnrollment).filter_by(user_key=USER).delete()
    set_setting(db, USER, "face_login", "ask")
    db.commit()
    return {"ok": True}


class PinIn(BaseModel):
    pin: str


@app.post("/api/face/set-pin")
def set_pin(body: PinIn, db: Session = Depends(get_db)):
    row = db.query(FaceEnrollment).filter_by(user_key=USER).first()
    if not row:
        row = FaceEnrollment(user_key=USER)
        db.add(row)
    row.pin_hash = face_mod.pin_hash(body.pin)
    db.commit()
    return {"ok": True}


@app.post("/api/face/unlock-pin")
def unlock_pin(body: PinIn, db: Session = Depends(get_db)):
    row = db.query(FaceEnrollment).filter_by(user_key=USER).first()
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
):
    try:
        require(db, USER, "screenshot", consent=consent)
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
        user_key=USER,
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
