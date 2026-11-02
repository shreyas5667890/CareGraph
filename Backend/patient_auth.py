"""Patient authentication for CareGraph appointment features.

The project already authenticates an *admin* via a signed, HTTP-only Flask
session cookie plus an ``X-CSRF-Token`` header (see :mod:`admin_auth`). There was
no patient identity, which the appointment specification requires ("the
authenticated patient's identity"). This module adds the **same** security
architecture for patients, backed by the real ``patients`` table created in
:mod:`schema`:

* credentials are stored as Werkzeug password hashes, never plain text;
* login state lives in the existing signed session cookie under patient-specific
  keys, so admin and patient sessions never collide;
* every mutating patient request must echo the session CSRF token.
"""
from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from functools import wraps
from typing import Any

from flask import jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from schema import connect, database_path, ensure_schema

PATIENT_AUTH_SESSION_KEY = "patient_authenticated"
PATIENT_ID_SESSION_KEY = "patient_id"
PATIENT_USERNAME_SESSION_KEY = "patient_username"
PATIENT_CSRF_SESSION_KEY = "patient_csrf_token"

USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,50}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 200


def _public_patient(row: Any) -> dict:
    return {
        "patient_id": row["Patient_ID"],
        "username": row["Username"],
        "full_name": row["Full_Name"] or "",
        "email": row["Email"] or "",
        "created_at": row["Created_At"],
    }


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def validate_registration(payload: Any) -> tuple[dict, list[str]]:
    """Validate a registration payload. Returns ``(values, errors)``."""
    errors: list[str] = []
    values: dict[str, str] = {}

    if not isinstance(payload, dict):
        return {}, ["Request body must be a JSON object."]

    username = payload.get("username")
    if not isinstance(username, str) or not username.strip():
        errors.append("Username is required.")
    elif not USERNAME_RE.match(username.strip()):
        errors.append(
            "Username must be 3-50 characters using letters, digits, dot, underscore or hyphen."
        )
    else:
        values["username"] = username.strip()

    password = payload.get("password")
    if not isinstance(password, str) or not password:
        errors.append("Password is required.")
    elif len(password) < MIN_PASSWORD_LENGTH:
        errors.append(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    elif len(password) > MAX_PASSWORD_LENGTH:
        errors.append(f"Password must be at most {MAX_PASSWORD_LENGTH} characters.")
    else:
        values["password"] = password

    full_name = payload.get("full_name", "")
    if full_name is None:
        full_name = ""
    if not isinstance(full_name, str):
        errors.append("Full name must be text.")
    elif len(full_name.strip()) > 120:
        errors.append("Full name must be at most 120 characters.")
    else:
        values["full_name"] = full_name.strip()

    email = payload.get("email", "")
    if email is None:
        email = ""
    if not isinstance(email, str):
        errors.append("Email must be text.")
    elif email.strip() and not EMAIL_RE.match(email.strip()):
        errors.append("Email address is not valid.")
    elif len(email.strip()) > 254:
        errors.append("Email must be at most 254 characters.")
    else:
        values["email"] = email.strip()

    return values, errors


def create_patient(payload: Any) -> tuple[dict, int]:
    """Register a new patient. Returns ``(payload, http_status)``."""
    values, errors = validate_registration(payload)
    if errors:
        return {"success": False, "error": "Validation failed.", "errors": errors}, 400

    con = connect()
    try:
        ensure_schema(con)
        existing = con.execute(
            'SELECT 1 FROM "patients" WHERE lower("Username") = lower(?)',
            (values["username"],),
        ).fetchone()
        if existing is not None:
            return {"success": False, "error": "That username is already taken."}, 409

        patient_id = ""
        for _ in range(5):
            candidate = "PAT-" + secrets.token_hex(4).upper()
            clash = con.execute(
                'SELECT 1 FROM "patients" WHERE "Patient_ID" = ?', (candidate,)
            ).fetchone()
            if clash is None:
                patient_id = candidate
                break
        if not patient_id:
            return {"success": False, "error": "Could not allocate a patient ID."}, 500

        con.execute(
            'INSERT INTO "patients" '
            '("Patient_ID", "Username", "Password_Hash", "Full_Name", "Email", "Created_At") '
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                patient_id,
                values["username"],
                generate_password_hash(values["password"]),
                values["full_name"],
                values["email"],
                _now(),
            ),
        )
        con.commit()
    except Exception:
        con.rollback()
        return {"success": False, "error": "Could not create the patient account."}, 500
    finally:
        con.close()

    return {"success": True, "patient": {"patient_id": patient_id, "username": values["username"]}}, 201


def verify_patient_credentials(username: Any, password: Any) -> dict | None:
    """Return the patient row for valid credentials, else ``None``."""
    if not isinstance(username, str) or not isinstance(password, str):
        return None
    if not username.strip() or not password:
        return None

    con = connect()
    try:
        ensure_schema(con)
        row = con.execute(
            'SELECT * FROM "patients" WHERE lower("Username") = lower(?)',
            (username.strip(),),
        ).fetchone()
    except Exception:
        return None
    finally:
        con.close()

    if row is None:
        return None
    if not check_password_hash(row["Password_Hash"], password):
        return None
    return _public_patient(row)


def login_patient(patient: dict) -> str:
    """Start an authenticated patient session and return the CSRF token."""
    session.clear()
    session[PATIENT_AUTH_SESSION_KEY] = True
    session[PATIENT_ID_SESSION_KEY] = patient["patient_id"]
    session[PATIENT_USERNAME_SESSION_KEY] = patient["username"]
    token = secrets.token_urlsafe(32)
    session[PATIENT_CSRF_SESSION_KEY] = token
    session.permanent = False
    return token


def logout_patient() -> None:
    session.clear()


def is_patient_authenticated() -> bool:
    return bool(session.get(PATIENT_AUTH_SESSION_KEY))


def current_patient_id() -> str | None:
    """The authenticated patient's ID from the session. Never trust the client."""
    value = session.get(PATIENT_ID_SESSION_KEY)
    return str(value) if value else None


def current_patient_username() -> str | None:
    value = session.get(PATIENT_USERNAME_SESSION_KEY)
    return str(value) if value else None


def current_patient_csrf_token() -> str | None:
    return session.get(PATIENT_CSRF_SESSION_KEY)


def _csrf_ok() -> bool:
    expected = session.get(PATIENT_CSRF_SESSION_KEY) or ""
    supplied = request.headers.get("X-CSRF-Token", "")
    if not expected or not supplied:
        return False
    return secrets.compare_digest(str(supplied), str(expected))


def patient_required(view):
    """Protect a patient endpoint: session auth plus CSRF for mutations."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        if not is_patient_authenticated() or not current_patient_id():
            return jsonify({"success": False, "error": "Patient authentication required."}), 401
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not _csrf_ok():
            return (
                jsonify({"success": False, "error": "Missing or invalid CSRF token."}),
                403,
            )
        return view(*args, **kwargs)

    return wrapper


__all__ = [
    "create_patient",
    "verify_patient_credentials",
    "login_patient",
    "logout_patient",
    "is_patient_authenticated",
    "current_patient_id",
    "current_patient_username",
    "current_patient_csrf_token",
    "patient_required",
    "database_path",
]
