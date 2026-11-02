"""Ranking, comparison and patient-appointment REST endpoints.

Route groups
------------
* Read-only ranking / comparison / doctor detail (public, same data the rest of
  the app already exposes).
* Doctor availability (public read; administrators can edit via ``admin_bp``).
* Patient account + appointment management. Every mutating request requires an
  authenticated patient session **and** a matching ``X-CSRF-Token`` header. The
  patient identity always comes from the signed session, never from the request
  body, so a client cannot act on another patient's data.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from appointments import (
    cancel_appointment,
    create_appointment,
    get_day_availability,
    get_patient_appointment,
    list_open_dates,
    list_patient_appointments,
    reschedule_appointment,
)
from doctor_recommender_sqlite import doctor_recommender
from patient_auth import (
    create_patient,
    current_patient_csrf_token,
    current_patient_id,
    current_patient_username,
    is_patient_authenticated,
    login_patient,
    logout_patient,
    patient_required,
    verify_patient_credentials,
)
from ranking import compare_doctors, rank_doctors

care_bp = Blueprint("care", __name__)


# ------------------------------------------------------------------- ranking
@care_bp.get("/api/ranked-doctors")
def ranked_doctors():
    """Eligible doctors for a specialty, ordered by the configurable score."""
    result = rank_doctors(
        request.args.get("specialty", ""),
        city=request.args.get("city"),
        user_latitude=request.args.get("user_latitude"),
        user_longitude=request.args.get("user_longitude"),
        max_distance_km=request.args.get("max_distance_km"),
        max_fee=request.args.get("max_fee"),
        min_rating=request.args.get("min_rating"),
        limit=request.args.get("limit"),
    )
    if not result.get("success"):
        status = 503 if result.get("details") else 400
        return jsonify(result), status
    return jsonify(result), 200


@care_bp.post("/api/compare-doctors")
def compare_doctors_endpoint():
    """Return 1-3 doctor records with the same scores used by the ranking page."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"success": False, "error": "Send a JSON request body."}), 400
    payload, status = compare_doctors(
        body.get("doctor_ids"),
        requested_specialty=body.get("specialty"),
        user_latitude=body.get("user_latitude"),
        user_longitude=body.get("user_longitude"),
    )
    return jsonify(payload), status


@care_bp.get("/api/doctors/<path:doctor_id>")
def doctor_detail(doctor_id: str):
    """A single directory record, scored when scores are available."""
    doctor = doctor_recommender.get_doctor(doctor_id)
    if doctor is None:
        return jsonify({"success": False, "error": "Doctor not found."}), 404

    payload, _status = compare_doctors(
        [doctor_id],
        requested_specialty=request.args.get("specialty"),
        user_latitude=request.args.get("user_latitude"),
        user_longitude=request.args.get("user_longitude"),
    )
    if not payload.get("success"):
        return jsonify({"success": True, "scored": False, "doctor": doctor}), 200
    return jsonify({
        "success": True,
        "scored": True,
        "doctor": payload["doctors"][0],
        "score_disclaimer": payload.get("score_disclaimer"),
        "notice": payload.get("notice"),
    }), 200


# -------------------------------------------------------------- availability
@care_bp.get("/api/doctors/<path:doctor_id>/slots")
def doctor_slots(doctor_id: str):
    """Bookable slots for one doctor on one date, from stored availability."""
    payload, status = get_day_availability(doctor_id, request.args.get("date"))
    return jsonify(payload), status


@care_bp.get("/api/doctors/<path:doctor_id>/open-dates")
def doctor_open_dates(doctor_id: str):
    """Per-date availability summary used by the comparison view."""
    payload, status = list_open_dates(
        doctor_id,
        start=request.args.get("from"),
        days=request.args.get("days", 14),
    )
    return jsonify(payload), status


# ------------------------------------------------------------ patient account
@care_bp.post("/api/patient/register")
def patient_register():
    payload, status = create_patient(request.get_json(silent=True))
    return jsonify(payload), status


@care_bp.post("/api/patient/login")
def patient_login():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"success": False, "error": "Send a JSON request body."}), 400
    patient = verify_patient_credentials(body.get("username"), body.get("password"))
    if patient is None:
        return jsonify({"success": False, "error": "Invalid username or password."}), 401
    token = login_patient(patient)
    return jsonify({
        "success": True,
        "authenticated": True,
        "patient": patient,
        "csrf_token": token,
    }), 200


@care_bp.post("/api/patient/logout")
@patient_required
def patient_logout():
    logout_patient()
    return jsonify({"success": True, "authenticated": False}), 200


@care_bp.get("/api/patient/me")
def patient_me():
    """Report the current patient session so the SPA can restore state."""
    if not is_patient_authenticated() or not current_patient_id():
        return jsonify({
            "success": True,
            "authenticated": False,
            "patient": None,
            "csrf_token": None,
        }), 200
    return jsonify({
        "success": True,
        "authenticated": True,
        "patient": {
            "patient_id": current_patient_id(),
            "username": current_patient_username(),
        },
        "csrf_token": current_patient_csrf_token(),
    }), 200


# ------------------------------------------------------------------ bookings
@care_bp.get("/api/patient/appointments")
@patient_required
def patient_appointments():
    payload = list_patient_appointments(current_patient_id())
    return jsonify(payload), (200 if payload.get("success") else 500)


@care_bp.post("/api/patient/appointments")
@patient_required
def patient_create_appointment():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"success": False, "error": "Send a JSON request body."}), 400
    payload, status = create_appointment(
        current_patient_id(),
        body.get("doctor_id"),
        body.get("date"),
        body.get("start_time"),
        body.get("consultation_type"),
    )
    return jsonify(payload), status


@care_bp.get("/api/patient/appointments/<path:appointment_id>")
@patient_required
def patient_get_appointment(appointment_id: str):
    payload, status = get_patient_appointment(current_patient_id(), appointment_id)
    return jsonify(payload), status


@care_bp.post("/api/patient/appointments/<path:appointment_id>/cancel")
@patient_required
def patient_cancel_appointment(appointment_id: str):
    payload, status = cancel_appointment(current_patient_id(), appointment_id)
    return jsonify(payload), status


@care_bp.post("/api/patient/appointments/<path:appointment_id>/reschedule")
@patient_required
def patient_reschedule_appointment(appointment_id: str):
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"success": False, "error": "Send a JSON request body."}), 400
    payload, status = reschedule_appointment(
        current_patient_id(),
        appointment_id,
        body.get("date"),
        body.get("start_time"),
    )
    return jsonify(payload), status


__all__ = ["care_bp"]
