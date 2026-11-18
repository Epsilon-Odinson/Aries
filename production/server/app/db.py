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


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
