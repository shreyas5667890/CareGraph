"""Protected admin REST endpoints for doctor management.

All routes are mounted under ``/api/admin``. Doctor CRUD routes require an
authenticated session and a valid CSRF token.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from admin_auth import (
    admin_configured,
    admin_required,
    admin_username,
    current_csrf_token,
    is_authenticated,
    login_admin,
    logout_admin,
    verify_credentials,
)
from doctor_recommender_sqlite import doctor_recommender

admin_bp = Blueprint("admin", __name__, url_prefix="/api/admin")


@admin_bp.post("/login")
def admin_login():
    if not admin_configured():
        return jsonify({
            "success": False,
            "error": (
                "The admin account is not configured. Set CAREGRAPH_ADMIN_USERNAME and "
                "CAREGRAPH_ADMIN_PASSWORD_HASH (or CAREGRAPH_ADMIN_PASSWORD for local development)."
            ),
        }), 503

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"success": False, "error": "Send a JSON request body."}), 400

    username = body.get("username")
    password = body.get("password")
    if not isinstance(username, str) or not username.strip():
        return jsonify({"success": False, "error": "Username is required."}), 400
    if not isinstance(password, str) or not password:
        return jsonify({"success": False, "error": "Password is required."}), 400

    if not verify_credentials(username, password):
        return jsonify({"success": False, "error": "Invalid username or password."}), 401

    token = login_admin(username)
    return jsonify({
        "success": True,
        "authenticated": True,
        "username": username.strip(),
        "csrf_token": token,
    }), 200


@admin_bp.post("/logout")
@admin_required
def admin_logout():
    logout_admin()
    return jsonify({"success": True, "authenticated": False}), 200


@admin_bp.get("/me")
def admin_me():
    """Report the current session so the SPA can restore state after a reload."""
    if not is_authenticated():
        return jsonify({
            "success": True,
            "authenticated": False,
            "admin_configured": admin_configured(),
            "username": None,
            "csrf_token": None,
        }), 200
    return jsonify({
        "success": True,
        "authenticated": True,
        "admin_configured": True,
        "username": admin_username(),
        "csrf_token": current_csrf_token(),
    }), 200


@admin_bp.get("/doctors")
@admin_required
def admin_list_doctors():
    result = doctor_recommender.list_doctors_admin(
        q=request.args.get("q"),
        specialization=request.args.get("specialization"),
        city=request.args.get("city"),
        page=request.args.get("page", 1),
        page_size=request.args.get("page_size", 20),
    )
    if not result.get("success"):
        status = 503 if "details" in result else 400
        return jsonify(result), status
    result["specialties"] = doctor_recommender.list_specialties()
    result["cities"] = doctor_recommender.list_cities()
    return jsonify(result), 200


@admin_bp.post("/doctors")
@admin_required
def admin_create_doctor():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"success": False, "error": "Send a JSON request body."}), 400
    result, status = doctor_recommender.create_doctor(body)
    return jsonify(result), status


@admin_bp.get("/doctors/<path:doctor_id>")
@admin_required
def admin_get_doctor(doctor_id: str):
    doctor = doctor_recommender.get_doctor(doctor_id)
    if doctor is None:
        return jsonify({"success": False, "error": "Doctor not found."}), 404
    return jsonify({"success": True, "doctor": doctor}), 200


@admin_bp.put("/doctors/<path:doctor_id>")
@admin_required
def admin_update_doctor(doctor_id: str):
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"success": False, "error": "Send a JSON request body."}), 400
    result, status = doctor_recommender.update_doctor(doctor_id, body)
    return jsonify(result), status


@admin_bp.delete("/doctors/<path:doctor_id>")
@admin_required
def admin_delete_doctor(doctor_id: str):
    result, status = doctor_recommender.delete_doctor(doctor_id)
    return jsonify(result), status
