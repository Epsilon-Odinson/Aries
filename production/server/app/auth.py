"""Accounts for Aries — login, sessions, and who is admin.

Design (decided by Epsilon, 2026-11-20 — this replaces an earlier
"first account is admin" idea, which is NOT the rule):

- Admin status belongs to ONE username, not to creation order.
  The admin username is ``ARIES_ADMIN_USERNAME`` (default
  "epsilon"), compared case-insensitively. Whoever holds that
  username is admin on EVERY login — first, last, or middle.
  If Kelfy (or anyone else) signs up first, they are a normal user;
  when Epsilon signs up later as "Epsilon", he is admin.
- The admin username can only be held by one account (usernames are
  unique case-insensitively), so nobody else can take it.
- Every account gets its own chat, memories, tasks, profile,
  personality, permissions, face enrollment and connections. Rows
  are keyed by the canonical lowercase username (``user_key``).
- Passwords are bcrypt hashes only — never plaintext, never logged.
- Sessions are random tokens stored server-side (``sessions`` table),
  sent as ``Authorization: Bearer <token>`` and kept by the frontend
  in localStorage. They expire after 30 days; logout deletes the row.

Migration: before accounts existed, every row belonged to
``user_key = "default"`` — that was Epsilon's data. The first time
the ADMIN account registers or logs in, those rows are claimed for
the admin username (``claim_default_data``). It is idempotent: once
claimed there are no "default" rows left, so later logins are a
no-op. Kelfy registering first claims nothing — the data waits for
Epsilon's account.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
from datetime import datetime, timedelta

import bcrypt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .db import (
    Connection,
    FaceEnrollment,
    Memory,
    Message,
    OAuthState,
    Permission,
    Personality,
    Profile,
    Screenshot,
    Task,
    User,
    Session as DbSession,
    get_db,
)

SESSION_DAYS = 30
MIN_PASSWORD_LEN = 6
_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,31}$")

# Rows that belonged to the pre-accounts household build carry this key.
LEGACY_USER_KEY = "default"


def admin_username() -> str:
    """The admin username, canonical (lowercase). Env-configurable."""
    return os.getenv("ARIES_ADMIN_USERNAME", "epsilon").strip().lower() or "epsilon"


def canonical(username: str) -> str:
    return (username or "").strip().lower()


def is_admin_username(username: str) -> bool:
    return canonical(username) == admin_username()


def user_key(user: User) -> str:
    """The key stamped on a user's data rows: canonical username."""
    return user.username_lower


# ---------------------------------------------------------------- passwords


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def validate_username(username: str) -> str:
    """Return the canonical username, or raise ValueError."""
    name = (username or "").strip()
    if not (2 <= len(name) <= 32):
        raise ValueError("Username must be 2–32 characters.")
    if not _USERNAME_RE.match(name.lower()):
        raise ValueError(
            "Username can only use letters, digits, dot, dash and underscore, "
            "and must start with a letter or digit."
        )
    return name.lower()


def validate_password(password: str) -> None:
    if len(password or "") < MIN_PASSWORD_LEN:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LEN} characters.")


# ---------------------------------------------------------------- users


def get_user_by_name(db: Session, username: str) -> User | None:
    return db.query(User).filter_by(username_lower=canonical(username)).first()


def create_user(db: Session, username: str, password: str) -> User:
    """Register an account. Role follows the username, not the order:
    the admin username is admin whenever it registers."""
    key = validate_username(username)
    validate_password(password)
    if get_user_by_name(db, key):
        raise ValueError("That username is already taken.")
    user = User(
        username=username.strip(),
        username_lower=key,
        password_hash=hash_password(password),
        role="admin" if is_admin_username(key) else "user",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    # The admin's first registration claims the pre-accounts data.
    if user.role == "admin":
        claim_default_data(db, user)
    return user


def authenticate(db: Session, username: str, password: str) -> User | None:
    user = get_user_by_name(db, username)
    if not user or not verify_password(password, user.password_hash):
        return None
    # The admin rule is by username on every login: if the env var
    # changed, the stored role follows it.
    want_role = "admin" if is_admin_username(user.username_lower) else "user"
    if user.role != want_role:
        user.role = want_role
        db.commit()
    # The admin's login claims the pre-accounts data if it is still
    # unclaimed (e.g. the account was created before this code shipped).
    if user.role == "admin":
        claim_default_data(db, user)
    return user


# ---------------------------------------------------------------- sessions


def create_session(db: Session, user: User) -> str:
    token = secrets.token_urlsafe(32)
    db.add(
        DbSession(
            token=token,
            user_id=user.id,
            expires_at=datetime.utcnow() + timedelta(days=SESSION_DAYS),
        )
    )
    db.commit()
    return token


def user_for_token(db: Session, token: str) -> User | None:
    if not token:
        return None
    row = db.query(DbSession).filter_by(token=token).first()
    if not row or row.expires_at < datetime.utcnow():
        if row:
            db.delete(row)
            db.commit()
        return None
    return db.query(User).filter_by(id=row.user_id).first()


def delete_session(db: Session, token: str) -> None:
    db.query(DbSession).filter_by(token=token).delete()
    db.commit()


def delete_all_sessions(db: Session, user: User) -> None:
    db.query(DbSession).filter_by(user_id=user.id).delete()
    db.commit()


# ---------------------------------------------------------------- FastAPI dependencies


_bearer = HTTPBearer(auto_error=False)


def _token_from_credentials(
    creds: HTTPAuthorizationCredentials | None,
) -> str | None:
    if creds and creds.scheme.lower() == "bearer" and creds.credentials:
        return creds.credentials
    return None


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    """Dependency: the caller must present a live session token.

    Every data endpoint uses this, so an unauthenticated request gets
    401 and nobody can read another account's data by guessing a URL.
    """
    token = _token_from_credentials(creds)
    user = user_for_token(db, token) if token else None
    if not user:
        raise HTTPException(401, "Sign in to use Aries.")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    """Dependency: the caller must hold the admin username."""
    if not is_admin_username(user.username_lower):
        raise HTTPException(403, "Admin only.")
    return user


# ---------------------------------------------------------------- migration + deletion


# Every table whose rows carry a user_key.
_USER_KEY_TABLES = (
    Profile,
    Personality,
    Memory,
    Message,
    Task,
    Permission,
    FaceEnrollment,
    Screenshot,
    Connection,
)


def claim_default_data(db: Session, admin: User) -> int:
    """Move every pre-accounts ("default") row to the admin account.

    Returns how many rows moved. Idempotent: after the first claim
    there are no "default" rows left, so later calls move zero rows.
    Also renames the face-model folder data/faces/default/ to the
    admin's folder, so an enrolled face keeps working.
    """
    key = user_key(admin)
    moved = 0
    for model in _USER_KEY_TABLES:
        n = (
            db.query(model)
            .filter_by(user_key=LEGACY_USER_KEY)
            .update({"user_key": key})
        )
        moved += n
    db.commit()
    faces_dir = os.getenv("FACES_DIR", "data/faces")
    old_dir = os.path.join(faces_dir, LEGACY_USER_KEY)
    new_dir = os.path.join(faces_dir, key)
    if os.path.isdir(old_dir) and not os.path.isdir(new_dir):
        try:
            shutil.move(old_dir, new_dir)
        except OSError:
            pass
    return moved


def delete_user_data(db: Session, user: User) -> None:
    """Delete a user and everything that belongs to them."""
    key = user_key(user)
    for model in _USER_KEY_TABLES:
        db.query(model).filter_by(user_key=key).delete()
    db.query(DbSession).filter_by(user_id=user.id).delete()
    db.query(OAuthState).filter_by(user_key=key).delete()
    db.delete(user)
    db.commit()
    faces_dir = os.getenv("FACES_DIR", "data/faces")
    user_dir = os.path.join(faces_dir, key)
    if os.path.isdir(user_dir):
        shutil.rmtree(user_dir, ignore_errors=True)


def public_user(user: User) -> dict:
    return {
        "username": user.username,
        "role": user.role,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }
