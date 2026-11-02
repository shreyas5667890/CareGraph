"""Configurable, deterministic doctor-ranking and comparison engine for CareGraph.

The recommendation score is an *accessibility* score derived from directory data
(specialty match, distance, patient rating, affordability and experience). It is
**not** a clinical quality, safety or medical-suitability score, and it never
overrides the existing emergency-symptom safeguards.

Design notes
------------
* Weights live in a single place (:data:`RANKING_WEIGHTS` / :class:`RankingWeights`).
* Every component is normalised onto a 0-100 scale before combining.
* Missing data never crashes scoring and is never treated as zero. Components
  that cannot be computed are dropped and the remaining weights are
  renormalised so the final score stays on a 0-100 scale.
* Ranking is deterministic: score descending, then ``doctor_id`` ascending as a
  stable tie-breaker.
* Eligibility filters (fee / rating / distance) are applied by the existing
  SQLite recommender *before* scores are calculated.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from doctor_recommender_sqlite import (
    DoctorRecommender,
    _display_number,
    canonical_specialty,
    haversine_km,
    validate_coordinates,
)
from doctor_recommender_sqlite import doctor_recommender as default_recommender


@dataclass(frozen=True)
class RankingWeights:
    """The single place ranking weights are configured.

    The defaults match the agreed specification and total 100%:
    specialty 35% + distance 25% + rating 15% + affordability 15% + experience 10%.
    """

    specialty: float = 0.35
    distance: float = 0.25
    rating: float = 0.15
    affordability: float = 0.15
    experience: float = 0.10

    def as_dict(self) -> dict[str, float]:
        return {
            "specialty": self.specialty,
            "distance": self.distance,
            "rating": self.rating,
            "affordability": self.affordability,
            "experience": self.experience,
        }

    def total(self) -> float:
        return round(sum(self.as_dict().values()), 6)


# The active configuration. Change weights here and every caller picks them up.
RANKING_WEIGHTS = RankingWeights()

# Reference scales used to normalise each component onto 0-100.
DISTANCE_REFERENCE_KM = 25.0
FEE_REFERENCE_INR = 2000.0
EXPERIENCE_REFERENCE_YEARS = 30.0

MAX_COMPARE_DOCTORS = 3

SCORE_DISCLAIMER = (
    "The recommendation score ranks directory records by accessibility and price "
    "using the data that is available. It is not a clinical quality, safety or "
    "medical-suitability score."
)

RANKING_NOTICE = (
    "Doctor profiles are synthetic demo records with approximate coordinates. "
    "When a value is missing its component is omitted and the remaining weights "
    "are renormalised."
)


def _number(value: Any) -> float | None:
    """Coerce to a finite float, or ``None`` for missing/invalid input."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def specialty_score(doctor_specialty: Any, requested_specialty: Any) -> float | None:
    """100 for an exact (canonicalised) specialty match, 0 for a mismatch.

    When no target specialty is supplied the doctor is scored against its own
    specialty (100), which keeps comparison usable for ad-hoc selections. A
    doctor with no recorded specialty and no target is genuinely unknown, so the
    component is reported as missing instead of being scored.
    """
    if not requested_specialty or not str(requested_specialty).strip():
        if doctor_specialty is None or not str(doctor_specialty).strip():
            return None
        return 100.0
    if canonical_specialty(doctor_specialty) == canonical_specialty(requested_specialty):
        return 100.0
    return 0.0


def distance_score(distance_km: Any) -> float | None:
    """100 at 0 km, falling linearly to 0 at :data:`DISTANCE_REFERENCE_KM`."""
    distance = _number(distance_km)
    if distance is None or distance < 0:
        return None
    return round(_clamp(100.0 * (1.0 - min(distance, DISTANCE_REFERENCE_KM) / DISTANCE_REFERENCE_KM)), 4)


def rating_score(rating_out_of_5: Any) -> float | None:
    rating = _number(rating_out_of_5)
    if rating is None or rating < 0:
        return None
    return round(_clamp(rating / 5.0 * 100.0), 4)


def affordability_score(consultation_fee_inr: Any) -> float | None:
    """Lower fees score higher: 100 at zero fee, 0 at/above the fee reference."""
    fee = _number(consultation_fee_inr)
    if fee is None or fee < 0:
        return None
    return round(
        _clamp(100.0 * (1.0 - min(fee, FEE_REFERENCE_INR) / FEE_REFERENCE_INR)), 4
    )


def experience_score(experience_years: Any) -> float | None:
    years = _number(experience_years)
    if years is None or years < 0:
        return None
    return round(
        _clamp(min(years, EXPERIENCE_REFERENCE_YEARS) / EXPERIENCE_REFERENCE_YEARS * 100.0), 4
    )


def score_doctor(
    doctor: dict,
    *,
    requested_specialty: Any = None,
    weights: RankingWeights = RANKING_WEIGHTS,
) -> dict:
    """Score one doctor record, renormalising the weights for available data."""
    components: dict[str, float | None] = {
        "specialty": specialty_score(doctor.get("specialization"), requested_specialty),
        "distance": distance_score(doctor.get("distance_km")),
        "rating": rating_score(doctor.get("rating_out_of_5")),
        "affordability": affordability_score(doctor.get("consultation_fee_inr")),
        "experience": experience_score(doctor.get("experience_years")),
    }
    weight_map = weights.as_dict()
    available = {key: value for key, value in components.items() if value is not None}
    total_weight = sum(weight_map[key] for key in available)

    if total_weight <= 0:
        # Deterministic fallback: nothing could be scored.
        score = 0.0
        applied: dict[str, float] = {}
    else:
        score = sum(components[key] * weight_map[key] for key in available) / total_weight
        applied = {key: round(weight_map[key] / total_weight, 6) for key in available}

    return {
        "recommendation_score": round(score, 2),
        "score_breakdown": components,
        "applied_weights": applied,
        "components_used": sorted(available),
        "components_missing": sorted(key for key in components if key not in available),
    }


def _rank_sort_key(doctor: dict) -> tuple:
    return (-doctor["recommendation_score"], str(doctor.get("doctor_id") or ""))


def rank_doctors(
    specialty: Any,
    *,
    recommender: DoctorRecommender | None = None,
    city: Any = None,
    user_latitude: Any = None,
    user_longitude: Any = None,
    max_distance_km: Any = None,
    max_fee: Any = None,
    min_rating: Any = None,
    limit: Any = None,
    weights: RankingWeights = RANKING_WEIGHTS,
) -> dict:
    """Retrieve eligible doctors for a specialty and return them ranked."""
    rec = recommender or default_recommender
    if specialty is None or not str(specialty).strip():
        return {
            "success": False,
            "error": "The 'specialty' parameter is required.",
            "doctors": [],
        }

    search = rec.search(
        specialty=specialty,
        city=city,
        user_latitude=user_latitude,
        user_longitude=user_longitude,
        max_distance_km=max_distance_km,
        max_fee=max_fee,
        min_rating=min_rating,
        sort_by="recommended",
        page=1,
        page_size=100,
    )
    if not search.get("success"):
        return {
            "success": False,
            "error": search.get("error", "Doctor search failed."),
            "details": search.get("details"),
            "doctors": [],
        }

    target = search.get("specialty") or canonical_specialty(specialty)
    doctors: list[dict] = []
    for record in search.get("doctors", []):
        scored = score_doctor(record, requested_specialty=target, weights=weights)
        doctors.append({**record, **scored})
    doctors.sort(key=_rank_sort_key)
    total = len(doctors)

    if limit is not None:
        try:
            limit_value: int | None = max(1, min(int(limit), 100))
        except (TypeError, ValueError):
            limit_value = None
        if limit_value is not None:
            doctors = doctors[:limit_value]

    filters = search.get("filters", {}) or {}
    return {
        "success": True,
        "specialty": target,
        "city_filter": search.get("city_filter"),
        "filters": filters,
        "weights": weights.as_dict(),
        "weights_total": weights.total(),
        "distance_available": filters.get("user_latitude") is not None,
        "count": len(doctors),
        "total": total,
        "doctors": doctors,
        "score_disclaimer": SCORE_DISCLAIMER,
        "notice": RANKING_NOTICE,
        "message": search.get("message"),
    }


def compare_doctors(
    doctor_ids: Any,
    *,
    recommender: DoctorRecommender | None = None,
    requested_specialty: Any = None,
    user_latitude: Any = None,
    user_longitude: Any = None,
    weights: RankingWeights = RANKING_WEIGHTS,
) -> tuple[dict, int]:
    """Validate and return the same records/scores used by the ranking engine.

    Enforces the three-doctor limit and rejects duplicates so the frontend rule
    is also backed by the API. Returns ``(payload, http_status)``.
    """
    rec = recommender or default_recommender

    if not isinstance(doctor_ids, list) or not doctor_ids:
        return {"success": False, "error": "Provide a list of 1 to 3 doctor IDs."}, 400

    cleaned: list[str] = []
    for raw in doctor_ids:
        if not isinstance(raw, str) or not raw.strip():
            return {"success": False, "error": "Doctor IDs must be non-empty strings."}, 400
        cleaned.append(raw.strip())

    if len(cleaned) > MAX_COMPARE_DOCTORS:
        return (
            {"success": False, "error": f"Compare at most {MAX_COMPARE_DOCTORS} doctors at a time."},
            400,
        )

    lowered = [value.lower() for value in cleaned]
    if len(set(lowered)) != len(lowered):
        return {"success": False, "error": "Duplicate doctor selections are not allowed."}, 400

    lat, lon, coord_error = validate_coordinates(user_latitude, user_longitude)
    if coord_error:
        return {"success": False, "error": coord_error}, 400

    doctors: list[dict] = []
    missing: list[str] = []
    for doctor_id in cleaned:
        record = rec.get_doctor(doctor_id)
        if record is None:
            missing.append(doctor_id)
            continue
        if lat is not None and lon is not None:
            record["distance_km"] = _display_number(
                haversine_km(lat, lon, record.get("latitude"), record.get("longitude")), 2
            )
        else:
            record["distance_km"] = None
        target = requested_specialty or record.get("specialization")
        doctors.append({**record, **score_doctor(record, requested_specialty=target, weights=weights)})

    if missing:
        return (
            {
                "success": False,
                "error": "One or more doctors were not found.",
                "missing_doctor_ids": missing,
            },
            404,
        )

    return (
        {
            "success": True,
            "max_doctors": MAX_COMPARE_DOCTORS,
            "requested_specialty": requested_specialty,
            "distance_available": lat is not None and lon is not None,
            "count": len(doctors),
            "doctors": doctors,
            "weights": weights.as_dict(),
            "score_disclaimer": SCORE_DISCLAIMER,
            "notice": RANKING_NOTICE,
        },
        200,
    )
