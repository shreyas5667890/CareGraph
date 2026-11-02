"""Persistent, transactional appointment booking for CareGraph.

Responsibilities
----------------
* Per-doctor availability (stored as data in ``doctor_availability``) used to
  generate bookable slots. There is no universal hardcoded schedule in the
  request path: each doctor's working blocks are rows in the database and can be
  replaced by an administrator.
* Appointment creation / cancellation / rescheduling inside ``BEGIN IMMEDIATE``
  transactions so availability is re-checked against committed state at
  confirmation time, not against anything the frontend was told earlier.
* Double-booking is prevented twice over: an explicit re-check inside the
  transaction **and** a partial unique index on
  ``(Doctor_ID, Appointment_Date, Start_Time) WHERE Status = 'booked'``.

The schedule data shipped by default is clearly-labelled *system configuration*
(``Source = 'default_schedule'``), not verified real-world provider availability.
"""
from __future__ import annotations

import secrets
import sqlite3
import zlib
from datetime import date as date_cls
from datetime import datetime, timedelta, timezone
from typing import Any

from doctor_recommender_sqlite import DoctorRecommender
from doctor_recommender_sqlite import doctor_recommender as default_recommender
from schema import connect, database_path, ensure_schema

SLOT_MINUTES_DEFAULT = 30
MAX_ADVANCE_DAYS = 90
CONSULTATION_TYPES = ("In-person", "Teleconsultation")
DEFAULT_CONSULTATION_TYPE = "In-person"
STATUS_BOOKED = "booked"
STATUS_CANCELLED = "cancelled"

AVAILABILITY_SOURCE_DEFAULT = "default_schedule"
AVAILABILITY_SOURCE_ADMIN = "admin_configured"
AVAILABILITY_NOTICE = (
    "Appointment schedules are system-generated configuration stored per doctor, "
    "not verified real-world provider availability. An administrator can edit them."
)

DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

# Deterministic default working patterns. The pattern for a doctor is chosen from
# their Doctor_ID, so schedules are always per-doctor and never one hardcoded
# universal schedule shared by everyone. Every pattern covers all seven days so
# that each doctor always has bookable demo slots, and the patterns still differ
# per doctor through their working hours.
DEFAULT_SCHEDULE_PATTERNS = (
    {"days": (0, 1, 2, 3, 4, 5, 6), "blocks": (("09:00", "13:00"), ("14:00", "18:00"))},
    {"days": (0, 1, 2, 3, 4, 5, 6), "blocks": (("10:00", "14:00"), ("15:00", "19:00"))},
    {"days": (0, 1, 2, 3, 4, 5, 6), "blocks": (("08:30", "12:30"), ("16:00", "20:00"))},
    {"days": (0, 1, 2, 3, 4, 5, 6), "blocks": (("09:30", "13:30"), ("15:30", "18:30"))},
)


# --------------------------------------------------------------------- helpers
def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_date(value: Any) -> date_cls | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_time(value: Any):
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    return None


def _time_to_minutes(value) -> int:
    return value.hour * 60 + value.minute


def _minutes_to_hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _block_slots(start, end, slot_minutes: int) -> list[tuple[str, str]]:
    """Split a working block into consecutive (start, end) slot strings."""
    step = slot_minutes if slot_minutes and slot_minutes > 0 else SLOT_MINUTES_DEFAULT
    cursor = _time_to_minutes(start)
    end_minutes = _time_to_minutes(end)
    slots: list[tuple[str, str]] = []
    while cursor + step <= end_minutes:
        slots.append((_minutes_to_hhmm(cursor), _minutes_to_hhmm(cursor + step)))
        cursor += step
    return slots


def _safe_rollback(con: sqlite3.Connection) -> None:
    try:
        con.execute("ROLLBACK")
    except sqlite3.Error:
        pass


def _default_pattern_for(doctor_id: str) -> dict:
    index = zlib.crc32(str(doctor_id).encode("utf-8")) % len(DEFAULT_SCHEDULE_PATTERNS)
    return DEFAULT_SCHEDULE_PATTERNS[index]


def _seed_default_availability(con: sqlite3.Connection, doctor_id: str) -> None:
    pattern = _default_pattern_for(doctor_id)
    for day in pattern["days"]:
        for start, end in pattern["blocks"]:
            con.execute(
                'INSERT OR IGNORE INTO "doctor_availability" '
                '("Doctor_ID", "Day_Of_Week", "Start_Time", "End_Time", '
                '"Slot_Minutes", "Consultation_Type", "Source") '
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    doctor_id,
                    day,
                    start,
                    end,
                    SLOT_MINUTES_DEFAULT,
                    DEFAULT_CONSULTATION_TYPE,
                    AVAILABILITY_SOURCE_DEFAULT,
                ),
            )


def _ensure_availability(con: sqlite3.Connection, doctor_id: str) -> str:
    """Return the availability source, generating/refreshing the default schedule.

    An administrator-configured schedule is always preserved. A doctor who is still
    on the generated default schedule is refreshed when the stored rows no longer
    cover all seven days, so an updated default pattern takes effect without wiping
    administrator edits.
    """
    rows = con.execute(
        'SELECT DISTINCT "Source", "Day_Of_Week" FROM "doctor_availability" WHERE "Doctor_ID" = ?',
        (doctor_id,),
    ).fetchall()
    if not rows:
        _seed_default_availability(con, doctor_id)
        return AVAILABILITY_SOURCE_DEFAULT

    sources = {row["Source"] or AVAILABILITY_SOURCE_DEFAULT for row in rows}
    if AVAILABILITY_SOURCE_ADMIN in sources:
        return AVAILABILITY_SOURCE_ADMIN
    if len({row["Day_Of_Week"] for row in rows}) < 7:
        con.execute(
            'DELETE FROM "doctor_availability" WHERE "Doctor_ID" = ? AND "Source" = ?',
            (doctor_id, AVAILABILITY_SOURCE_DEFAULT),
        )
        _seed_default_availability(con, doctor_id)
    return AVAILABILITY_SOURCE_DEFAULT


def _blocks_for_weekday(con: sqlite3.Connection, doctor_id: str, weekday: int) -> list[sqlite3.Row]:
    return con.execute(
        'SELECT "Start_Time", "End_Time", "Slot_Minutes", "Consultation_Type" '
        'FROM "doctor_availability" WHERE "Doctor_ID" = ? AND "Day_Of_Week" = ? '
        'ORDER BY "Start_Time"',
        (doctor_id, weekday),
    ).fetchall()


def _booked_starts(
    con: sqlite3.Connection, doctor_id: str, date_str: str, *, exclude_id: str | None = None
) -> set[str]:
    sql = (
        'SELECT "Start_Time" FROM "appointments" WHERE "Doctor_ID" = ? '
        'AND "Appointment_Date" = ? AND "Status" = ?'
    )
    params: list[Any] = [doctor_id, date_str, STATUS_BOOKED]
    if exclude_id:
        sql += ' AND "Appointment_ID" <> ?'
        params.append(exclude_id)
    return {row["Start_Time"] for row in con.execute(sql, params).fetchall()}


def _build_day(
    con: sqlite3.Connection, doctor_id: str, day: date_cls
) -> tuple[list[dict], list[dict], str]:
    """Return (slots, working_hours, consultation_type) for one calendar day.

    Demo behaviour: every generated slot is offered as available unless it is
    already booked. Past dates are still rejected before this point, so a slot is
    never offered for a day that has already passed, and booked slots stay
    unavailable so double-booking can still be demonstrated.
    """
    booked = _booked_starts(con, doctor_id, day.isoformat())
    slots: list[dict] = []
    working_hours: list[dict] = []
    consultation_type = DEFAULT_CONSULTATION_TYPE

    for block in _blocks_for_weekday(con, doctor_id, day.weekday()):
        start = _parse_time(block["Start_Time"])
        end = _parse_time(block["End_Time"])
        if start is None or end is None:
            continue
        slot_minutes = block["Slot_Minutes"] or SLOT_MINUTES_DEFAULT
        consultation_type = block["Consultation_Type"] or DEFAULT_CONSULTATION_TYPE
        working_hours.append(
            {"start": block["Start_Time"], "end": block["End_Time"], "slot_minutes": slot_minutes}
        )
        for slot_start, slot_end in _block_slots(start, end, slot_minutes):
            is_free = slot_start not in booked
            slots.append(
                {
                    "start": slot_start,
                    "end": slot_end,
                    "available": bool(is_free),
                    "reason": None if is_free else "booked",
                }
            )
    return slots, working_hours, consultation_type


def _new_appointment_id(con: sqlite3.Connection) -> str:
    for _ in range(6):
        candidate = "APT-" + secrets.token_hex(4).upper()
        clash = con.execute(
            'SELECT 1 FROM "appointments" WHERE "Appointment_ID" = ?', (candidate,)
        ).fetchone()
        if clash is None:
            return candidate
    raise sqlite3.IntegrityError("Could not allocate a unique appointment ID.")


def _appointment_payload(row: Any, doctor: dict | None) -> dict:
    duration = None
    start = _parse_time(row["Start_Time"])
    end = _parse_time(row["End_Time"])
    if start is not None and end is not None:
        duration = _time_to_minutes(end) - _time_to_minutes(start)
    return {
        "appointment_id": row["Appointment_ID"],
        "patient_id": row["Patient_ID"],
        "doctor_id": row["Doctor_ID"],
        "doctor_name": (doctor or {}).get("name") or "",
        "specialty": (doctor or {}).get("specialization") or "",
        "doctor_city": (doctor or {}).get("city") or "",
        "date": row["Appointment_Date"],
        "start_time": row["Start_Time"],
        "end_time": row["End_Time"],
        "duration_minutes": duration,
        "status": row["Status"],
        "consultation_type": row["Consultation_Type"] or DEFAULT_CONSULTATION_TYPE,
        "fee_inr": row["Fee_INR"],
        "created_at": row["Created_At"],
        "cancelled_at": row["Cancelled_At"],
        "rescheduled_at": row["Rescheduled_At"],
    }


def _doctor_for(recommender: DoctorRecommender, doctor_id: str) -> dict | None:
    try:
        return recommender.get_doctor(doctor_id)
    except Exception:
        return None


def _init_schema(path: str | None) -> None:
    con = connect(path)
    try:
        ensure_schema(con)
    finally:
        con.close()


# ------------------------------------------------------------------ availability
def get_day_availability(
    doctor_id: Any,
    date_value: Any,
    *,
    recommender: DoctorRecommender | None = None,
    path: str | None = None,
) -> tuple[dict, int]:
    """Available slots for one doctor on one date. Returns ``(payload, status)``."""
    rec = recommender or default_recommender
    doctor = _doctor_for(rec, str(doctor_id or ""))
    if doctor is None:
        return {"success": False, "error": "Doctor not found."}, 404

    day = _parse_date(date_value)
    if day is None:
        return {"success": False, "error": "The 'date' parameter must be a YYYY-MM-DD date."}, 400
    today = date_cls.today()
    if day < today:
        return {"success": False, "error": "Appointment date must be today or later."}, 400
    if (day - today).days > MAX_ADVANCE_DAYS:
        return {
            "success": False,
            "error": f"Appointments can be booked at most {MAX_ADVANCE_DAYS} days in advance.",
        }, 400

    con = connect(path)
    try:
        ensure_schema(con)
        source = _ensure_availability(con, doctor["doctor_id"])
        con.commit()
        slots, working_hours, consultation_type = _build_day(con, doctor["doctor_id"], day)
    except sqlite3.Error:
        return {"success": False, "error": "Could not read appointment availability."}, 503
    finally:
        con.close()

    available_count = sum(1 for slot in slots if slot["available"])
    payload = {
        "success": True,
        "doctor_id": doctor["doctor_id"],
        "date": day.isoformat(),
        "day_of_week": day.weekday(),
        "day_name": DAY_NAMES[day.weekday()],
        "working_hours": working_hours,
        "slot_minutes": SLOT_MINUTES_DEFAULT,
        "consultation_type": consultation_type,
        "consultation_types": list(CONSULTATION_TYPES),
        "availability_source": source,
        "available_count": available_count,
        "total_slots": len(slots),
        "slots": slots,
        "notice": AVAILABILITY_NOTICE,
    }
    if not slots:
        payload["message"] = "This doctor has no configured availability on this day."
    return payload, 200


def list_open_dates(
    doctor_id: Any,
    *,
    start: Any = None,
    days: Any = 14,
    recommender: DoctorRecommender | None = None,
    path: str | None = None,
) -> tuple[dict, int]:
    """Per-date availability summary for the next ``days`` days (comparison view)."""
    rec = recommender or default_recommender
    doctor = _doctor_for(rec, str(doctor_id or ""))
    if doctor is None:
        return {"success": False, "error": "Doctor not found."}, 404

    today = date_cls.today()
    start_day = _parse_date(start) if start else today
    if start_day is None:
        return {"success": False, "error": "The 'from' parameter must be a YYYY-MM-DD date."}, 400
    if start_day < today:
        start_day = today

    try:
        window = max(1, min(int(days), 60))
    except (TypeError, ValueError):
        window = 14

    con = connect(path)
    try:
        ensure_schema(con)
        source = _ensure_availability(con, doctor["doctor_id"])
        con.commit()
        dates: list[dict] = []
        for offset in range(window):
            day = start_day + timedelta(days=offset)
            if (day - today).days > MAX_ADVANCE_DAYS:
                break
            slots, working_hours, _ = _build_day(con, doctor["doctor_id"], day)
            available = sum(1 for slot in slots if slot["available"])
            dates.append(
                {
                    "date": day.isoformat(),
                    "day_of_week": day.weekday(),
                    "day_name": DAY_NAMES[day.weekday()],
                    "available_count": available,
                    "total_slots": len(slots),
                    "bookable": available > 0,
                    "working_hours": working_hours,
                }
            )
    except sqlite3.Error:
        return {"success": False, "error": "Could not read appointment availability."}, 503
    finally:
        con.close()

    return (
        {
            "success": True,
            "doctor_id": doctor["doctor_id"],
            "from": start_day.isoformat(),
            "days": window,
            "availability_source": source,
            "dates": dates,
            "notice": AVAILABILITY_NOTICE,
        },
        200,
    )


def set_doctor_availability(
    doctor_id: Any,
    blocks: Any,
    *,
    recommender: DoctorRecommender | None = None,
    path: str | None = None,
) -> tuple[dict, int]:
    """Replace a doctor's availability with administrator-supplied blocks."""
    rec = recommender or default_recommender
    doctor = _doctor_for(rec, str(doctor_id or ""))
    if doctor is None:
        return {"success": False, "error": "Doctor not found."}, 404
    if not isinstance(blocks, list):
        return {"success": False, "error": "'blocks' must be a list."}, 400

    errors: list[str] = []
    cleaned: list[tuple] = []
    for index, block in enumerate(blocks):
        if not isinstance(block, dict):
            errors.append(f"Block {index + 1} must be an object.")
            continue
        try:
            day = int(block.get("day_of_week"))
        except (TypeError, ValueError):
            errors.append(f"Block {index + 1}: day_of_week must be 0 (Monday) to 6 (Sunday).")
            continue
        if not 0 <= day <= 6:
            errors.append(f"Block {index + 1}: day_of_week must be 0 (Monday) to 6 (Sunday).")
            continue
        start = _parse_time(block.get("start_time"))
        end = _parse_time(block.get("end_time"))
        if start is None or end is None:
            errors.append(f"Block {index + 1}: start_time and end_time must be HH:MM.")
            continue
        if _time_to_minutes(end) - _time_to_minutes(start) < SLOT_MINUTES_DEFAULT:
            errors.append(f"Block {index + 1}: the block must be at least {SLOT_MINUTES_DEFAULT} minutes long.")
            continue
        try:
            slot_minutes = int(block.get("slot_minutes", SLOT_MINUTES_DEFAULT))
        except (TypeError, ValueError):
            errors.append(f"Block {index + 1}: slot_minutes must be a whole number.")
            continue
        if slot_minutes < 5 or slot_minutes > 240:
            errors.append(f"Block {index + 1}: slot_minutes must be between 5 and 240.")
            continue
        consultation_type = block.get("consultation_type") or DEFAULT_CONSULTATION_TYPE
        if consultation_type not in CONSULTATION_TYPES:
            errors.append(
                f"Block {index + 1}: consultation_type must be one of {', '.join(CONSULTATION_TYPES)}."
            )
            continue
        cleaned.append(
            (
                doctor["doctor_id"],
                day,
                _minutes_to_hhmm(_time_to_minutes(start)),
                _minutes_to_hhmm(_time_to_minutes(end)),
                slot_minutes,
                consultation_type,
                AVAILABILITY_SOURCE_ADMIN,
            )
        )

    if errors:
        return {"success": False, "error": "Validation failed.", "errors": errors}, 400

    _init_schema(path)
    con = connect(path, isolation_level=None)
    try:
        con.execute("BEGIN IMMEDIATE")
        con.execute('DELETE FROM "doctor_availability" WHERE "Doctor_ID" = ?', (doctor["doctor_id"],))
        if cleaned:
            con.executemany(
                'INSERT INTO "doctor_availability" '
                '("Doctor_ID", "Day_Of_Week", "Start_Time", "End_Time", '
                '"Slot_Minutes", "Consultation_Type", "Source") VALUES (?, ?, ?, ?, ?, ?, ?)',
                cleaned,
            )
        con.execute("COMMIT")
    except sqlite3.IntegrityError:
        _safe_rollback(con)
        return {"success": False, "error": "Overlapping availability blocks are not allowed."}, 409
    except sqlite3.Error:
        _safe_rollback(con)
        return {"success": False, "error": "Could not update availability."}, 500
    finally:
        con.close()

    return (
        {
            "success": True,
            "doctor_id": doctor["doctor_id"],
            "blocks": len(cleaned),
            "availability_source": AVAILABILITY_SOURCE_ADMIN if cleaned else "none",
        },
        200,
    )


# -------------------------------------------------------------------- bookings
def _book(
    con: sqlite3.Connection,
    doctor_id: str,
    day: date_cls,
    start_hhmm: str,
    consultation_type: str,
    fee: Any,
    *,
    exclude_id: str | None = None,
) -> tuple[str | None, str | None]:
    """Validate a slot inside an open transaction. Returns (start, end) or (None, error)."""
    slots, _, _ = _build_day(con, doctor_id, day)
    match = next((slot for slot in slots if slot["start"] == start_hhmm), None)
    if match is None:
        return None, "not_a_slot"
    clash = con.execute(
        'SELECT 1 FROM "appointments" WHERE "Doctor_ID" = ? AND "Appointment_Date" = ? '
        'AND "Start_Time" = ? AND "Status" = ?'
        + (' AND "Appointment_ID" <> ?' if exclude_id else ""),
        tuple(
            [doctor_id, day.isoformat(), start_hhmm, STATUS_BOOKED]
            + ([exclude_id] if exclude_id else [])
        ),
    ).fetchone()
    if clash is not None:
        return None, "conflict"
    return match["start"], match["end"]


def _validate_booking_request(
    date_value: Any, start_time: Any, consultation_type: Any
) -> tuple[date_cls | None, str | None, str, str | None]:
    day = _parse_date(date_value)
    if day is None:
        return None, None, "", "Appointment date must be a valid YYYY-MM-DD date."
    start = _parse_time(start_time)
    if start is None:
        return None, None, "", "Start time must be a valid HH:MM time."
    today = date_cls.today()
    if day < today:
        return None, None, "", "Appointment date must be today or later."
    if (day - today).days > MAX_ADVANCE_DAYS:
        return (
            None,
            None,
            "",
            f"Appointments can be booked at most {MAX_ADVANCE_DAYS} days in advance.",
        )
    ctype = consultation_type or DEFAULT_CONSULTATION_TYPE
    if not isinstance(ctype, str) or ctype not in CONSULTATION_TYPES:
        return None, None, "", f"consultation_type must be one of {', '.join(CONSULTATION_TYPES)}."
    return day, _minutes_to_hhmm(_time_to_minutes(start)), ctype, None


def create_appointment(
    patient_id: Any,
    doctor_id: Any,
    date_value: Any,
    start_time: Any,
    consultation_type: Any = None,
    *,
    recommender: DoctorRecommender | None = None,
    path: str | None = None,
) -> tuple[dict, int]:
    """Create a persistent appointment. Returns ``(payload, http_status)``."""
    if not patient_id or not str(patient_id).strip():
        return {"success": False, "error": "Patient authentication required."}, 401
    patient_id = str(patient_id)

    rec = recommender or default_recommender
    doctor = _doctor_for(rec, str(doctor_id or ""))
    if doctor is None:
        return {"success": False, "error": "Doctor not found."}, 404

    day, start_hhmm, ctype, error = _validate_booking_request(
        date_value, start_time, consultation_type
    )
    if error:
        return {"success": False, "error": error}, 400

    _init_schema(path)
    con = connect(path, isolation_level=None)
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            'SELECT "Consultation_Fee_INR" FROM "doctors" WHERE "Doctor_ID" = ?',
            (doctor["doctor_id"],),
        ).fetchone()
        if row is None:
            _safe_rollback(con)
            return {"success": False, "error": "Doctor not found."}, 404

        _ensure_availability(con, doctor["doctor_id"])
        slot_start, slot_end = _book(
            con, doctor["doctor_id"], day, start_hhmm, ctype, row["Consultation_Fee_INR"]
        )
        if slot_start is None:
            _safe_rollback(con)
            if slot_end == "not_a_slot":
                return {
                    "success": False,
                    "error": "The selected time is not one of this doctor's appointment slots.",
                }, 400
            return {
                "success": False,
                "error": "That slot has just been booked by someone else. Please choose another.",
            }, 409

        appointment_id = _new_appointment_id(con)
        con.execute(
            'INSERT INTO "appointments" '
            '("Appointment_ID", "Patient_ID", "Doctor_ID", "Appointment_Date", "Start_Time", '
            '"End_Time", "Status", "Consultation_Type", "Fee_INR", "Created_At") '
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                appointment_id,
                patient_id,
                doctor["doctor_id"],
                day.isoformat(),
                slot_start,
                slot_end,
                STATUS_BOOKED,
                ctype,
                row["Consultation_Fee_INR"],
                _now_iso(),
            ),
        )
        con.execute("COMMIT")
    except sqlite3.IntegrityError:
        _safe_rollback(con)
        return {
            "success": False,
            "error": "That slot has just been booked by someone else. Please choose another.",
        }, 409
    except sqlite3.OperationalError:
        _safe_rollback(con)
        return {"success": False, "error": "The booking system is busy. Please try again."}, 503
    except sqlite3.Error:
        _safe_rollback(con)
        return {"success": False, "error": "Could not create the appointment."}, 500
    finally:
        con.close()

    fresh = rec.get_doctor(doctor["doctor_id"])
    return (
        {
            "success": True,
            "appointment": _appointment_payload(
                {
                    "Appointment_ID": appointment_id,
                    "Patient_ID": patient_id,
                    "Doctor_ID": doctor["doctor_id"],
                    "Appointment_Date": day.isoformat(),
                    "Start_Time": slot_start,
                    "End_Time": slot_end,
                    "Status": STATUS_BOOKED,
                    "Consultation_Type": ctype,
                    "Fee_INR": row["Consultation_Fee_INR"],
                    "Created_At": _now_iso(),
                    "Cancelled_At": None,
                    "Rescheduled_At": None,
                },
                fresh,
            ),
        },
        201,
    )


def _owned_appointment(con: sqlite3.Connection, patient_id: str, appointment_id: str):
    return con.execute(
        'SELECT * FROM "appointments" WHERE "Appointment_ID" = ? AND "Patient_ID" = ?',
        (appointment_id, patient_id),
    ).fetchone()


def cancel_appointment(
    patient_id: Any,
    appointment_id: Any,
    *,
    recommender: DoctorRecommender | None = None,
    path: str | None = None,
) -> tuple[dict, int]:
    if not patient_id or not str(patient_id).strip():
        return {"success": False, "error": "Patient authentication required."}, 401
    if not appointment_id or not str(appointment_id).strip():
        return {"success": False, "error": "Appointment ID is required."}, 400
    patient_id = str(patient_id)
    appointment_id = str(appointment_id).strip()

    rec = recommender or default_recommender
    _init_schema(path)
    con = connect(path, isolation_level=None)
    try:
        con.execute("BEGIN IMMEDIATE")
        row = _owned_appointment(con, patient_id, appointment_id)
        if row is None:
            _safe_rollback(con)
            return {"success": False, "error": "Appointment not found."}, 404
        if row["Status"] != STATUS_BOOKED:
            _safe_rollback(con)
            return {"success": False, "error": "Only booked appointments can be cancelled."}, 409
        cancelled_at = _now_iso()
        con.execute(
            'UPDATE "appointments" SET "Status" = ?, "Cancelled_At" = ? '
            'WHERE "Appointment_ID" = ?',
            (STATUS_CANCELLED, cancelled_at, appointment_id),
        )
        con.execute("COMMIT")
        updated = dict(row)
        updated["Status"] = STATUS_CANCELLED
        updated["Cancelled_At"] = cancelled_at
    except sqlite3.Error:
        _safe_rollback(con)
        return {"success": False, "error": "Could not cancel the appointment."}, 500
    finally:
        con.close()

    return (
        {"success": True, "appointment": _appointment_payload(updated, _doctor_for(rec, updated["Doctor_ID"]))},
        200,
    )


def reschedule_appointment(
    patient_id: Any,
    appointment_id: Any,
    date_value: Any,
    start_time: Any,
    *,
    recommender: DoctorRecommender | None = None,
    path: str | None = None,
) -> tuple[dict, int]:
    if not patient_id or not str(patient_id).strip():
        return {"success": False, "error": "Patient authentication required."}, 401
    if not appointment_id or not str(appointment_id).strip():
        return {"success": False, "error": "Appointment ID is required."}, 400
    patient_id = str(patient_id)
    appointment_id = str(appointment_id).strip()

    day, start_hhmm, _ctype, error = _validate_booking_request(date_value, start_time, None)
    if error:
        return {"success": False, "error": error}, 400

    rec = recommender or default_recommender
    _init_schema(path)
    con = connect(path, isolation_level=None)
    try:
        con.execute("BEGIN IMMEDIATE")
        row = _owned_appointment(con, patient_id, appointment_id)
        if row is None:
            _safe_rollback(con)
            return {"success": False, "error": "Appointment not found."}, 404
        if row["Status"] != STATUS_BOOKED:
            _safe_rollback(con)
            return {"success": False, "error": "Only booked appointments can be rescheduled."}, 409

        if row["Doctor_ID"] != "" and con.execute(
            'SELECT 1 FROM "doctors" WHERE "Doctor_ID" = ?', (row["Doctor_ID"],)
        ).fetchone() is None:
            _safe_rollback(con)
            return {"success": False, "error": "The doctor for this appointment no longer exists."}, 409

        _ensure_availability(con, row["Doctor_ID"])
        slot_start, slot_end = _book(
            con,
            row["Doctor_ID"],
            day,
            start_hhmm,
            row["Consultation_Type"] or DEFAULT_CONSULTATION_TYPE,
            row["Fee_INR"],
            exclude_id=appointment_id,
        )
        if slot_start is None:
            _safe_rollback(con)
            if slot_end == "not_a_slot":
                return {
                    "success": False,
                    "error": "The selected time is not one of this doctor's appointment slots.",
                }, 400
            return {
                "success": False,
                "error": "That slot has just been booked by someone else. Please choose another.",
            }, 409

        rescheduled_at = _now_iso()
        con.execute(
            'UPDATE "appointments" SET "Appointment_Date" = ?, "Start_Time" = ?, "End_Time" = ?, '
            '"Rescheduled_At" = ? WHERE "Appointment_ID" = ?',
            (day.isoformat(), slot_start, slot_end, rescheduled_at, appointment_id),
        )
        con.execute("COMMIT")
        updated = dict(row)
        updated["Appointment_Date"] = day.isoformat()
        updated["Start_Time"] = slot_start
        updated["End_Time"] = slot_end
        updated["Rescheduled_At"] = rescheduled_at
    except sqlite3.IntegrityError:
        _safe_rollback(con)
        return {
            "success": False,
            "error": "That slot has just been booked by someone else. Please choose another.",
        }, 409
    except sqlite3.OperationalError:
        _safe_rollback(con)
        return {"success": False, "error": "The booking system is busy. Please try again."}, 503
    except sqlite3.Error:
        _safe_rollback(con)
        return {"success": False, "error": "Could not reschedule the appointment."}, 500
    finally:
        con.close()

    return (
        {"success": True, "appointment": _appointment_payload(updated, _doctor_for(rec, updated["Doctor_ID"]))},
        200,
    )


def list_patient_appointments(
    patient_id: Any,
    *,
    recommender: DoctorRecommender | None = None,
    path: str | None = None,
    now: datetime | None = None,
) -> dict:
    """Return a patient's appointments grouped into upcoming / past / cancelled."""
    if not patient_id or not str(patient_id).strip():
        return {"success": False, "error": "Patient authentication required."}
    patient_id = str(patient_id)
    now = now or datetime.now()

    rec = recommender or default_recommender
    con = connect(path)
    try:
        ensure_schema(con)
        rows = con.execute(
            'SELECT * FROM "appointments" WHERE "Patient_ID" = ? '
            'ORDER BY "Appointment_Date" ASC, "Start_Time" ASC',
            (patient_id,),
        ).fetchall()
    except sqlite3.Error:
        return {"success": False, "error": "Could not read appointments."}
    finally:
        con.close()

    doctors: dict[str, dict | None] = {}
    upcoming: list[dict] = []
    past: list[dict] = []
    cancelled: list[dict] = []

    for row in rows:
        doctor_id = row["Doctor_ID"]
        if doctor_id not in doctors:
            doctors[doctor_id] = _doctor_for(rec, doctor_id)
        payload = _appointment_payload(row, doctors[doctor_id])
        if row["Status"] == STATUS_CANCELLED:
            payload["category"] = "cancelled"
            cancelled.append(payload)
            continue
        end = _parse_time(row["End_Time"]) or _parse_time(row["Start_Time"])
        end_dt = datetime.combine(_parse_date(row["Appointment_Date"]), end) if end else None
        if end_dt is not None and end_dt < now:
            payload["category"] = "past"
            past.append(payload)
        else:
            payload["category"] = "upcoming"
            upcoming.append(payload)

    upcoming.sort(key=lambda item: (item["date"], item["start_time"]))
    past.sort(key=lambda item: (item["date"], item["start_time"]), reverse=True)
    cancelled.sort(key=lambda item: (item["date"], item["start_time"]), reverse=True)

    return {
        "success": True,
        "patient_id": patient_id,
        "upcoming": upcoming,
        "past": past,
        "cancelled": cancelled,
        "counts": {
            "upcoming": len(upcoming),
            "past": len(past),
            "cancelled": len(cancelled),
            "total": len(rows),
        },
    }


def get_patient_appointment(
    patient_id: Any,
    appointment_id: Any,
    *,
    recommender: DoctorRecommender | None = None,
    path: str | None = None,
) -> tuple[dict, int]:
    if not patient_id or not str(patient_id).strip():
        return {"success": False, "error": "Patient authentication required."}, 401
    if not appointment_id or not str(appointment_id).strip():
        return {"success": False, "error": "Appointment ID is required."}, 400

    rec = recommender or default_recommender
    con = connect(path)
    try:
        ensure_schema(con)
        row = _owned_appointment(con, str(patient_id), str(appointment_id).strip())
    except sqlite3.Error:
        return {"success": False, "error": "Could not read the appointment."}, 500
    finally:
        con.close()

    if row is None:
        return {"success": False, "error": "Appointment not found."}, 404
    return {"success": True, "appointment": _appointment_payload(row, _doctor_for(rec, row["Doctor_ID"]))}, 200
