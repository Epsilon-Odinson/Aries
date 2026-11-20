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


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
