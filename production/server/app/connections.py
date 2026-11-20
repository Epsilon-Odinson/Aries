"""OAuth connections for Aries — Gmail (Google) and Spotify.

Tokens live in the private database (Postgres on Render), never in the
repo, never logged, never sent to the frontend. The API only reports
whether a service is connected and which account it is. Skills that
need an account (Gmail reading, Spotify playback) will use the stored
tokens through the helpers here.

Client IDs and secrets come from environment variables ONLY:
- Google:  GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
- Spotify: SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET
- APP_BASE_URL: the public URL of this service, e.g.
  https://aries-ahvo.onrender.com — the OAuth redirect URIs are built
  from it, and the exact callback URLs must be registered in the
  Google Cloud Console / Spotify Developer Dashboard:
    {APP_BASE_URL}/api/connections/google/callback
    {APP_BASE_URL}/api/connections/spotify/callback

WhatsApp is NOT here on purpose: a personal WhatsApp account has no
one-tap OAuth sign-in like Gmail or Spotify. Connecting it would need
a separate setup (WhatsApp Business API, or a linked-device session),
so the Connections UI lists it honestly as not connectable this way.
"""

from __future__ import annotations

import base64
import os
import secrets
from datetime import datetime, timedelta
from urllib.parse import urlencode

import requests
from fastapi import Request
from sqlalchemy.orm import Session

from .db import Connection, OAuthState

TIMEOUT = 30
STATE_TTL_MINUTES = 15

# ---------------------------------------------------------------- Google

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GOOGLE_PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
# Gmail read (for the future email skill) + email (to label the account).
GOOGLE_SCOPES = "openid email https://www.googleapis.com/auth/gmail.readonly"

# ---------------------------------------------------------------- Spotify

SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_ME_URL = "https://api.spotify.com/v1/me"
# Playback (for the music skill) + identity (to label the account).
SPOTIFY_SCOPES = (
    "user-read-email user-read-private "
    "user-read-playback-state user-modify-playback-state "
    "user-read-currently-playing"
)


class ConnectionError(RuntimeError):
    """The provider answered with an error, or a step failed."""


class NotConfigured(RuntimeError):
    """The OAuth client env vars for this service are not set."""


# ---------------------------------------------------------------- config


def _google_client() -> tuple[str, str]:
    cid = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    if not cid or not secret:
        raise NotConfigured(
            "Google OAuth is not set up on the server. Add "
            "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in the Render "
            "dashboard (Environment), never in code."
        )
    return cid, secret


def _spotify_client() -> tuple[str, str]:
    cid = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
    secret = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()
    if not cid or not secret:
        raise NotConfigured(
            "Spotify OAuth is not set up on the server. Add "
            "SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in the Render "
            "dashboard (Environment), never in code."
        )
    return cid, secret


def configured(service: str) -> bool:
    """True when the OAuth client env vars for this service are set."""
    try:
        if service == "google":
            _google_client()
        elif service == "spotify":
            _spotify_client()
        else:
            return False
    except NotConfigured:
        return False
    return True


def _base_url(request: Request) -> str:
    """Public base URL of this service, for the OAuth redirect URIs."""
    override = os.getenv("APP_BASE_URL", "").strip().rstrip("/")
    if override:
        return override
    return str(request.base_url).rstrip("/")


def _callback_url(request: Request, service: str) -> str:
    return f"{_base_url(request)}/api/connections/{service}/callback"


# ---------------------------------------------------------------- state


def _new_state(db: Session, service: str, redirect_uri: str, user_key: str = "") -> str:
    state = secrets.token_urlsafe(32)
    db.add(OAuthState(service=service, state=state, redirect_uri=redirect_uri, user_key=user_key))
    db.commit()
    return state


def _consume_state(db: Session, service: str, state: str) -> tuple[str, str]:
    """Verify the state; return (redirect_uri, user_key).

    The callback is a plain browser redirect with no Bearer token,
    so the user who started the flow is recovered from the state row.
    """
    # Drop stale rows so the table does not grow.
    cutoff = datetime.utcnow() - timedelta(minutes=STATE_TTL_MINUTES)
    db.query(OAuthState).filter(OAuthState.created_at < cutoff).delete()
    db.commit()
    row = (
        db.query(OAuthState)
        .filter_by(service=service, state=state)
        .first()
    )
    if not row:
        raise ConnectionError("This sign-in link has expired or was already used. Start again from Settings → Connections.")
    redirect_uri, user_key = row.redirect_uri, row.user_key or ""
    db.delete(row)
    db.commit()
    return redirect_uri, user_key


# ---------------------------------------------------------------- auth URLs


def google_auth_url(db: Session, request: Request, user_key: str = "") -> str:
    cid, _ = _google_client()
    redirect_uri = _callback_url(request, "google")
    state = _new_state(db, "google", redirect_uri, user_key)
    params = {
        "client_id": cid,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": GOOGLE_SCOPES,
        "access_type": "offline",  # ask for a refresh token
        "prompt": "consent",  # always return a refresh token, not just once
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def spotify_auth_url(db: Session, request: Request, user_key: str = "") -> str:
    cid, _ = _spotify_client()
    redirect_uri = _callback_url(request, "spotify")
    state = _new_state(db, "spotify", redirect_uri, user_key)
    params = {
        "client_id": cid,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SPOTIFY_SCOPES,
        "state": state,
    }
    return f"{SPOTIFY_AUTH_URL}?{urlencode(params)}"


# ---------------------------------------------------------------- token exchange


def _post_token(url: str, data: dict, headers: dict | None = None) -> dict:
    try:
        resp = requests.post(url, data=data, headers=headers or {}, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise ConnectionError(f"Could not reach the sign-in provider: {exc}") from exc
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if not resp.ok:
        # Never log the body — it may echo the code or client secret.
        detail = body.get("error_description") or body.get("error") or f"HTTP {resp.status}"
        raise ConnectionError(f"Sign-in failed: {detail}")
    return body


def _store_tokens(
    db: Session,
    user_key: str,
    service: str,
    token: dict,
    account_label: str,
) -> None:
    expires_at = None
    if token.get("expires_in"):
        try:
            expires_at = datetime.utcnow() + timedelta(seconds=int(token["expires_in"]))
        except (TypeError, ValueError):
            expires_at = None
    row = (
        db.query(Connection)
        .filter_by(user_key=user_key, service=service)
        .first()
    )
    if not row:
        row = Connection(user_key=user_key, service=service)
        db.add(row)
    row.account_label = account_label
    row.access_token = token.get("access_token", "")
    # A refresh token is only returned on some exchanges; keep the old
    # one when the provider does not send a new one.
    if token.get("refresh_token"):
        row.refresh_token = token["refresh_token"]
    row.token_type = token.get("token_type", "Bearer")
    if expires_at:
        row.expires_at = expires_at
    row.scope = token.get("scope", "")
    row.updated_at = datetime.utcnow()
    db.commit()


def _google_account_email(access_token: str) -> str:
    try:
        resp = requests.get(
            GOOGLE_PROFILE_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise ConnectionError(f"Could not read the Gmail account: {exc}") from exc
    if not resp.ok:
        raise ConnectionError("Connected, but Gmail would not confirm the account.")
    return (resp.json().get("emailAddress") or "").strip()


def _spotify_account_label(access_token: str) -> str:
    try:
        resp = requests.get(
            SPOTIFY_ME_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise ConnectionError(f"Could not read the Spotify account: {exc}") from exc
    if not resp.ok:
        raise ConnectionError("Connected, but Spotify would not confirm the account.")
    data = resp.json()
    return (data.get("display_name") or data.get("email") or "").strip()


def handle_google_callback(db: Session, request: Request, code: str, state: str) -> str:
    cid, secret = _google_client()
    redirect_uri, user_key = _consume_state(db, "google", state)
    if not user_key:
        raise ConnectionError("This sign-in was started before accounts — sign in to Aries and try again.")
    token = _post_token(
        GOOGLE_TOKEN_URL,
        {
            "code": code,
            "client_id": cid,
            "client_secret": secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
    )
    label = _google_account_email(token.get("access_token", ""))
    _store_tokens(db, user_key, "google", token, label)
    return label


def handle_spotify_callback(db: Session, request: Request, code: str, state: str) -> str:
    cid, secret = _spotify_client()
    redirect_uri, user_key = _consume_state(db, "spotify", state)
    if not user_key:
        raise ConnectionError("This sign-in was started before accounts — sign in to Aries and try again.")
    basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    token = _post_token(
        SPOTIFY_TOKEN_URL,
        {
            "code": code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        headers={"Authorization": f"Basic {basic}"},
    )
    label = _spotify_account_label(token.get("access_token", ""))
    _store_tokens(db, user_key, "spotify", token, label)
    return label


# ---------------------------------------------------------------- status / disconnect / refresh


def get_connection(db: Session, user_key: str, service: str) -> Connection | None:
    return (
        db.query(Connection)
        .filter_by(user_key=user_key, service=service)
        .first()
    )


def status(db: Session, user_key: str) -> dict:
    """What the frontend shows. Tokens are never included."""
    out = {}
    for service, label in (("google", "Gmail"), ("spotify", "Spotify")):
        row = get_connection(db, user_key, service)
        out[service] = {
            "name": label,
            "connected": bool(row and row.access_token),
            "account": row.account_label if row else None,
            "configured": configured(service),
        }
    out["whatsapp"] = {
        "name": "WhatsApp",
        "connected": False,
        "account": None,
        "configured": False,
        "note": (
            "Personal WhatsApp has no one-tap sign-in like Gmail or Spotify. "
            "Connecting it needs a separate setup (WhatsApp Business API or a "
            "linked-device session) — not available as a Connect button yet."
        ),
    }
    return out


def disconnect(db: Session, user_key: str, service: str) -> None:
    row = get_connection(db, user_key, service)
    if not row:
        return
    if service == "google" and row.access_token:
        # Best effort: tell Google the token is dead. Ignore failures —
        # the row is deleted either way.
        try:
            requests.post(
                GOOGLE_REVOKE_URL,
                data={"token": row.access_token},
                timeout=TIMEOUT,
            )
        except requests.RequestException:
            pass
    db.delete(row)
    db.commit()


def refresh_access_token(db: Session, user_key: str, service: str) -> str:
    """Refresh an expired access token and return the new one.

    For the future Gmail / Spotify skills. Raises ConnectionError when
    there is no refresh token or the provider refuses.
    """
    row = get_connection(db, user_key, service)
    if not row or not row.refresh_token:
        raise ConnectionError(f"{service} is not connected.")
    if service == "google":
        cid, secret = _google_client()
        token = _post_token(
            GOOGLE_TOKEN_URL,
            {
                "client_id": cid,
                "client_secret": secret,
                "refresh_token": row.refresh_token,
                "grant_type": "refresh_token",
            },
        )
    elif service == "spotify":
        cid, secret = _spotify_client()
        basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
        token = _post_token(
            SPOTIFY_TOKEN_URL,
            {"refresh_token": row.refresh_token, "grant_type": "refresh_token"},
            headers={"Authorization": f"Basic {basic}"},
        )
    else:
        raise ConnectionError(f"Unknown service: {service}")
    _store_tokens(db, user_key, service, token, row.account_label)
    return token.get("access_token", "")
