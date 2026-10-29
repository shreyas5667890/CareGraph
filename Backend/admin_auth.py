"""Admin authentication for the CareGraph dashboard.

Design
------
* Credentials come from environment variables, never from source code.
  - ``CAREGRAPH_ADMIN_USERNAME`` (default ``admin``)
  - ``CAREGRAPH_ADMIN_PASSWORD_HASH`` (preferred, a Werkzeug hash)
  - ``CAREGRAPH_ADMIN_PASSWORD`` (development convenience; hashed on startup)
* Passwords are compared against a Werkzeug password hash.
* Login state lives in a signed, HTTP-only Flask session cookie.
* Every mutating admin request must echo the session CSRF token in the
  ``X-CSRF-Token`` header.
"""
from __future__ import annotations

import os
import secrets
from functools import wraps
from typing import Any

from flask import jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

CSRF_SESSION_KEY = "admin_csrf_token"
AUTH_SESSION_KEY = "admin_authenticated"
USER_SESSION_KEY = "admin_username"

_plain_hash_cache: str | None = None


def admin_username() -> str:
    return os.getenv("CAREGRAPH_ADMIN_USERNAME", "admin").strip() or "admin"


def admin_password_hash() -> str | None:
    """Resolve the configured admin password hash, hashing a plain env value if needed."""
    global _plain_hash_cache

    stored = os.getenv("CAREGRAPH_ADMIN_PASSWORD_HASH", "").strip()
    if stored:
        return stored
    plain = os.getenv("CAREGRAPH_ADMIN_PASSWORD", "")
    if plain:
        if _plain_hash_cache is None:
            _plain_hash_cache = generate_password_hash(plain)
        return _plain_hash_cache
    return None


def admin_configured() -> bool:
    return admin_password_hash() is not None


def verify_credentials(username: Any, password: Any) -> bool:
    """Constant-ish time credential check. Never logs or returns the password."""
    if not isinstance(username, str) or not isinstance(password, str):
        return False
    stored_hash = admin_password_hash()
    if not stored_hash:
        return False
    if not secrets.compare_digest(username.strip(), admin_username()):
        return False
    return check_password_hash(stored_hash, password)


def login_admin(username: str) -> str:
    """Start an authenticated session and return the CSRF token."""
    session.clear()
    session[AUTH_SESSION_KEY] = True
    session[USER_SESSION_KEY] = username.strip()
    token = secrets.token_urlsafe(32)
    session[CSRF_SESSION_KEY] = token
    session.permanent = False
    return token


def logout_admin() -> None:
    session.clear()


def is_authenticated() -> bool:
    return bool(session.get(AUTH_SESSION_KEY))


def current_csrf_token() -> str | None:
    return session.get(CSRF_SESSION_KEY)


def _csrf_ok() -> bool:
    expected = session.get(CSRF_SESSION_KEY) or ""
    supplied = request.headers.get("X-CSRF-Token", "")
    if not expected or not supplied:
        return False
    return secrets.compare_digest(str(supplied), str(expected))


def admin_required(view):
    """Protect an admin endpoint: session auth plus CSRF for mutations."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        if not is_authenticated():
            return jsonify({"success": False, "error": "Authentication required."}), 401
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not _csrf_ok():
            return (
                jsonify({"success": False, "error": "Missing or invalid CSRF token."}),
                403,
            )
        return view(*args, **kwargs)

    return wrapper
