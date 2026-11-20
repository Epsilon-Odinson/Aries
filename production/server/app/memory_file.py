"""Per-user memory file.

Every account gets its own ``.md`` file that Aries maintains — like
Muse's MEMORY.md. The database vault stays the source of truth; the
file is rendered from it (grouped by category as ``## Category``
headings with ``- fact`` bullets — the same format as the memory
import file, so export and import round-trip).

Files live under the server data dir (``data/memory-files/``, next
to ``data/faces/``), one file per account, named by the account's
``user_key``. They are NOT in the repo — the repo is public. On
Render the filesystem is ephemeral, which is fine: the file is
regenerated from the vault whenever it is read and whenever the
vault changes, so losing it costs nothing.

Only approved memories are rendered. Pending (unapproved) items are
not in the file yet.
"""

from __future__ import annotations

import os
import re

from sqlalchemy.orm import Session

from .db import Memory

MEMORY_FILES_DIR = os.getenv("MEMORY_FILES_DIR", "data/memory-files")


def _safe_user_key(user_key: str) -> str:
    """A filename-safe account key. ``user_key`` is already the
    canonical lowercase username, but never trust a raw value in a
    path — strip anything that is not a plain filename character."""
    safe = re.sub(r"[^a-z0-9_\-]", "", (user_key or "").lower())
    if not safe:
        raise ValueError("Invalid user key")
    return safe


def memory_file_path(user_key: str) -> str:
    return os.path.join(MEMORY_FILES_DIR, _safe_user_key(user_key) + ".md")


def render_memory_file(db: Session, user_key: str) -> str:
    """Render the user's file text from the vault. Empty string when
    the user has no approved memories yet."""
    memories = (
        db.query(Memory)
        .filter_by(user_key=user_key, pending=False)
        .order_by(Memory.created_at.asc())
        .all()
    )
    if not memories:
        return ""
    by_category: dict[str, list[str]] = {}
    for mem in memories:
        by_category.setdefault((mem.category or "general").strip() or "general", []).append(mem.text)
    lines = ["# Aries memory", ""]
    for category in sorted(by_category):
        lines.append(f"## {category}")
        lines.extend(f"- {text}" for text in by_category[category])
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def refresh_memory_file(db: Session, user_key: str) -> str:
    """Rewrite the user's file from the vault. Returns the new text
    ("" when the user has no approved memories — the file is removed
    in that case). Called after every vault change."""
    text = render_memory_file(db, user_key)
    path = memory_file_path(user_key)
    if not text:
        try:
            os.remove(path)
        except OSError:
            pass
        return ""
    os.makedirs(MEMORY_FILES_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return text


def read_memory_file(db: Session, user_key: str) -> str:
    """The signed-in user's file text. Renders from the vault when
    the file is missing or stale (e.g. a vault built before this
    feature existed), so the file always matches the vault."""
    path = memory_file_path(user_key)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            disk = fh.read()
    except OSError:
        disk = None
    fresh = render_memory_file(db, user_key)
    if disk != fresh:
        return refresh_memory_file(db, user_key)
    return disk
