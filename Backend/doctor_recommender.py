"""Specialty-based doctor directory lookup for the CareGraph demo.

Doctor directory records are synthetic demo data. Do not use as real referrals.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import pandas as pd

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent
DEFAULT_DIRECTORY = PROJECT_ROOT / "Data" / "CareGraph_Doctor_Directory_Mumbai_Thane_Synthetic.xlsx"

REQUIRED_COLUMNS = {
    "Doctor_ID", "Doctor_Name", "Specialization", "Age", "Experience_Years",
    "Description", "Address", "City", "State", "Latitude", "Longitude",
    "Consultation_Fee_INR", "Rating_Out_of_5", "Data_Status",
}


def _key(value: Any) -> str:
    value = str(value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


# Canonicalize common spelling/label variants between the model mapping and directory.
SPECIALTY_ALIASES = {
    "internal medcine": "internal medicine",
    "internal medicine": "internal medicine",
    "dermatologists": "dermatologist",
    "rheumatologists": "rheumatologist",
    "gynecologist": "gynecologist",
    "gynaecologist": "gynecologist",
    "ent specialist": "otolaryngologist",
}


def canonical_specialty(value: Any) -> str:
    key = _key(value)
    key = SPECIALTY_ALIASES.get(key, key)
    # Return display style from the directory where possible.
    display = {
        "allergist": "Allergist", "cardiologist": "Cardiologist",
        "dermatologist": "Dermatologist", "endocrinologist": "Endocrinologist",
        "gastroenterologist": "Gastroenterologist", "gynecologist": "Gynecologist",
        "hepatologist": "Hepatologist", "internal medicine": "Internal Medicine",
        "neurologist": "Neurologist", "osteopathic": "Osteopathic",
        "otolaryngologist": "Otolaryngologist", "pediatrician": "Pediatrician",
        "phlebologist": "Phlebologist", "pulmonologist": "Pulmonologist",
        "rheumatologist": "Rheumatologist",
    }
    return display.get(key, str(value or "").strip())


class DoctorRecommender:
    def __init__(self, directory_path: str | Path | None = None):
        path = Path(directory_path or os.getenv("CAREGRAPH_DOCTOR_DIRECTORY", str(DEFAULT_DIRECTORY)))
        if not path.is_absolute():
            path = (PROJECT_ROOT / path).resolve()
        self.directory_path = path
        self.load_error: str | None = None
        self.doctors = pd.DataFrame()
        try:
            if not path.exists():
                raise FileNotFoundError(
                    f"Doctor directory not found at {path}. Put the Excel file in CareGraph/Data "
                    "or set CAREGRAPH_DOCTOR_DIRECTORY to its full path."
                )
            df = pd.read_excel(path, sheet_name="Doctors")
            missing = REQUIRED_COLUMNS - set(df.columns)
            if missing:
                raise ValueError("Doctor directory is missing columns: " + ", ".join(sorted(missing)))
            df = df.copy()
            df["_specialty_key"] = df["Specialization"].map(lambda x: _key(canonical_specialty(x)))
            df["_city_key"] = df["City"].map(_key)
            for col in ("Age", "Experience_Years", "Latitude", "Longitude", "Consultation_Fee_INR", "Rating_Out_of_5"):
                df[col] = pd.to_numeric(df[col], errors="coerce")
            self.doctors = df
        except Exception as exc:
            self.load_error = str(exc)

    def search(self, specialty: str, city: str | None = None, limit: int = 10,
               sort_by: str = "recommended") -> dict:
        if self.load_error:
            return {"success": False, "error": "Doctor directory is unavailable.", "details": self.load_error, "doctors": []}
        if not specialty or not str(specialty).strip():
            return {"success": False, "error": "The 'specialty' parameter is required.", "doctors": []}

        requested = canonical_specialty(specialty)
        specialty_key = _key(requested)
        matches = self.doctors[self.doctors["_specialty_key"] == specialty_key].copy()
        if city:
            city_key = _key(city)
            if city_key not in {"mumbai", "thane"}:
                return {"success": False, "error": "City must be Mumbai or Thane.", "doctors": []}
            matches = matches[matches["_city_key"] == city_key]

        if sort_by == "rating":
            matches = matches.sort_values(["Rating_Out_of_5", "Experience_Years"], ascending=[False, False])
        elif sort_by == "fees":
            matches = matches.sort_values(["Consultation_Fee_INR", "Rating_Out_of_5"], ascending=[True, False])
        elif sort_by == "experience":
            matches = matches.sort_values(["Experience_Years", "Rating_Out_of_5"], ascending=[False, False])
        else:
            # Natural-looking mixed ranking; do not imply clinical quality.
            matches = matches.sort_values(["Rating_Out_of_5", "Experience_Years"], ascending=[False, False])

        try:
            limit = max(1, min(int(limit), 50))
        except (TypeError, ValueError):
            limit = 10

        result = []
        for _, row in matches.head(limit).iterrows():
            def number(col, digits=None):
                value = row[col]
                if pd.isna(value):
                    return None
                value = float(value)
                return round(value, digits) if digits is not None else (int(value) if value.is_integer() else value)
            result.append({
                "doctor_id": str(row["Doctor_ID"]),
                "name": str(row["Doctor_Name"]),
                "specialization": canonical_specialty(row["Specialization"]),
                "age": number("Age"),
                "experience_years": number("Experience_Years"),
                "description": str(row["Description"]),
                "address": str(row["Address"]),
                "city": str(row["City"]),
                "state": str(row["State"]),
                "latitude": number("Latitude", 6),
                "longitude": number("Longitude", 6),
                "consultation_fee_inr": number("Consultation_Fee_INR"),
                "rating_out_of_5": number("Rating_Out_of_5", 1),
                "data_status": str(row["Data_Status"]),
            })

        return {
            "success": True,
            "requested_specialty": requested,
            "city_filter": city.title() if city else None,
            "count": len(result),
            "doctors": result,
            "notice": "Demo directory only: doctor profiles and clinic details are synthetic, and coordinates are approximate. Verify real providers before patient use.",
        }


doctor_recommender = DoctorRecommender()
