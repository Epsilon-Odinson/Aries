"""Database layer for the Aries production backend.

SQLite by default (local dev and single-service Render deploys).
Set ``DATABASE_URL`` to a Postgres URL (Render Postgres) to switch —
``postgres://`` is normalised to ``postgresql://`` for SQLAlchemy.
No secrets are stored here; the URL comes from the environment only.
"""

from __future__ import annotations

import os
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

if DATABASE_URL:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
else:
    # SQLite file lives next to the server code; Render's free web
    # service disk is ephemeral, so attach Render Postgres (DATABASE_URL)
    # for durable multi-user data. See README.md.
    os.makedirs("data", exist_ok=True)
    engine = create_engine(
        "sqlite:///data/aries.db", connect_args={"check_same_thread": False}
    )

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class User(Base):
    """One Aries account.

    Admin is NOT by creation order: the admin is whoever holds the
    admin username (env ARIES_ADMIN_USERNAME, default "epsilon") —
    see auth.py. ``username`` keeps the casing the user typed;
    ``username_lower`` is the canonical key (unique) used for
    lookups and as the ``user_key`` on every data row, so "Epsilon"
    and "epsilon" are the same account.
    """

    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(64), nullable=False)
    username_lower = Column(String(64), unique=True, nullable=False)
    password_hash = Column(String(128), nullable=False)  # bcrypt hash, never plaintext
    role = Column(String(16), default="user")  # admin | user (derived from the username; see auth.py)
    created_at = Column(DateTime, default=datetime.utcnow)


class Session(Base):
    """A login session: a random token handed to the frontend as a
    Bearer token (kept in localStorage). The server checks it on every
    request; expiry is 30 days. Logout deletes the row."""

    __tablename__ = "sessions"
    id = Column(Integer, primary_key=True)
    token = Column(String(128), unique=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)


class Profile(Base):
    __tablename__ = "profiles"
    id = Column(Integer, primary_key=True)
    user_key = Column(String(64), unique=True, nullable=False, default="default")
    name = Column(String(128), default="")
    call_name = Column(String(128), default="")
    about = Column(Text, default="")
    updated_at = Column(DateTime, default=datetime.utcnow)


class Personality(Base):
    __tablename__ = "personality"
    id = Column(Integer, primary_key=True)
    user_key = Column(String(64), unique=True, nullable=False, default="default")
    voice = Column(String(32), default="companion")  # companion|mentor|hype|calm
    warmth = Column(Integer, default=70)
    directness = Column(Integer, default=60)
    humour = Column(Integer, default=50)
    notes = Column(Text, default="")
    # The user's own character for Aries ("soul"). When set, it replaces
    # the shared SOUL.md default for this account only; empty means the
    # default. Every account gets its own Aries this way.
    soul = Column(Text, default="")
    updated_at = Column(DateTime, default=datetime.utcnow)


class Memory(Base):
    __tablename__ = "memories"
    id = Column(Integer, primary_key=True)
    user_key = Column(String(64), nullable=False, default="default")
    text = Column(Text, nullable=False)
    category = Column(String(64), default="general")
    pinned = Column(Boolean, default=False)
    pending = Column(Boolean, default=False)  # learned in chat, awaiting approval
    created_at = Column(DateTime, default=datetime.utcnow)


class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True)
    user_key = Column(String(64), nullable=False, default="default")
    role = Column(String(16), nullable=False)  # user | aries
    content = Column(Text, nullable=False)
    sources = Column(Text, default="")  # JSON list of {title, url, publisher}
    created_at = Column(DateTime, default=datetime.utcnow)


class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True)
    user_key = Column(String(64), nullable=False, default="default")
    title = Column(String(256), nullable=False)
    notes = Column(Text, default="")
    due_at = Column(DateTime, nullable=True)
    done = Column(Boolean, default=False)
    reminded = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Permission(Base):
    __tablename__ = "permissions"
    id = Column(Integer, primary_key=True)
    user_key = Column(String(64), nullable=False, default="default")
    capability = Column(String(64), nullable=False)  # microphone|web_search|save_memory|delete_memory|clear_chat|screenshot|face_login
    setting = Column(String(16), default="ask")  # ask|always|never
    updated_at = Column(DateTime, default=datetime.utcnow)


class StudyMaterial(Base):
    """One study file a user uploaded (slides, handout, notes).

    The original file is kept under data/study/<user_key>/ (same
    pattern as face crops); the extracted plain text lives here in
    the database, so teaching from it still works even though the
    server disk is ephemeral. Only the signed-in account ever sees
    its own rows.
    """

    __tablename__ = "study_materials"
    id = Column(Integer, primary_key=True)
    user_key = Column(String(64), nullable=False, default="default")
    filename = Column(String(256), default="")  # name the user uploaded
    stored_name = Column(String(256), default="")  # file on disk, if kept
    kind = Column(String(16), default="")  # pptx | pdf | txt | md
    text = Column(Text, default="")  # extracted plain text
    created_at = Column(DateTime, default=datetime.utcnow)


class StudySet(Base):
    """A set of Anki cards Aries made for a user (a downloadable .tsv).

    The file lives under data/study/sets/<user_key>/; the row is the
    record of it, so the Study tab can list downloads per account.
    """

    __tablename__ = "study_sets"
    id = Column(Integer, primary_key=True)
    user_key = Column(String(64), nullable=False, default="default")
    name = Column(String(256), default="")  # e.g. "Cell structure"
    filename = Column(String(256), default="")  # file on disk
    card_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


class ExamCountdown(Base):
    """An exam a user is counting down to. Days left is computed
    from exam_date at read time, so nothing ever goes stale."""

    __tablename__ = "exam_countdowns"
    id = Column(Integer, primary_key=True)
    user_key = Column(String(64), nullable=False, default="default")
    name = Column(String(256), default="")  # e.g. "Anatomy paper 1"
    subject = Column(String(128), default="")
    exam_date = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class QuizSession(Base):
    """One quiz in progress for a user. Only ever started when the
    user asks to be quizzed — never on its own. ``current_answer``
    is the expected answer to ``current_question`` (kept here, never
    shown to the user); ``score``/``total`` are the running tally."""

    __tablename__ = "quiz_sessions"
    id = Column(Integer, primary_key=True)
    user_key = Column(String(64), nullable=False, default="default")
    topic = Column(String(256), default="")
    material_id = Column(Integer, nullable=True)
    score = Column(Integer, default=0)
    total = Column(Integer, default=0)
    current_question = Column(Text, default="")
    current_answer = Column(Text, default="")
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class IssueReport(Base):
    """A problem a user reported to the admin, from inside the app.

    No email, no outside service. ``context`` is JSON with the
    auto-attached details the reporter never had to type (engine,
    app version, screen). Users only ever see their own reports;
    only the admin sees everyone's. Status is open or resolved.
    """

    __tablename__ = "issue_reports"
    id = Column(Integer, primary_key=True)
    reporter_key = Column(String(64), nullable=False, default="default")
    title = Column(String(256), nullable=False)
    body = Column(Text, nullable=False)
    context = Column(Text, default="")  # JSON: {engine, app_version, screen}
    status = Column(String(16), default="open")  # open | resolved
    created_at = Column(DateTime, default=datetime.utcnow)


class FaceEnrollment(Base):
    __tablename__ = "face_enrollments"
    id = Column(Integer, primary_key=True)
    user_key = Column(String(64), unique=True, nullable=False, default="default")
    name = Column(String(128), default="")
    # LBPH model + face crop stored as files under data/faces/<user_key>/
    # (kept out of the DB; see face.py). Only metadata lives here.
    enrolled_at = Column(DateTime, default=datetime.utcnow)
    pin_hash = Column(String(128), default="")  # SHA-256 of unlock PIN, never the PIN


class Screenshot(Base):
    __tablename__ = "screenshots"
    id = Column(Integer, primary_key=True)
    user_key = Column(String(64), nullable=False, default="default")
    filename = Column(String(256), default="")
    mime = Column(String(64), default="")
    width = Column(Integer, default=0)
    height = Column(Integer, default=0)
    description = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)


class Connection(Base):
    """One connected third-party account (Gmail / Spotify).

    OAuth tokens live here, in the private database — never in the repo,
    never logged, never sent to the frontend. The API only reports
    whether a service is connected and which account it is.
    One row per (user, service).
    """

    __tablename__ = "connections"
    __table_args__ = (UniqueConstraint("user_key", "service"),)
    id = Column(Integer, primary_key=True)
    user_key = Column(String(64), nullable=False, default="default")
    service = Column(String(32), nullable=False)  # google | spotify
    account_label = Column(String(256), default="")  # email or display name
    access_token = Column(Text, default="")
    refresh_token = Column(Text, default="")
    token_type = Column(String(32), default="Bearer")
    expires_at = Column(DateTime, nullable=True)
    scope = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class OAuthState(Base):
    """Short-lived OAuth `state` values (CSRF protection).

    Created when a connect flow starts, consumed (deleted) on the
    callback. Rows older than 15 minutes are stale and ignored.
    """

    __tablename__ = "oauth_states"
    id = Column(Integer, primary_key=True)
    service = Column(String(32), nullable=False)  # google | spotify
    state = Column(String(128), unique=True, nullable=False)
    redirect_uri = Column(Text, default="")  # must match the token exchange
    # Which account started this connect flow. The OAuth callback is a
    # plain browser redirect (no Bearer token), so the state row is
    # what ties the returning tokens to the right user.
    user_key = Column(String(64), default="")
    created_at = Column(DateTime, default=datetime.utcnow)


def _ensure_personality_soul_column() -> None:
    """Startup migration: add personality.soul if the table predates it.

    ``Base.metadata.create_all`` only creates missing tables — it never
    adds a column to an existing table. A database built by an older
    build would otherwise lack the column and every personality read
    would fail. ``ALTER TABLE ... ADD COLUMN`` with no default is the
    same statement on SQLite and Postgres, and inspecting first makes
    this a no-op once the column exists (fresh databases already have
    it from create_all). Existing rows are untouched: the column is
    nullable and reads as empty, i.e. the shared default character.
    """
    from sqlalchemy import inspect, text

    columns = {c["name"] for c in inspect(engine).get_columns("personality")}
    if "soul" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE personality ADD COLUMN soul TEXT"))


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _ensure_personality_soul_column()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
