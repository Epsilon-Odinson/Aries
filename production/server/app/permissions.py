"""Permissions — enforced server-side, not just in the UI.

Capabilities: microphone, web_search, save_memory, delete_memory,
clear_chat, screenshot, face_login.
Settings: ask | always | never.
- ``never`` hard-blocks the action on the server.
- ``ask`` requires an explicit per-request consent flag.
- ``always`` allows without asking.

Permission choices are stored per user in the database and editable
in Settings / More. Nothing sensitive happens without a decision.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from .db import Permission

CAPABILITIES = (
    "microphone",
    "web_search",
    "save_memory",
    "delete_memory",
    "clear_chat",
    "screenshot",
    "face_login",
)

DEFAULTS = {cap: "ask" for cap in CAPABILITIES}


class PermissionDenied(RuntimeError):
    """Raised when a capability is blocked by the user's setting."""


def get_setting(db: Session, user_key: str, capability: str) -> str:
    row = (
        db.query(Permission)
        .filter_by(user_key=user_key, capability=capability)
        .first()
    )
    return row.setting if row else DEFAULTS.get(capability, "ask")


def set_setting(db: Session, user_key: str, capability: str, setting: str) -> str:
    if capability not in CAPABILITIES:
        raise ValueError(f"Unknown capability: {capability}")
    if setting not in ("ask", "always", "never"):
        raise ValueError(f"Unknown setting: {setting}")
    row = (
        db.query(Permission)
        .filter_by(user_key=user_key, capability=capability)
        .first()
    )
    if row is None:
        row = Permission(user_key=user_key, capability=capability, setting=setting)
        db.add(row)
    else:
        row.setting = setting
    db.commit()
    return row.setting


def require(db: Session, user_key: str, capability: str, consent: bool = False) -> None:
    """Enforce a capability. Raises PermissionDenied when blocked."""
    setting = get_setting(db, user_key, capability)
    if setting == "never":
        raise PermissionDenied(
            f"{capability} is set to Never. Change it in Settings → Permissions."
        )
    if setting == "ask" and not consent:
        raise PermissionDenied(f"{capability} needs your permission first.")
    # "always", or "ask" with explicit consent: allowed.
