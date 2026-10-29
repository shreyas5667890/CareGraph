"""Shared helpers for the CareGraph backend test suite."""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
REAL_DB = PROJECT_ROOT / "Data" / "caregraph_doctors.db"

# Make the backend modules importable regardless of the working directory.
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

DOCTOR_COLUMNS = (
    "Doctor_ID",
    "Doctor_Name",
    "Specialization",
    "Age",
    "Experience_Years",
    "Description",
    "Address",
    "City",
    "State",
    "Latitude",
    "Longitude",
    "Consultation_Fee_INR",
    "Rating_Out_of_5",
    "Data_Status",
)


def temp_db_path() -> str:
    """A unique, empty path for a throwaway SQLite database."""
    handle, path = tempfile.mkstemp(prefix="caregraph_test_", suffix=".db")
    os.close(handle)
    return path


def create_doctor_db(path: str, rows: list[dict]) -> str:
    """Create a test database with the exact production schema."""
    con = sqlite3.connect(path)
    try:
        con.execute(
            'CREATE TABLE IF NOT EXISTS "doctors" ('
            '"Doctor_ID" TEXT, "Doctor_Name" TEXT, "Specialization" TEXT, '
            '"Age" INTEGER, "Experience_Years" INTEGER, "Description" TEXT, '
            '"Address" TEXT, "City" TEXT, "State" TEXT, "Latitude" REAL, '
            '"Longitude" REAL, "Consultation_Fee_INR" INTEGER, '
            '"Rating_Out_of_5" REAL, "Data_Status" TEXT)'
        )
        columns = ", ".join(f'"{c}"' for c in DOCTOR_COLUMNS)
        placeholders = ", ".join("?" for _ in DOCTOR_COLUMNS)
        con.executemany(
            f'INSERT INTO "doctors" ({columns}) VALUES ({placeholders})',
            [tuple(row.get(c) for c in DOCTOR_COLUMNS) for row in rows],
        )
        con.commit()
    finally:
        con.close()
    return path


def copy_real_db(path: str) -> str:
    """Copy the production database so tests never mutate the real data."""
    shutil.copyfile(str(REAL_DB), str(path))
    return path
