"""Additive database schema for the CareGraph patient/appointment features.

The existing ``doctors`` table is never rewritten. This module only adds new
tables and indexes, so it is safe to run repeatedly and never removes or alters
existing records.

Tables
------
* ``patients``            - lightweight patient accounts (session-based auth).
* ``doctor_availability`` - per-doctor weekly working blocks used to generate
                            bookable slots. Stored as data, not hardcoded in the
                            request path.
* ``appointments``        - persistent appointment records with a partial unique
                            index that makes double-booking impossible at the
                            database level while an appointment is ``booked``.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent
DEFAULT_DB = PROJECT_ROOT / "Data" / "caregraph_doctors.db"

# Statements are ordered so the tables exist before their indexes.
NEW_OBJECTS = (
    # ---------------------------------------------------------------- patients
    """
    CREATE TABLE IF NOT EXISTS "patients" (
        "Patient_ID"    TEXT PRIMARY KEY,
        "Username"      TEXT NOT NULL,
        "Password_Hash" TEXT NOT NULL,
        "Full_Name"     TEXT,
        "Email"         TEXT,
        "Created_At"    TEXT NOT NULL
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS "ux_patients_username"
    ON "patients"(lower("Username"))
    """,
    # ------------------------------------------------------- doctor availability
    """
    CREATE TABLE IF NOT EXISTS "doctor_availability" (
        "Availability_ID"   INTEGER PRIMARY KEY AUTOINCREMENT,
        "Doctor_ID"         TEXT NOT NULL,
        "Day_Of_Week"       INTEGER NOT NULL,
        "Start_Time"        TEXT NOT NULL,
        "End_Time"          TEXT NOT NULL,
        "Slot_Minutes"      INTEGER NOT NULL DEFAULT 30,
        "Consultation_Type" TEXT NOT NULL DEFAULT 'In-person',
        "Source"            TEXT NOT NULL DEFAULT 'default_schedule'
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS "ux_availability_doctor_day_block"
    ON "doctor_availability"("Doctor_ID", "Day_Of_Week", "Start_Time")
    """,
    """
    CREATE INDEX IF NOT EXISTS "idx_availability_doctor"
    ON "doctor_availability"("Doctor_ID", "Day_Of_Week")
    """,
    # ------------------------------------------------------------ appointments
    """
    CREATE TABLE IF NOT EXISTS "appointments" (
        "Appointment_ID"   TEXT PRIMARY KEY,
        "Patient_ID"       TEXT NOT NULL REFERENCES "patients"("Patient_ID"),
        "Doctor_ID"        TEXT NOT NULL,
        "Appointment_Date" TEXT NOT NULL,
        "Start_Time"       TEXT NOT NULL,
        "End_Time"         TEXT NOT NULL,
        "Status"           TEXT NOT NULL DEFAULT 'booked',
        "Consultation_Type" TEXT,
        "Fee_INR"          INTEGER,
        "Created_At"       TEXT NOT NULL,
        "Cancelled_At"     TEXT,
        "Rescheduled_At"   TEXT
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS "idx_appointments_patient"
    ON "appointments"("Patient_ID", "Appointment_Date")
    """,
    """
    CREATE INDEX IF NOT EXISTS "idx_appointments_doctor_slot"
    ON "appointments"("Doctor_ID", "Appointment_Date", "Start_Time")
    """,
    # Enforces "one booked appointment per doctor/date/start" at the DB level.
    # Cancelled rows are excluded, so cancelling frees the slot again.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS "ux_appointments_active_slot"
    ON "appointments"("Doctor_ID", "Appointment_Date", "Start_Time")
    WHERE "Status" = 'booked'
    """,
)

# Additive index on the pre-existing doctors table so appointment rows can
# reference a doctor ID. Skipped silently if the data ever contained duplicates
# (doctor existence is additionally verified in application logic).
DOCTOR_INDEX = (
    'CREATE UNIQUE INDEX IF NOT EXISTS "ux_doctors_doctor_id" '
    'ON "doctors"("Doctor_ID")'
)


def database_path() -> Path:
    """Resolve the SQLite path, honouring the existing CAREGRAPH_DOCTOR_DB env var."""
    path = Path(os.getenv("CAREGRAPH_DOCTOR_DB", str(DEFAULT_DB)))
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    return path


def connect(
    path: str | Path | None = None,
    *,
    isolation_level: str | None = "",
    timeout: float = 10.0,
) -> sqlite3.Connection:
    """Open a connection with row access by name and foreign keys enabled."""
    con = sqlite3.connect(
        str(path or database_path()),
        isolation_level=isolation_level,
        timeout=timeout,
    )
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def ensure_schema(con: sqlite3.Connection) -> None:
    """Create every object the new features need. Idempotent and additive.

    Commits its own DDL, so call it *before* opening an application transaction.
    """
    for statement in NEW_OBJECTS:
        con.execute(statement)
    try:
        con.execute(DOCTOR_INDEX)
    except sqlite3.Error:
        # Duplicate Doctor_ID values would prevent the unique index; doctor
        # existence is still validated in the application layer.
        pass
    con.commit()


def initialize(path: str | Path | None = None) -> Path:
    """Create the schema on a standalone connection and return the database path."""
    resolved = Path(path) if path is not None else database_path()
    con = connect(resolved)
    try:
        con.execute("BEGIN")
        ensure_schema(con)
        con.commit()
    except sqlite3.Error:
        con.rollback()
        raise
    finally:
        con.close()
    return resolved
