"""SQLite-backed doctor lookup and administration for the CareGraph demo.

This module is the single authoritative access layer for the ``doctors`` table in
``Data/caregraph_doctors.db``. It provides:

* strict specialty matching,
* Haversine (great-circle) distance calculation,
* distance / fee / rating filters,
* configurable sorting,
* safe pagination,
* admin CRUD used by the protected admin API.

Doctor directory records are synthetic demo data. Do not use them as real referrals.
"""
from __future__ import annotations

import math
import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent
DEFAULT_DB = PROJECT_ROOT / "Data" / "caregraph_doctors.db"

TABLE_NAME = "doctors"

# Column names exactly as they exist in the SQLite schema (verified against the DB).
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

EARTH_RADIUS_KM = 6371.0088

# Only Mumbai/Thane are present in the synthetic directory.
ALLOWED_CITIES = {"mumbai", "thane"}

SORT_KEYS = ("recommended", "rating", "fees", "distance", "experience")
SORT_ALIASES = {
    "recommended": "recommended",
    "rating": "rating",
    "fees": "fees",
    "fee": "fees",
    "distance": "distance",
    "nearest": "distance",
    "experience": "experience",
}

DIRECTORY_NOTICE = (
    "Demo directory only: doctor profiles are synthetic, and clinic coordinates are "
    "approximate. Verify real providers before patient use."
)


def _key(value: Any) -> str:
    value = str(value or "").strip().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value)).strip()


ALIASES = {
    "internal medcine": "internal medicine",
    "internal medicine": "internal medicine",
    "dermatologists": "dermatologist",
    "rheumatologists": "rheumatologist",
    "gynaecologist": "gynecologist",
    "ent specialist": "otolaryngologist",
}
DISPLAY = {
    "allergist": "Allergist", "cardiologist": "Cardiologist",
    "dermatologist": "Dermatologist", "endocrinologist": "Endocrinologist",
    "gastroenterologist": "Gastroenterologist", "gynecologist": "Gynecologist",
    "hepatologist": "Hepatologist", "internal medicine": "Internal Medicine",
    "neurologist": "Neurologist", "osteopathic": "Osteopathic",
    "otolaryngologist": "Otolaryngologist", "pediatrician": "Pediatrician",
    "phlebologist": "Phlebologist", "pulmonologist": "Pulmonologist",
    "rheumatologist": "Rheumatologist",
}


def canonical_specialty(value: Any) -> str:
    """Normalise capitalization/whitespace and known spelling variants of a specialty."""
    k = ALIASES.get(_key(value), _key(value))
    return DISPLAY.get(k, str(value or "").strip())


def haversine_km(lat1: Any, lon1: Any, lat2: Any, lon2: Any) -> float | None:
    """Great-circle distance in kilometres, or ``None`` for invalid/missing input."""
    values = (lat1, lon1, lat2, lon2)
    if any(v is None for v in values):
        return None
    try:
        lat1, lon1, lat2, lon2 = (float(v) for v in values)
    except (TypeError, ValueError):
        return None
    if any(math.isnan(v) or math.isinf(v) for v in (lat1, lon1, lat2, lon2)):
        return None
    if not (-90.0 <= lat1 <= 90.0 and -90.0 <= lat2 <= 90.0):
        return None
    if not (-180.0 <= lon1 <= 180.0 and -180.0 <= lon2 <= 180.0):
        return None

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(d_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    )
    a = min(1.0, max(0.0, a))
    return 2.0 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _coerce_number(
    value: Any,
    name: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    integer: bool = False,
) -> tuple[float | int | None, str | None]:
    """Return ``(number, None)`` or ``(None, error_message)``. Blank input means 'not supplied'."""
    if value is None:
        return None, None
    if isinstance(value, str) and not value.strip():
        return None, None
    if isinstance(value, bool):
        return None, f"{name} must be a number."
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, f"{name} must be a number."
    if math.isnan(number) or math.isinf(number):
        return None, f"{name} must be a finite number."
    if minimum is not None and number < minimum:
        return None, f"{name} must be at least {minimum}."
    if maximum is not None and number > maximum:
        return None, f"{name} must be at most {maximum}."
    if integer:
        if abs(number - round(number)) > 1e-9:
            return None, f"{name} must be a whole number."
        number = int(round(number))
    return number, None


def validate_coordinates(
    latitude: Any, longitude: Any, *, prefix: str = "user_"
) -> tuple[float | None, float | None, str | None]:
    """Validate a user coordinate pair supplied together. Returns (lat, lon, error)."""
    lat_given = latitude is not None and not (isinstance(latitude, str) and not latitude.strip())
    lon_given = longitude is not None and not (isinstance(longitude, str) and not longitude.strip())

    if not lat_given and not lon_given:
        return None, None, None
    if lat_given != lon_given:
        return None, None, (
            f"{prefix}latitude and {prefix}longitude must be supplied together."
        )

    lat, err = _coerce_number(latitude, f"{prefix}latitude", minimum=-90.0, maximum=90.0)
    if err:
        return None, None, err
    lon, err = _coerce_number(longitude, f"{prefix}longitude", minimum=-180.0, maximum=180.0)
    if err:
        return None, None, err
    return float(lat), float(lon), None


def _display_number(value: Any, digits: int | None = None) -> Any:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    if digits is not None:
        return round(number, digits)
    return int(number) if number.is_integer() else number


def _row_to_doctor(row: sqlite3.Row) -> dict:
    return {
        "doctor_id": str(row["Doctor_ID"]),
        "name": str(row["Doctor_Name"]),
        "specialization": canonical_specialty(row["Specialization"]),
        "age": _display_number(row["Age"]),
        "experience_years": _display_number(row["Experience_Years"]),
        "description": str(row["Description"]) if row["Description"] is not None else "",
        "address": str(row["Address"]) if row["Address"] is not None else "",
        "city": str(row["City"]) if row["City"] is not None else "",
        "state": str(row["State"]) if row["State"] is not None else "",
        "latitude": _display_number(row["Latitude"], 6),
        "longitude": _display_number(row["Longitude"], 6),
        "consultation_fee_inr": _display_number(row["Consultation_Fee_INR"]),
        "rating_out_of_5": _display_number(row["Rating_Out_of_5"], 1),
        "data_status": str(row["Data_Status"]) if row["Data_Status"] is not None else "",
    }


def _neg(value: Any) -> float:
    """Descending sort component; missing values sort last under an ascending sort."""
    if value is None:
        return float("inf")
    try:
        return -float(value)
    except (TypeError, ValueError):
        return float("inf")


def _pos(value: Any) -> float:
    """Ascending sort component; missing values sort last."""
    if value is None:
        return float("inf")
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("inf")


def _sort_key(doctor: dict, sort_key: str, has_coords: bool) -> tuple:
    rating = doctor.get("rating_out_of_5")
    fee = doctor.get("consultation_fee_inr")
    experience = doctor.get("experience_years")
    distance = doctor.get("distance_km")
    doctor_id = str(doctor.get("doctor_id") or "")

    if sort_key == "rating":
        return (_neg(rating), _pos(fee), doctor_id)
    if sort_key == "fees":
        return (_pos(fee), _neg(rating), doctor_id)
    if sort_key == "distance":
        return (_pos(distance), _neg(rating), doctor_id)
    if sort_key == "experience":
        return (_neg(experience), _neg(rating), doctor_id)
    # "recommended": rating desc, fee asc, distance asc (if available), experience desc.
    if has_coords:
        return (_neg(rating), _pos(fee), _pos(distance), _neg(experience), doctor_id)
    return (_neg(rating), _pos(fee), _neg(experience), doctor_id)


class DoctorRecommender:
    """Query and manage the SQLite doctor directory."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path or os.getenv("CAREGRAPH_DOCTOR_DB", str(DEFAULT_DB)))
        if not self.db_path.is_absolute():
            self.db_path = (PROJECT_ROOT / self.db_path).resolve()
        self.load_error: str | None = None
        if not self.db_path.exists():
            self.load_error = f"Doctor database not found: {self.db_path}"

    # ------------------------------------------------------------------ helpers
    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.db_path))
        con.row_factory = sqlite3.Row
        return con

    @contextmanager
    def _connection(self):
        """One connection per operation: commits on success and always closes."""
        con = self._connect()
        try:
            with con:
                yield con
        finally:
            con.close()

    def _unavailable(self) -> dict:
        return {
            "success": False,
            "error": "Doctor database is unavailable.",
            "details": self.load_error,
            "doctors": [],
        }

    # ------------------------------------------------------------------ search
    def search(
        self,
        specialty: str,
        city: str | None = None,
        limit: int = 10,
        sort_by: str = "recommended",
        user_latitude: Any = None,
        user_longitude: Any = None,
        max_distance_km: Any = None,
        max_fee: Any = None,
        min_rating: Any = None,
        page: Any = None,
        page_size: Any = None,
    ) -> dict:
        """Find doctors for one specialty, with optional distance/fee/rating filters."""
        if self.load_error:
            return self._unavailable()

        if specialty is None or not str(specialty).strip():
            return {
                "success": False,
                "error": "The 'specialty' parameter is required.",
                "doctors": [],
            }

        requested = canonical_specialty(specialty)

        if city:
            city_key = _key(city)
            if city_key not in ALLOWED_CITIES:
                return {
                    "success": False,
                    "error": "City must be Mumbai or Thane.",
                    "doctors": [],
                }
            city = "Mumbai" if city_key == "mumbai" else "Thane"

        # --- sorting
        sort_key = SORT_ALIASES.get(str(sort_by or "recommended").strip().lower())
        if sort_key is None:
            return {
                "success": False,
                "error": "sort must be one of: " + ", ".join(SORT_KEYS) + ".",
                "doctors": [],
            }

        # --- coordinates
        lat, lon, coord_error = validate_coordinates(user_latitude, user_longitude)
        if coord_error:
            return {"success": False, "error": coord_error, "doctors": []}
        has_coords = lat is not None and lon is not None

        if sort_key == "distance" and not has_coords:
            return {
                "success": False,
                "error": (
                    "Location is required to sort by nearest doctor: supply valid "
                    "user_latitude and user_longitude."
                ),
                "doctors": [],
            }

        # --- numeric filters
        distance_limit, err = _coerce_number(
            max_distance_km, "max_distance_km", minimum=0.0
        )
        if err:
            return {"success": False, "error": err, "doctors": []}
        if distance_limit is not None and not has_coords:
            return {
                "success": False,
                "error": "max_distance_km requires valid user_latitude and user_longitude.",
                "doctors": [],
            }

        fee_limit, err = _coerce_number(max_fee, "max_fee", minimum=0.0)
        if err:
            return {"success": False, "error": err, "doctors": []}

        rating_floor, err = _coerce_number(
            min_rating, "min_rating", minimum=0.0, maximum=5.0
        )
        if err:
            return {"success": False, "error": err, "doctors": []}

        # --- legacy limit (used when no pagination is requested)
        try:
            legacy_limit = max(1, min(int(limit), 50))
        except (TypeError, ValueError):
            legacy_limit = 10

        want_pagination = page is not None
        if want_pagination:
            page_number, err = _coerce_number(page, "page", minimum=1, integer=True)
            if err:
                return {"success": False, "error": err, "doctors": []}
            page_number = int(page_number)
            if page_size is None:
                size = 20
            else:
                size_value, err = _coerce_number(
                    page_size, "page_size", minimum=1, maximum=100, integer=True
                )
                if err:
                    return {"success": False, "error": err, "doctors": []}
                size = int(size_value)
        else:
            page_number = None
            size = None

        # --- candidate query (specialty + city are indexed columns)
        sql = (
            f'SELECT * FROM "{TABLE_NAME}" '
            "WHERE lower(trim(Specialization)) = lower(trim(?))"
        )
        params: list[Any] = [requested]
        if city:
            sql += " AND lower(trim(City)) = lower(trim(?))"
            params.append(city)

        try:
            with self._connection() as con:
                rows = con.execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            return {
                "success": False,
                "error": "Doctor database query failed.",
                "details": str(exc),
                "doctors": [],
            }

        doctors: list[dict] = []
        for row in rows:
            doctor = _row_to_doctor(row)
            if has_coords:
                doctor["distance_km"] = _display_number(
                    haversine_km(lat, lon, doctor["latitude"], doctor["longitude"]), 2
                )
            else:
                doctor["distance_km"] = None
            doctors.append(doctor)

        # --- filters (applied after distance calculation, before sorting/pagination)
        if distance_limit is not None:
            doctors = [
                d for d in doctors
                if d["distance_km"] is not None and d["distance_km"] <= distance_limit
            ]
        if fee_limit is not None:
            doctors = [
                d for d in doctors
                if d["consultation_fee_inr"] is not None
                and d["consultation_fee_inr"] <= fee_limit
            ]
        if rating_floor is not None:
            doctors = [
                d for d in doctors
                if d["rating_out_of_5"] is not None
                and d["rating_out_of_5"] >= rating_floor
            ]

        doctors.sort(key=lambda d: _sort_key(d, sort_key, has_coords))
        total = len(doctors)

        # --- pagination last
        if want_pagination:
            start = (page_number - 1) * size
            page_doctors = doctors[start:start + size]
        else:
            page_doctors = doctors[:legacy_limit]
            page_number = 1 if doctors else 1
            size = legacy_limit

        result = {
            "success": True,
            "requested_specialty": requested,
            "specialty": requested,
            "city_filter": city,
            "sort": sort_key,
            "filters": {
                "user_latitude": lat,
                "user_longitude": lon,
                "max_distance_km": distance_limit,
                "max_fee": fee_limit,
                "min_rating": rating_floor,
            },
            "count": len(page_doctors),
            "total": total,
            "page": page_number,
            "page_size": size,
            "doctors": page_doctors,
            "notice": DIRECTORY_NOTICE,
        }
        if total == 0:
            if distance_limit is not None:
                result["message"] = (
                    "No doctors are currently available for this specialty with the selected filters."
                )
            else:
                result["message"] = (
                    f"No doctors are currently available for this specialty "
                    f"'{requested}' with the selected filters."
                )
        return result

    # ------------------------------------------------------------------ admin reads
    def list_specialties(self) -> list[str]:
        if self.load_error:
            return []
        try:
            with self._connection() as con:
                rows = con.execute(
                    f'SELECT DISTINCT Specialization FROM "{TABLE_NAME}" '
                    "WHERE Specialization IS NOT NULL AND trim(Specialization) <> '' "
                    "ORDER BY lower(Specialization)"
                ).fetchall()
        except sqlite3.Error:
            return []
        return [canonical_specialty(row[0]) for row in rows]

    def list_cities(self) -> list[str]:
        if self.load_error:
            return []
        try:
            with self._connection() as con:
                rows = con.execute(
                    f'SELECT DISTINCT City FROM "{TABLE_NAME}" '
                    "WHERE City IS NOT NULL AND trim(City) <> '' ORDER BY lower(City)"
                ).fetchall()
        except sqlite3.Error:
            return []
        return [str(row[0]) for row in rows]

    def get_doctor(self, doctor_id: str) -> dict | None:
        if self.load_error:
            return None
        with self._connection() as con:
            row = con.execute(
                f'SELECT * FROM "{TABLE_NAME}" WHERE Doctor_ID = ?', (str(doctor_id),)
            ).fetchone()
        return _row_to_doctor(row) if row else None

    def list_doctors_admin(
        self,
        *,
        q: str | None = None,
        specialization: str | None = None,
        city: str | None = None,
        page: Any = 1,
        page_size: Any = 20,
    ) -> dict:
        if self.load_error:
            return self._unavailable()

        where: list[str] = []
        params: list[Any] = []

        if q and str(q).strip():
            like = f"%{str(q).strip().lower()}%"
            where.append(
                "(lower(Doctor_Name) LIKE ? OR lower(Doctor_ID) LIKE ? "
                "OR lower(City) LIKE ? OR lower(Specialization) LIKE ?)"
            )
            params.extend([like, like, like, like])
        if specialization and str(specialization).strip():
            where.append("lower(trim(Specialization)) = lower(trim(?))")
            params.append(canonical_specialty(specialization))
        if city and str(city).strip():
            where.append("lower(trim(City)) = lower(trim(?))")
            params.append(str(city).strip())

        clause = (" WHERE " + " AND ".join(where)) if where else ""

        try:
            page_number = max(1, int(page))
        except (TypeError, ValueError):
            page_number = 1
        try:
            size = max(1, min(int(page_size), 100))
        except (TypeError, ValueError):
            size = 20

        try:
            with self._connection() as con:
                total = con.execute(
                    f'SELECT COUNT(*) FROM "{TABLE_NAME}"{clause}', params
                ).fetchone()[0]
                rows = con.execute(
                    f'SELECT * FROM "{TABLE_NAME}"{clause} '
                    "ORDER BY Doctor_ID ASC LIMIT ? OFFSET ?",
                    params + [size, (page_number - 1) * size],
                ).fetchall()
        except sqlite3.Error as exc:
            return {
                "success": False,
                "error": "Doctor database query failed.",
                "details": str(exc),
                "doctors": [],
            }

        return {
            "success": True,
            "count": len(rows),
            "total": total,
            "page": page_number,
            "page_size": size,
            "doctors": [_row_to_doctor(row) for row in rows],
        }

    # ------------------------------------------------------------------ admin writes
    def create_doctor(self, payload: dict) -> tuple[dict, int]:
        if self.load_error:
            return self._unavailable(), 503

        values, errors = validate_doctor_payload(payload, require_all=True)
        if errors:
            return {"success": False, "error": "Validation failed.", "errors": errors}, 400

        if self.get_doctor(values["Doctor_ID"]) is not None:
            return {
                "success": False,
                "error": f"A doctor with ID '{values['Doctor_ID']}' already exists.",
            }, 409

        columns = [c for c in DOCTOR_COLUMNS if c in values]
        placeholders = ", ".join("?" for _ in columns)
        quoted = ", ".join(f'"{c}"' for c in columns)
        try:
            with self._connection() as con:
                con.execute(
                    f'INSERT INTO "{TABLE_NAME}" ({quoted}) VALUES ({placeholders})',
                    [values[c] for c in columns],
                )
        except sqlite3.IntegrityError as exc:
            return {"success": False, "error": "Could not create doctor.", "details": str(exc)}, 409
        except sqlite3.Error as exc:
            return {"success": False, "error": "Could not create doctor.", "details": str(exc)}, 500

        return {"success": True, "doctor": self.get_doctor(values["Doctor_ID"])}, 201

    def update_doctor(self, doctor_id: str, payload: dict) -> tuple[dict, int]:
        if self.load_error:
            return self._unavailable(), 503

        existing = self.get_doctor(doctor_id)
        if existing is None:
            return {"success": False, "error": "Doctor not found."}, 404

        values, errors = validate_doctor_payload(payload, require_all=False)
        if errors:
            return {"success": False, "error": "Validation failed.", "errors": errors}, 400

        # Never allow the primary key to be silently rewritten to a duplicate.
        if "Doctor_ID" in values and str(values["Doctor_ID"]) != str(doctor_id):
            if self.get_doctor(values["Doctor_ID"]) is not None:
                return {
                    "success": False,
                    "error": f"A doctor with ID '{values['Doctor_ID']}' already exists.",
                }, 409

        if not values:
            return {"success": True, "doctor": existing}, 200

        assignments = ", ".join(f'"{c}" = ?' for c in values)
        params = list(values.values()) + [str(doctor_id)]
        try:
            with self._connection() as con:
                con.execute(
                    f'UPDATE "{TABLE_NAME}" SET {assignments} WHERE Doctor_ID = ?', params
                )
        except sqlite3.Error as exc:
            return {"success": False, "error": "Could not update doctor.", "details": str(exc)}, 500

        new_id = str(values.get("Doctor_ID", doctor_id))
        return {"success": True, "doctor": self.get_doctor(new_id)}, 200

    def delete_doctor(self, doctor_id: str) -> tuple[dict, int]:
        if self.load_error:
            return self._unavailable(), 503

        if self.get_doctor(doctor_id) is None:
            return {"success": False, "error": "Doctor not found."}, 404

        try:
            with self._connection() as con:
                con.execute(
                    f'DELETE FROM "{TABLE_NAME}" WHERE Doctor_ID = ?', (str(doctor_id),)
                )
        except sqlite3.Error as exc:
            return {"success": False, "error": "Could not delete doctor.", "details": str(exc)}, 500

        return {"success": True, "deleted_doctor_id": str(doctor_id)}, 200


def validate_doctor_payload(
    payload: dict, *, require_all: bool
) -> tuple[dict, list[str]]:
    """Validate/normalise a doctor payload.

    Returns ``(values_by_column, errors)``. Only keys present in ``payload`` are
    returned, so partial updates never overwrite unrelated fields.
    """
    if not isinstance(payload, dict):
        return {}, ["Request body must be a JSON object."]

    errors: list[str] = []
    values: dict[str, Any] = {}

    def text_field(column: str, label: str, must_be_present: bool, max_len: int = 2000) -> None:
        """Validate a text column.

        ``must_be_present`` is only true while creating: a partial update may omit
        a field, but a field that *is* supplied must never be blank.
        """
        if column not in payload:
            if must_be_present:
                errors.append(f"{label} is required.")
            return
        raw = payload[column]
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            errors.append(f"{label} must not be empty.")
            return
        text = str(raw).strip()
        if len(text) > max_len:
            errors.append(f"{label} must be at most {max_len} characters.")
            return
        values[column] = text

    text_field("Doctor_ID", "Doctor_ID", require_all, 64)
    text_field("Doctor_Name", "Doctor_Name", require_all)
    text_field("Specialization", "Specialization", require_all)
    text_field("Description", "Description", False)
    text_field("Address", "Address", False)
    text_field("City", "City", False, 120)
    text_field("State", "State", False, 120)
    text_field("Data_Status", "Data_Status", False, 500)

    def number_field(
        column: str,
        label: str,
        *,
        minimum: float | None = None,
        maximum: float | None = None,
        integer: bool = False,
    ) -> None:
        if column not in payload:
            return
        number, err = _coerce_number(
            payload[column], label, minimum=minimum, maximum=maximum, integer=integer
        )
        if err:
            errors.append(err)
        elif number is not None:
            values[column] = number

    number_field("Age", "Age", minimum=0, maximum=120, integer=True)
    number_field("Experience_Years", "Experience_Years", minimum=0, maximum=80, integer=True)
    number_field("Consultation_Fee_INR", "Consultation_Fee_INR", minimum=0, maximum=1_000_000)
    number_field("Rating_Out_of_5", "Rating_Out_of_5", minimum=0, maximum=5)
    number_field("Latitude", "Latitude", minimum=-90, maximum=90)
    number_field("Longitude", "Longitude", minimum=-180, maximum=180)

    # Coordinates must be supplied together (both or neither).
    has_lat = "Latitude" in payload and payload["Latitude"] not in (None, "")
    has_lon = "Longitude" in payload and payload["Longitude"] not in (None, "")
    if has_lat != has_lon:
        errors.append("Latitude and Longitude must be supplied together.")

    return values, errors


doctor_recommender = DoctorRecommender()
