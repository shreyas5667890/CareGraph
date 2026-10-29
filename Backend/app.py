"""
CareGraph Flask API
Place this folder at CareGraph/Backend.
Expected project structure:
CareGraph/
  Backend/app.py
  Recommendation/caregraph_recommendation_artifacts/
    disease_classifier.joblib
    model_metadata.json
    disease_specialty_mapping_needs_review.csv
  SymptomExtraction/
    symptom_pipeline.py
    symptom_extractor.py
    symptom_context.py
    symptom_normalizer.py
"""
from __future__ import annotations

import json
import os
import re
import secrets
import sys
from pathlib import Path
from typing import Any

import joblib
import pandas as pd
from flask import Flask, jsonify, request

from admin_routes import admin_bp
from doctor_recommender_sqlite import doctor_recommender

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = (
    PROJECT_ROOT / "Recommendation" / "caregraph_recommendation_artifacts"
)
SYMPTOM_DIR = PROJECT_ROOT / "SymptomExtraction"

# Import the existing symptom pipeline without moving its files.
if str(SYMPTOM_DIR) not in sys.path:
    sys.path.insert(0, str(SYMPTOM_DIR))

app = Flask(__name__)

# --- session / cookie security -------------------------------------------------
# CAREGRAPH_SECRET_KEY must be set for any real deployment. A random per-process
# key is generated for local development so no secret is ever committed.
_secret_key = os.getenv("CAREGRAPH_SECRET_KEY", "").strip()
if not _secret_key:
    _secret_key = secrets.token_hex(32)
    print(
        "WARNING: CAREGRAPH_SECRET_KEY is not set; using a temporary key. "
        "Admin sessions will not survive a restart."
    )
app.config.update(
    SECRET_KEY=_secret_key,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("CAREGRAPH_COOKIE_SECURE", "false").strip().lower()
    in {"1", "true", "yes"},
)

app.register_blueprint(admin_bp)

# Optional cross-origin access. The Vite dev server proxies /api, so this is only
# needed when the frontend is hosted on a different origin. Restricted to one origin.
FRONTEND_ORIGIN = os.getenv("CAREGRAPH_FRONTEND_ORIGIN", "").strip()


@app.after_request
def apply_cors(response):
    if FRONTEND_ORIGIN:
        origin = request.headers.get("Origin")
        if origin and origin == FRONTEND_ORIGIN:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-CSRF-Token"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
            response.headers.add("Vary", "Origin")
    return response


@app.errorhandler(500)
def handle_internal_error(_error):
    """Never leak stack traces or database details to clients."""
    return jsonify({"success": False, "error": "Internal server error."}), 500


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value).replace("\xa0", " ").strip().lower())


def feature_key(value: Any) -> str:
    """Comparable key for symptom names; model feature names remain unchanged."""
    value = clean_text(value)
    value = re.sub(r"\s+", "_", value)
    return value


def load_artifacts():
    model_path = ARTIFACT_DIR / "disease_classifier.joblib"
    metadata_path = ARTIFACT_DIR / "model_metadata.json"
    mapping_path = ARTIFACT_DIR / "disease_specialty_mapping_needs_review.csv"

    missing = [str(p) for p in (model_path, metadata_path, mapping_path) if not p.exists()]
    if missing:
        raise FileNotFoundError(
            "Missing recommendation artifacts: " + ", ".join(missing)
        )

    model = joblib.load(model_path)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    mapping_df = pd.read_csv(mapping_path)

    required_metadata = ("feature_names", "disease_classes")
    for key in required_metadata:
        if key not in metadata or not metadata[key]:
            raise ValueError(f"model_metadata.json is missing '{key}'.")

    if not {"disease", "specialty"}.issubset(mapping_df.columns):
        raise ValueError(
            "Mapping CSV must have 'disease' and 'specialty' columns. "
            "Re-export it from the recommendation notebook."
        )

    # Exclude ambiguous disease mappings rather than selecting one arbitrarily.
    mapping_df["disease_key"] = mapping_df["disease"].map(clean_text)
    mapping_df["specialty_value"] = mapping_df["specialty"].map(
        lambda x: re.sub(r"\s+", " ", clean_text(x))
    )
    mapping_df = mapping_df[
        (mapping_df["disease_key"] != "") &
        (mapping_df["specialty_value"] != "")
    ].copy()

    ambiguous = (
        mapping_df.groupby("disease_key")["specialty_value"].nunique()
    )
    ambiguous_diseases = set(ambiguous[ambiguous > 1].index)
    mapping_df = mapping_df[
        ~mapping_df["disease_key"].isin(ambiguous_diseases)
    ].drop_duplicates("disease_key")

    specialty_mapping = dict(
        zip(mapping_df["disease_key"], mapping_df["specialty_value"])
    )

    feature_names = list(metadata["feature_names"])
    feature_lookup = {}
    for feature in feature_names:
        feature_lookup.setdefault(feature_key(feature), feature)

    model_classes = [str(c) for c in getattr(model, "classes_", [])]
    if model_classes and len(model_classes) != len(metadata["disease_classes"]):
        raise ValueError(
            "Model class count does not match metadata. Verify that the model "
            "and metadata were saved from the same training run."
        )

    return model, metadata, feature_names, feature_lookup, specialty_mapping, sorted(ambiguous_diseases)


def attach_doctor_results(recommendation: dict, city: str | None = None, doctors_per_specialty: int = 5) -> dict:
    """Attach directory matches for specialties predicted by the existing model."""
    if recommendation.get("status") != "candidate_only":
        recommendation["doctor_recommendations"] = []
        return recommendation

    grouped = []
    seen_specialties = set()
    for candidate in recommendation.get("ranked_candidates", []):
        specialty = candidate.get("possible_specialty")
        if not specialty:
            continue
        key = clean_text(specialty)
        if key in seen_specialties:
            continue
        seen_specialties.add(key)
        directory_result = doctor_recommender.search(
            specialty=specialty, city=city, limit=doctors_per_specialty
        )
        grouped.append({
            "disease_candidate": candidate.get("disease_candidate"),
            "model_score": candidate.get("model_score"),
            "specialty": specialty,
            "directory_success": directory_result.get("success", False),
            "doctor_count": directory_result.get("count", 0),
            "doctors": directory_result.get("doctors", []),
            "directory_error": directory_result.get("error"),
        })
    recommendation["doctor_recommendations"] = grouped
    recommendation["doctor_directory_notice"] = (
        "Doctor directory entries are synthetic demo records with approximate coordinates, "
        "not verified real providers. Model scores are not diagnoses or validated referral probabilities."
    )
    return recommendation


try:
    (
        disease_model,
        metadata,
        FEATURE_NAMES,
        FEATURE_LOOKUP,
        SPECIALTY_MAPPING,
        AMBIGUOUS_MAPPINGS,
    ) = load_artifacts()
    ARTIFACT_LOAD_ERROR = None
except Exception as exc:
    disease_model = metadata = None
    FEATURE_NAMES = []
    FEATURE_LOOKUP = {}
    SPECIALTY_MAPPING = {}
    AMBIGUOUS_MAPPINGS = []
    ARTIFACT_LOAD_ERROR = str(exc)


def build_feature_vector(confirmed_symptoms: list[str]) -> tuple[pd.DataFrame, list[str]]:
    """Create a one-row DataFrame with the exact saved training feature order."""
    normalized_keys = {feature_key(s) for s in confirmed_symptoms if clean_text(s)}
    matched_features = {
        FEATURE_LOOKUP[key] for key in normalized_keys if key in FEATURE_LOOKUP
    }
    unknown = sorted(
        s for s in confirmed_symptoms
        if feature_key(s) not in FEATURE_LOOKUP
    )
    vector = pd.DataFrame(
        [[int(feature in matched_features) for feature in FEATURE_NAMES]],
        columns=FEATURE_NAMES,
    )
    return vector, unknown


def predict_recommendations(confirmed_symptoms: list[str], top_k: int = 3) -> dict:
    if not confirmed_symptoms:
        return {
            "status": "needs_more_information",
            "message": "No confirmed, normalized current symptoms were identified.",
            "confirmed_symptoms": [],
            "ranked_candidates": [],
        }

    feature_vector, unknown = build_feature_vector(confirmed_symptoms)
    if int(feature_vector.to_numpy().sum()) == 0:
        return {
            "status": "needs_more_information",
            "message": "None of the confirmed symptoms matched the model's training vocabulary.",
            "confirmed_symptoms": confirmed_symptoms,
            "unknown_symptoms_ignored_by_model": unknown,
            "ranked_candidates": [],
        }

    # RandomForestClassifier supports predict_proba. Keep a safe fallback for
    # a future model that may only implement predict().
    if hasattr(disease_model, "predict_proba"):
        scores = disease_model.predict_proba(feature_vector)[0]
        classes = [str(c) for c in disease_model.classes_]
        ranked_indices = sorted(range(len(scores)), key=lambda i: float(scores[i]), reverse=True)
        candidates = []
        for i in ranked_indices[:max(1, min(int(top_k), len(ranked_indices)))]:
            disease = classes[i]
            candidates.append({
                "disease_candidate": disease,
                "model_score": round(float(scores[i]), 4),
                "possible_specialty": SPECIALTY_MAPPING.get(clean_text(disease)),
            })
    else:
        disease = str(disease_model.predict(feature_vector)[0])
        candidates = [{
            "disease_candidate": disease,
            "model_score": None,
            "possible_specialty": SPECIALTY_MAPPING.get(clean_text(disease)),
        }]

    return {
        "status": "candidate_only",
        "confirmed_symptoms": confirmed_symptoms,
        "unknown_symptoms_ignored_by_model": unknown,
        "ranked_candidates": candidates,
        "mapping_needs_medical_review": True,
        "ambiguous_disease_mappings_excluded": AMBIGUOUS_MAPPINGS,
        "safety_note": (
            "Educational prototype only; not a diagnosis or a clinically validated referral. "
            "The supplied disease-to-specialty mapping requires medical review. "
            "Seek urgent medical care for severe or emergency symptoms."
        ),
    }


@app.get("/api/health")
def health():
    return jsonify({
        "success": ARTIFACT_LOAD_ERROR is None,
        "service": "CareGraph recommendation API",
        "model_loaded": disease_model is not None,
        "artifact_error": ARTIFACT_LOAD_ERROR,
    }), (200 if ARTIFACT_LOAD_ERROR is None else 503)


@app.get("/api/doctors")
def list_doctors():
    """Find directory records by specialty, with optional distance/fee/rating filters.

    All filters and sorting are applied by the SQLite-backed recommender so the
    returned result set is always consistent with the requested parameters.
    """
    result = doctor_recommender.search(
        specialty=request.args.get("specialty", ""),
        city=request.args.get("city"),
        sort_by=request.args.get("sort", "recommended"),
        limit=request.args.get("limit", "10"),
        user_latitude=request.args.get("user_latitude"),
        user_longitude=request.args.get("user_longitude"),
        max_distance_km=request.args.get("max_distance_km"),
        max_fee=request.args.get("max_fee"),
        min_rating=request.args.get("min_rating"),
        page=request.args.get("page"),
        page_size=request.args.get("page_size"),
    )
    if not result.get("success"):
        status = 503 if "details" in result else 400
        return jsonify(result), status
    return jsonify(result)


@app.get("/api/specialties")
def list_specialties():
    """The specialties actually present in the doctor directory."""
    return jsonify({
        "success": True,
        "specialties": doctor_recommender.list_specialties(),
        "cities": doctor_recommender.list_cities(),
    })


@app.post("/api/analyze")
def analyze():
    if ARTIFACT_LOAD_ERROR:
        return jsonify({"success": False, "error": "Model artifacts could not be loaded.", "details": ARTIFACT_LOAD_ERROR}), 503

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"success": False, "error": "Send a JSON request body."}), 400

    text_input = body.get("text")
    if not isinstance(text_input, str) or not text_input.strip():
        return jsonify({"success": False, "error": "The 'text' field must be a non-empty string."}), 400
    if len(text_input) > 5000:
        return jsonify({"success": False, "error": "Text is too long; maximum length is 5000 characters."}), 400

    try:
        from symptom_pipeline import analyze_patient_text
        symptom_analysis = analyze_patient_text(text_input)
        confirmed = sorted({
            str(item["normalized_symptom"])
            for item in symptom_analysis.get("confirmed_symptoms", [])
            if item.get("normalized_symptom")
        })
        top_k = body.get("top_k", 3)
        try:
            top_k = int(top_k)
        except (TypeError, ValueError):
            top_k = 3
        top_k = max(1, min(top_k, 5))

        recommendation = predict_recommendations(confirmed, top_k=top_k)
        city = body.get("city")
        if city is not None and not isinstance(city, str):
            city = None
        per_specialty = body.get("doctors_per_specialty", 5)
        try:
            per_specialty = max(1, min(int(per_specialty), 10))
        except (TypeError, ValueError):
            per_specialty = 5
        recommendation = attach_doctor_results(recommendation, city=city, doctors_per_specialty=per_specialty)
        return jsonify({
            "success": True,
            "symptom_analysis": symptom_analysis,
            "recommendation": recommendation,
        })
    except Exception as exc:
        app.logger.exception("Symptom analysis/recommendation failed")
        return jsonify({
            "success": False,
            "error": "Could not analyze this input.",
            "details": str(exc),
        }), 500


@app.post("/api/recommend")
def recommend_from_symptoms():
    """Optional endpoint for clients that already have normalized symptom labels."""
    if ARTIFACT_LOAD_ERROR:
        return jsonify({"success": False, "error": "Model artifacts could not be loaded.", "details": ARTIFACT_LOAD_ERROR}), 503

    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("symptoms"), list):
        return jsonify({"success": False, "error": "Provide JSON with a 'symptoms' list."}), 400

    symptoms = [str(s) for s in body["symptoms"] if isinstance(s, str) and s.strip()]
    top_k = body.get("top_k", 3)
    try:
        top_k = max(1, min(int(top_k), 5))
    except (TypeError, ValueError):
        top_k = 3

    try:
        result = predict_recommendations(symptoms, top_k=top_k)
        city = body.get("city")
        if city is not None and not isinstance(city, str):
            city = None
        per_specialty = body.get("doctors_per_specialty", 5)
        try:
            per_specialty = max(1, min(int(per_specialty), 10))
        except (TypeError, ValueError):
            per_specialty = 5
        result = attach_doctor_results(result, city=city, doctors_per_specialty=per_specialty)
        return jsonify({"success": True, "recommendation": result})
    except Exception as exc:
        app.logger.exception("Recommendation failed")
        return jsonify({"success": False, "error": "Could not generate recommendations.", "details": str(exc)}), 500


if __name__ == "__main__":
    print("CareGraph project root:", PROJECT_ROOT)
    print("Artifacts directory:", ARTIFACT_DIR)
    print("Health check: http://127.0.0.1:5000/api/health")
    print("Doctor directory: http://127.0.0.1:5000/api/doctors?specialty=Cardiologist")
    print("Specialties: http://127.0.0.1:5000/api/specialties")
    debug = os.getenv("CAREGRAPH_DEBUG", "true").strip().lower() in {"1", "true", "yes"}
    app.run(host="127.0.0.1", port=int(os.getenv("CAREGRAPH_BACKEND_PORT", "5000")), debug=debug)
