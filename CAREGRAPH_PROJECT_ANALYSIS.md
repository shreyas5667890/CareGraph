# CareGraph — Complete Project Analysis

> **Document type:** Full technical analysis (architecture, code, models, database, API, frontend)
> **Scope:** The entire CareGraph repository as it exists on disk
> **Method:** Static read-only inspection (file reads, greps, and one read-only SQLite schema query)
> **Status:** Analysis only — no file, database record, model artifact, configuration, or dependency was modified

---

## Table of Contents

1. [Project Overview and Purpose](#1-project-overview-and-purpose)
2. [Directory Structure](#2-directory-structure)
3. [Architecture Diagram](#3-architecture-diagram)
4. [Technology Stack](#4-technology-stack)
5. [File-by-File Explanation](#5-file-by-file-explanation)
6. [Function and Component Inventory](#6-function-and-component-inventory)
7. [Symptom Extraction Pipeline](#7-symptom-extraction-pipeline)
8. [Disease Classification and Specialty Mapping](#8-disease-classification-and-specialty-mapping)
9. [Model Performance and Evaluation Metrics](#9-model-performance-and-evaluation-metrics)
10. [SQLite Schema and Database Operations](#10-sqlite-schema-and-database-operations)
11. [Doctor Recommendation Algorithm](#11-doctor-recommendation-algorithm)
12. [Frontend and Admin Dashboard](#12-frontend-and-admin-dashboard)
13. [API Endpoint Documentation](#13-api-endpoint-documentation)
14. [Complete End-to-End Data Flows](#14-complete-end-to-end-data-flows)
15. [Configuration, Dependencies, and Startup](#15-configuration-dependencies-and-startup)
16. [Existing Tests and Verification Status](#16-existing-tests-and-verification-status)
17. [Confirmed Limitations and Integration Gaps](#17-confirmed-limitations-and-integration-gaps)
18. [Recommended Future Evaluations and Improvements](#18-recommended-future-evaluations-and-improvements)
19. [Read-Only Inspection Confirmation](#19-read-only-inspection-confirmation)

---

## 1. Project Overview and Purpose

CareGraph is an **educational prototype** that converts free-text patient symptom descriptions into a
**suggested medical specialty**, and then recommends **doctors** from a local SQLite directory.

The system chains two largely independent problems:

1. **Symptom understanding** (`SymptomExtraction/`) — a pretrained clinical NER model (MultiClinNER)
   combined with rule-based context detection and a vocabulary-based normalizer.
2. **Disease → specialty → doctor** (`Recommendation/` + `Backend/`) — a trained
   `RandomForestClassifier` over 41 disease classes, exposed via Flask, joined to a 150-row
   synthetic doctor directory and served to a React/Vite single-page app that includes a
   password-protected admin dashboard.

### Safety framing (present in code and documentation)

- Model output is **not a diagnosis**; model scores are **not calibrated probabilities**.
- Doctor records are **synthetic demo data** with approximate coordinates.
- The disease→specialty mapping is shipped as `disease_specialty_mapping_needs_review.csv`
  and explicitly flagged for medical review.
- Severe or emergency symptoms are expected to bypass model-based recommendations.

---

## 2. Directory Structure

Verified from disk:

```text
CareGraph/
├── Backend/
│   ├── app.py                       # Flask API + artifact loading + prediction
│   ├── doctor_recommender_sqlite.py # SQLite access layer (search, filters, CRUD)
│   ├── doctor_recommender.py        # LEGACY Excel/Pandas recommender (not on the active path)
│   ├── admin_auth.py                # env-var credentials, session, CSRF
│   ├── admin_routes.py              # /api/admin/* blueprint
│   ├── import_doctors_to_sqlite.py  # rebuild DB from Excel (source file currently absent)
│   ├── requirements.txt
│   ├── __pycache__/                 # cpython-314 bytecode
│   └── tests/
│       ├── _support.py
│       ├── test_distance.py
│       ├── test_doctor_search.py
│       └── test_admin_crud.py
├── Data/
│   ├── caregraph_doctors.db         # SQLite: "doctors" table, 150 rows
│   ├── Disease_Description.csv
│   ├── Doctor_Specialist.csv
│   ├── Doctor_Versus_Disease.csv
│   ├── Original_Dataset.csv         # model training data
│   ├── Specialist.xlsx
│   └── Symptom_Weights.csv
├── Recommendation/
│   ├── CareGraph_Doctor_Recommendation.ipynb
│   ├── classifier_comparison_results.csv
│   └── caregraph_recommendation_artifacts/
│       ├── disease_classifier.joblib
│       ├── model_metadata.json
│       └── disease_specialty_mapping_needs_review.csv
├── SymptomExtraction/
│   ├── symptom_extractor.py         # MultiClinNER wrapper
│   ├── symptom_context.py           # rule-based context classifier
│   ├── symptom_normalizer.py        # vocabulary + synonym + fuzzy normalizer
│   ├── symptom_pipeline.py          # orchestrator (analyze_patient_text)
│   ├── symptom_vocabulary.csv       # 131 canonical symptoms
│   ├── Symptom_Weights.csv
│   ├── Disease_Description.csv
│   ├── inspect_symptoms.py
│   ├── test_symptoms.py             # smoke tests (writes symptom_test_results.json)
│   ├── test_symptoms_normalization.py
│   ├── symptom_test_results.json    # recorded run output
│   ├── requirements.txt
│   ├── README.md
│   └── .venv/                       # Python 3.14 virtualenv
├── frontend/
│   ├── index.html
│   ├── package.json / package-lock.json
│   ├── vite.config.js
│   └── src/
│       ├── main.jsx
│       ├── App.jsx
│       ├── styles.css
│       ├── api/client.js
│       ├── components/DoctorCard.jsx
│       └── pages/
│           ├── DoctorRecommendation.jsx
│           ├── AdminLogin.jsx
│           └── AdminDoctors.jsx
├── .env.example
├── README.md
├── README_SQLite.md
└── CareGraph_Doctor_Recommendation_SQLite.zip   # packaged copy, not used at runtime
```

---

## 3. Architecture Diagram

```text
                       ┌──────────────────────── Browser ────────────────────────┐
                       │  React 18 + Vite SPA (localhost:5173)                   │
                       │  App.jsx (state routing) → DoctorRecommendation.jsx      │
                       │                            → AdminLogin / AdminDoctors   │
                       │  api/client.js  (fetch wrapper, credentials: 'include')  │
                       └───────────────┬──────────────────────────────────────────┘
                                       │  /api/*  (Vite dev proxy → 127.0.0.1:5000)
                                       ▼
                       ┌──────────────────────── Flask  Backend/app.py ───────────┐
                       │ load_artifacts() → joblib model + metadata + mapping CSV │
                       │ build_feature_vector() / predict_recommendations()       │
                       │ after_request CORS · errorhandler(500)                   │
                       │ blueprint: admin_bp  (/api/admin)                        │
                       └───┬───────────────────────────────┬──────────────────────┘
                           │ sys.path.insert(SYMPTOM_DIR)  │ doctor_recommender (singleton)
                           ▼                               ▼
        ┌──────────────────────────────┐   ┌───────────────────────────────────────┐
        │ SymptomExtraction/            │   │ Backend/doctor_recommender_sqlite.py  │
        │  symptom_pipeline.py          │   │  DoctorRecommender.search()           │
        │   ├─ symptom_extractor.py     │   │   (strict specialty, Haversine,       │
        │   │    MultiClinNER (HF)      │   │    fee/rating/distance filters,       │
        │   ├─ symptom_context.py       │   │    sort, pagination, CRUD)            │
        │   └─ symptom_normalizer.py    │   └───────────────┬───────────────────────┘
        │        ← symptom_vocabulary   │                   │ sqlite3 (bound params)
        └──────────────────────────────┘                   ▼
                                              ┌──────────────────────────────┐
                                              │ Data/caregraph_doctors.db    │
                                              │  table: doctors (150 rows)   │
                                              └──────────────────────────────┘
```

---

## 4. Technology Stack

| Layer | Technology | Evidence |
|---|---|---|
| Backend | Flask ≥3.0,<4.0; `jsonify`, Blueprint, `session` | `Backend/requirements.txt`, `Backend/app.py` |
| Data (doctors) | stdlib `sqlite3` (no ORM), `sqlite3.Row`, `contextmanager` | `Backend/doctor_recommender_sqlite.py` |
| ML (disease) | scikit-learn `RandomForestClassifier` persisted with `joblib`; `pandas` for feature vectors | `Recommendation/caregraph_recommendation_artifacts/model_metadata.json` |
| ML (symptoms) | `ner_nlp4bia` (GitHub) → MultiClinNER `BSC-NLP4BIA/multiclinner-en-symptom-BiomedBERT` (PyTorch/transformers) | `SymptomExtraction/symptom_extractor.py`, `SymptomExtraction/requirements.txt` |
| Auth | Werkzeug password hashing, `secrets.compare_digest`, `secrets.token_urlsafe(32)` CSRF, signed HttpOnly session cookie | `Backend/admin_auth.py` |
| Frontend | React 18.3.1, react-dom, Vite 5.4.11, `@vitejs/plugin-react` | `frontend/package.json` |
| Runtime | Python 3.14 (bytecode `cpython-314`) with venv at `SymptomExtraction/.venv` | `__pycache__` files, notebook output paths |
| Tests | stdlib `unittest`, temporary SQLite databases | `Backend/tests/` |

---

## 5. File-by-File Explanation

### 5.1 `Backend/app.py` — Flask API (433 lines)

**Purpose:** load ML artifacts once, expose prediction and directory HTTP routes, mount the admin
blueprint, and apply CORS/error policy.

**Imports:** `json`, `os`, `re`, `secrets`, `sys`, `pathlib.Path`, `typing.Any`; `joblib`,
`pandas as pd`, `flask`. Local: `admin_routes.admin_bp`,
`doctor_recommender_sqlite.doctor_recommender`.

**Path resolution:**

- `PROJECT_ROOT = Path(__file__).resolve().parents[1]` → the CareGraph root.
- `ARTIFACT_DIR = PROJECT_ROOT / "Recommendation" / "caregraph_recommendation_artifacts"`.
- `SYMPTOM_DIR = PROJECT_ROOT / "SymptomExtraction"` is inserted into `sys.path` so that
  `from symptom_pipeline import analyze_patient_text` works without moving files.

**Security/config block:**

- `CAREGRAPH_SECRET_KEY` with a random `secrets.token_hex(32)` fallback plus a warning print.
- Session cookie flags: `SESSION_COOKIE_HTTPONLY=True`, `SESSION_COOKIE_SAMESITE="Lax"`,
  `SESSION_COOKIE_SECURE` from `CAREGRAPH_COOKIE_SECURE`.

**`apply_cors` (`@app.after_request`):** adds `Access-Control-Allow-*` headers only when the
request `Origin` exactly equals `CAREGRAPH_FRONTEND_ORIGIN`; also adds a `Vary: Origin` header.

**`handle_internal_error` (`@app.errorhandler(500)`):** always returns
`{"success": false, "error": "Internal server error."}` — never a stack trace.

**`load_artifacts()`:** requires the three artifact files; validates the `feature_names` and
`disease_classes` keys; requires mapping CSV columns `disease` and `specialty`; normalizes keys
with `clean_text`; **drops ambiguous disease→multiple-specialty entries** and de-duplicates;
validates that the model's class count equals the metadata length. Returns
`(model, metadata, feature_names, feature_lookup, specialty_mapping, ambiguous_diseases)`.

**`build_feature_vector(confirmed_symptoms)`:** builds a one-row DataFrame in the exact saved
column order; cells are `0`/`1`; returns `(vector, unknown_symptoms)`.

**`predict_recommendations(confirmed_symptoms, top_k=3)`:** returns `needs_more_information` when
no symptoms are supplied or all are unknown to the model; otherwise uses `predict_proba` → ranked
candidates `{disease_candidate, model_score, possible_specialty}` plus safety fields; includes a
`predict()`-only fallback branch.

**`attach_doctor_results(recommendation, city, doctors_per_specialty)`:** runs only when
`status == "candidate_only"`; de-duplicates specialties; calls `doctor_recommender.search(...)`
per specialty; attaches `doctor_recommendations` and a synthetic-data notice.

### 5.2 `Backend/doctor_recommender_sqlite.py` — SQLite access layer (767 lines)

**Purpose:** the single authoritative data layer for the `doctors` table: strict specialty
matching, Haversine distance, filters, sorting, pagination, and admin CRUD.

Key module constants: `DOCTOR_COLUMNS` (14 columns), `EARTH_RADIUS_KM = 6371.0088`,
`ALLOWED_CITIES = {"mumbai", "thane"}`, `SORT_KEYS`, `SORT_ALIASES`, `ALIASES` (spelling
variants), `DISPLAY` (15 display names).

Functions: `_key`, `canonical_specialty`, `haversine_km`, `_coerce_number`,
`validate_coordinates`, `_display_number`, `_row_to_doctor`, `_neg`, `_pos`, `_sort_key`,
`validate_doctor_payload`, and class `DoctorRecommender` with `search`, `list_specialties`,
`list_cities`, `get_doctor`, `list_doctors_admin`, `create_doctor`, `update_doctor`,
`delete_doctor`. Module-level singleton: `doctor_recommender = DoctorRecommender()`.

### 5.3 `Backend/admin_auth.py` — authentication + CSRF (111 lines)

`admin_username()`, `admin_password_hash()` (prefers `CAREGRAPH_ADMIN_PASSWORD_HASH`, otherwise
hashes `CAREGRAPH_ADMIN_PASSWORD` once and caches it), `admin_configured()`,
`verify_credentials()` (constant-time username comparison + `check_password_hash`),
`login_admin()` (clears the session, sets auth/user/CSRF values), `logout_admin()`,
`is_authenticated()`, `current_csrf_token()`, `_csrf_ok()`, and the `admin_required` decorator
(401 when unauthenticated; 403 when a mutating method lacks a valid `X-CSRF-Token`).

### 5.4 `Backend/admin_routes.py` — admin blueprint (137 lines)

`admin_bp = Blueprint("admin", __name__, url_prefix="/api/admin")` with `POST /login`,
`POST /logout`, `GET /me`, `GET /doctors`, `POST /doctors`, and
`GET|PUT|DELETE /doctors/<path:doctor_id>`. Delegates to `doctor_recommender`; the list route also
returns `specialties` and `cities`.

### 5.5 `Backend/doctor_recommender.py` — LEGACY (153 lines)

An earlier Excel/Pandas recommender reading
`Data/CareGraph_Doctor_Directory_Mumbai_Thane_Synthetic.xlsx`. It is **not imported by `app.py`**,
so it is dead code — but it still instantiates a module-level singleton at import time.

### 5.6 `Backend/import_doctors_to_sqlite.py` (26 lines)

Rebuilds the database from
`Data/CareGraph_Doctor_Directory_Mumbai_Thane_Synthetic.xlsx` (sheet `Doctors`), validates the
required columns, writes with `df.to_sql("doctors", if_exists="replace")`, and creates three
indexes. **The source `.xlsx` is not present in `Data/`** (verified), so this script would fail if
run.

### 5.7 `Backend/tests/*`

- `_support.py` — temporary DB helpers plus the production schema; `copy_real_db()` guarantees
  tests never mutate real data.
- `test_distance.py` — 13 Haversine/coordinate-validation tests.
- `test_doctor_search.py` — specialty/distance/filter/sort/pagination tests plus real-DB read-only
  sanity checks.
- `test_admin_crud.py` — 22 auth + CRUD + public-API tests, run against a **copy** of the real DB.

The README documents **68 tests total**.

### 5.8 `SymptomExtraction/symptom_extractor.py` (76 lines)

Lazy MultiClinNER wrapper: `get_extractor()` (singleton `PipelineInferencer(MODEL_NAME)`, times the
load), `get_model_load_seconds()`, and `extract_symptoms(text)` → list of
`{symptom, start, end, text_in_input, label}` filtered to `label.upper() == "SYMPTOM"`.

### 5.9 `SymptomExtraction/symptom_context.py` — rule-based context

Regexes: `CLAUSE_BOUNDARIES` (`but|however|although|whereas|nevertheless|yet|;`),
`SENTENCE_BOUNDARIES`, and the pattern lists `NEGATION_PATTERNS`, `UNCERTAINTY_PATTERNS`,
`RESOLVED_PATTERNS`, `HISTORICAL_PATTERNS` (including `\bhad\b`, `\byesterday\b`,
`\blast week\b`), `OTHER_PERSON_PATTERNS` (including he/she/they). Functions: `_matches_any`,
`_find_sentence`, `_find_clause`, `_other_person_mentioned_before_symptom`,
`classify_symptom_context(...)`.

**Precedence:** `other_person → resolved → negated → uncertain → historical → present`.
Offsets are validated (`ValueError` on invalid spans).

### 5.10 `SymptomExtraction/symptom_normalizer.py` (290 lines)

`clean_text`, `load_vocabulary()` (reads `symptom_vocabulary.csv`, otherwise the first column of
`Symptom_Weights.csv`), the `SYNONYMS` dict, `MIN_FUZZY_SCORE = 0.88`,
`MIN_SCORE_MARGIN = 0.08`. Class `SymptomNormalizer` provides `preprocess_phrase` (iteratively
strips conversational prefixes/suffixes and leading articles), `__init__` (vocabulary +
cleaned→canonical map + validated aliases), and `normalize(phrase)` returning one of
`empty_input | exact (1.0) | synonym (0.95) | fuzzy (rounded ratio) | unmatched (with suggestion)`.

### 5.11 `SymptomExtraction/symptom_pipeline.py` (283 lines)

`get_normalizer()` (singleton), `analyze_patient_text(text)` (four steps — see §7), and
`print_analysis(result)`.

### 5.12 `SymptomExtraction` helper scripts

- `inspect_symptoms.py` — inspects `Symptom_Weights.csv` and writes `symptom_vocabulary.csv`.
- `test_symptoms.py` — five smoke cases; measures load/inference time; writes
  `symptom_test_results.json`.
- `test_symptoms_normalization.py` — prints four normalization examples.

### 5.13 Frontend

- `src/main.jsx` — React root and `styles.css` import.
- `src/App.jsx` — state-based routing between `recommend` and `admin`; restores the admin session
  via `adminMe()` on mount; `handleLogout`.
- `src/api/client.js` — `request()` wrapper (`credentials: 'include'`, CSRF header, `ApiError`),
  `buildQuery`, and nine exported API functions.
- `src/pages/DoctorRecommendation.jsx` — symptom analysis + directory browser + filters +
  pagination.
- `src/pages/AdminLogin.jsx`, `src/pages/AdminDoctors.jsx`, `src/components/DoctorCard.jsx`.
- `src/styles.css` — CSS custom properties in `:root`, component classes, and one
  `@media (max-width: 620px)` responsive breakpoint.

### 5.14 Artifacts and data

| File | Role |
|---|---|
| `disease_classifier.joblib` | Binary artifact; the trained 41-class RandomForest loaded via `joblib.load` |
| `model_metadata.json` | 131 `feature_names`, 41 `disease_classes`, `training_dataset: Original_Dataset.csv`, `model_type: RandomForestClassifier`, warning string |
| `disease_specialty_mapping_needs_review.csv` | 41 rows; columns `disease_raw, specialty_raw, disease, specialty` |
| `classifier_comparison_results.csv` | Six candidate models with metrics |
| `Data/caregraph_doctors.db` | Verified read-only: table `doctors` (14 columns), 150 rows, 15 specialties, cities Mumbai/Thane, 3 indexes |

---

## 6. Function and Component Inventory

| File | Function/Class | Purpose | Input | Output | Called By | Calls |
|---|---|---|---|---|---|---|
| `Backend/app.py` | `clean_text` | lowercase/collapse whitespace | Any | str | `feature_key`, `load_artifacts`, `attach_doctor_results`, `predict_recommendations` | `re.sub` |
| `Backend/app.py` | `feature_key` | symptom name → model key | Any | str | `build_feature_vector`, `load_artifacts` | `clean_text` |
| `Backend/app.py` | `load_artifacts` | load + validate model/metadata/mapping | — | 6-tuple | module import | `joblib.load`, `pd.read_csv` |
| `Backend/app.py` | `attach_doctor_results` | attach directory groups | dict, city, n | dict | `analyze`, `recommend_from_symptoms` | `doctor_recommender.search` |
| `Backend/app.py` | `build_feature_vector` | one-hot 1-row DataFrame | list[str] | (DataFrame, unknown) | `predict_recommendations` | `feature_key`, `pd.DataFrame` |
| `Backend/app.py` | `predict_recommendations` | rank disease candidates | list[str], top_k | dict | `analyze`, `recommend_from_symptoms` | `build_feature_vector`, `predict_proba` |
| `Backend/app.py` | `health`, `list_doctors`, `list_specialties`, `analyze`, `recommend_from_symptoms` | HTTP routes | Flask request | JSON | Flask | see §13 |
| `Backend/app.py` | `apply_cors`, `handle_internal_error` | hooks | response/error | response | Flask | — |
| `doctor_recommender_sqlite.py` | `canonical_specialty` | alias/display normalization | Any | str | `search`, `_row_to_doctor`, `list_specialties`, `validate_doctor_payload` | `_key` |
| `doctor_recommender_sqlite.py` | `haversine_km` | great-circle km | 4 lat/lon | float\|None | `search` | `math` |
| `doctor_recommender_sqlite.py` | `_coerce_number` | validated numeric cast | value, name, bounds | (num\|None, err\|None) | `search`, `validate_doctor_payload`, `validate_coordinates` | — |
| `doctor_recommender_sqlite.py` | `validate_coordinates` | pair-wise lat/lon validation | lat, lon | (lat, lon, err) | `search` | `_coerce_number` |
| `doctor_recommender_sqlite.py` | `_row_to_doctor` | row → API dict | `sqlite3.Row` | dict | `search`, `list_doctors_admin`, `get_doctor` | `canonical_specialty`, `_display_number` |
| `doctor_recommender_sqlite.py` | `_neg`, `_pos`, `_sort_key` | deterministic sort keys | doctor, key, has_coords | tuple | `search` | — |
| `doctor_recommender_sqlite.py` | `DoctorRecommender.search` | specialty + filters + sort + page | 11 params | dict | `app.list_doctors`, `attach_doctor_results` | `_connection`, `haversine_km`, `_sort_key` |
| `doctor_recommender_sqlite.py` | `DoctorRecommender.list_specialties/list_cities` | directory facets | — | list | `app.list_specialties`, admin list | `_connection` |
| `doctor_recommender_sqlite.py` | `DoctorRecommender.get_doctor` | fetch one | doctor_id | dict\|None | CRUD, admin get | `_connection` |
| `doctor_recommender_sqlite.py` | `DoctorRecommender.list_doctors_admin` | search + paginate | q/spec/city/page | dict | `admin_list_doctors` | `_connection` |
| `doctor_recommender_sqlite.py` | `create_doctor`, `update_doctor`, `delete_doctor` | admin writes | payload/id | (dict, status) | `admin_routes` | `validate_doctor_payload`, `get_doctor`, `_connection` |
| `doctor_recommender_sqlite.py` | `validate_doctor_payload` | field validation/normalization | payload, require_all | (values, errors) | create/update | `_coerce_number` |
| `admin_auth.py` | `admin_username`, `admin_password_hash`, `admin_configured` | env credential resolution | — | str/bool | routes, verify | `os.getenv`, `generate_password_hash` |
| `admin_auth.py` | `verify_credentials` | credential check | user, pass | bool | `admin_login` | `compare_digest`, `check_password_hash` |
| `admin_auth.py` | `login_admin`, `logout_admin`, `is_authenticated`, `current_csrf_token`, `_csrf_ok` | session + CSRF | — | token/bool | routes, decorator | `session`, `secrets` |
| `admin_auth.py` | `admin_required` | route decorator | view | wrapper | applied in `admin_routes` | `is_authenticated`, `_csrf_ok` |
| `admin_routes.py` | `admin_login/logout/me/list_doctors/create_doctor/get_doctor/update_doctor/delete_doctor` | admin REST | Flask request | JSON + status | Flask | auth + recommender |
| `doctor_recommender.py` (legacy) | `DoctorRecommender.search` | Excel lookup | specialty… | dict | nothing (unused) | `pandas` |
| `symptom_extractor.py` | `get_extractor`, `get_model_load_seconds`, `extract_symptoms` | NER | text | list[dict] | `symptom_pipeline` | `PipelineInferencer`, `Document` |
| `symptom_context.py` | `_matches_any`, `_find_sentence`, `_find_clause`, `_other_person_mentioned_before_symptom` | context helpers | text/offsets | str/bool | `classify_symptom_context` | `re` |
| `symptom_context.py` | `classify_symptom_context` | status + experiencer | text, start, end, mention | dict | `symptom_pipeline` | helpers |
| `symptom_normalizer.py` | `clean_text`, `load_vocabulary` | text util / vocab load | str / — | str / list | normalizer | `csv`, `re` |
| `symptom_normalizer.py` | `SymptomNormalizer.preprocess_phrase` | strip framing | phrase | str | `normalize` | `clean_text`, `re` |
| `symptom_normalizer.py` | `SymptomNormalizer.normalize` | match pipeline | phrase | dict | `get_normalizer().normalize` | `SequenceMatcher` |
| `symptom_pipeline.py` | `get_normalizer` | singleton normalizer | — | SymptomNormalizer | `analyze_patient_text` | — |
| `symptom_pipeline.py` | `analyze_patient_text` | orchestrator | text | dict | `app.analyze` | extractor, context, normalizer |
| `symptom_pipeline.py` | `print_analysis` | CLI print | dict | None | `__main__` | — |
| frontend | `DoctorRecommendation` | analysis + directory UI | — | JSX | `App` | client API, `DoctorCard` |
| frontend | `AdminLogin`, `AdminDoctors`, `DoctorCard` | auth, CRUD UI, card | props | JSX | `App` / `AdminDoctors` | client API |
| frontend | `request`, `buildQuery`, 9 API functions | HTTP layer | path/params | Promise | pages | `fetch` |

---

## 7. Symptom Extraction Pipeline

**Orchestrator:** `analyze_patient_text(text)` in `SymptomExtraction/symptom_pipeline.py`.

1. **Type guard** — non-string raises `TypeError`; blank text returns an early dict containing
   `unmatched_symptoms: []`.
2. **Step 1 — extract:** `extract_symptoms(text)` → MultiClinNER annotations filtered to `SYMPTOM`.
3. **Step 2 — context:** for each mention, `classify_symptom_context(text, start, end, symptom_text=mention)`.
4. **Step 3 — normalize:** `normalizer.normalize(mention)` → exact / synonym / fuzzy / unmatched.
5. **Step 4 — partition:** `confirmed = item["status"] == "present" AND item["normalization_matched"]`.

### Answers to the specific questions

| Question | Answer |
|---|---|
| Is a pretrained NER model used? | Yes — MultiClinNER `BSC-NLP4BIA/multiclinner-en-symptom-BiomedBERT`, loaded once per process via `ner_nlp4bia`. |
| How are synonyms handled? | The `SYNONYMS` dict, validated against the vocabulary at `__init__`. |
| Negation / uncertainty / historical / resolved / other-person? | All rule-based via regex pattern lists, with clause splitting so `but` resets negation scope. |
| How are confirmed symptoms selected? | Requires both `status == "present"` **and** a successful normalization. |
| How does the result reach the classifier? | `app.analyze` collects `normalized_symptom` from `confirmed_symptoms`, de-duplicates, and passes them to `predict_recommendations`. |

### Illustrative trace (hypothetical offsets — labelled as such)

Input: `"I have a headache, but I do not have a fever."`

| Mention | Span (illustrative) | Context | Normalization | Result bucket |
|---|---|---|---|---|
| `headache` | 9–17 | clause before `but` → **present** | exact → `headache` | **confirmed_symptoms** |
| `fever` | 35–40 | clause after `but` → **negated** | unmatched — the vocabulary has `mild_fever`/`high_fever`, not bare `fever`; `SequenceMatcher("fever","mild fever") ≈ 0.67 < 0.88` | `negated_symptoms` **and** `unmatched_normalizations` |

So the downstream model receives **`["headache"]`** only. *(Hypothetical — no model was executed.)*

---

## 8. Disease Classification and Specialty Mapping

- **Algorithm:** `RandomForestClassifier` (`n_estimators=300`, `class_weight="balanced_subsample"`,
  `random_state=42`, `n_jobs=-1`) inside a `SimpleImputer(strategy="most_frequent")` pipeline.
  Evidence: notebook cell + `model_type` in metadata.
- **Input features:** a one-row **binary** vector over the **131** `feature_names` (one per
  canonical symptom), in the saved column order.
- **Classes/labels:** **41** disease classes (verified from `model_metadata.json`; `load_artifacts`
  also asserts the model's `classes_` count matches).
- **Loading / prediction:** `load_artifacts()` → `joblib.load`; `predict_recommendations()` →
  `predict_proba(feature_vector)[0]` → sorted descending → top-k (k clamped 1–5, default 3).
- **Prediction scores:** RandomForest **tree-vote proportions** — *not* calibrated clinical
  probabilities and *not* diagnoses.
- **Mapping:** `disease_specialty_mapping_needs_review.csv` (`disease`→`specialty`), keyed by
  `clean_text`. Ambiguous diseases (multiple specialties) are **excluded**; the notebook reports
  `Diseases with multiple specialty labels: {}`, so this set is currently empty.
- **Missing mappings:** `possible_specialty` becomes `None`; the UI shows "no specialty mapped" and
  no doctor group is created.
- **Multiple candidates:** each distinct specialty from the ranked candidates produces one labelled
  doctor group.

---

## 9. Model Performance and Evaluation Metrics

### 9.1 Summary table

| Model | Task | Dataset | Accuracy | Precision | Recall | F1-score | Evaluation Status |
|---|---|---|---|---|---|---|---|
| MultiClinNER `...multiclinner-en-symptom-BiomedBERT` | Symptom NER (mention extraction) | 5 hand-written smoke sentences | Not available in the inspected project | Not available in the inspected project | Not available in the inspected project | Not available in the inspected project | Only **execution** success (5/5 ran without error); recorded load 105.79 s, mean inference 0.1468 s. No entity-level metric. |
| RandomForestClassifier (`disease_classifier.joblib`) | 41-class disease classification | `Original_Dataset.csv`, 80/20 split, `random_state=42` (test support ≈ 984) | **1.00** (reported) | **1.00** macro | **1.00** macro | **1.00** macro & weighted | Reported in `classifier_comparison_results.csv` and the notebook's `classification_report`. **Not re-run in this pass.** |
| Logistic Regression | same | same | 1.00 | 1.00 | 1.00 | 1.00 | Comparison row |
| Linear SVM | same | same | 1.00 | 1.00 | 1.00 | 1.00 | Comparison row (row name is malformed — the pipeline repr leaked into the `Model` column) |
| Extra Trees | same | same | 1.00 | 1.00 | 1.00 | 1.00 | Comparison row |
| Gradient Boosting | same | same | 1.00 | 1.00 | 1.00 | 1.00 | Comparison row |
| LightGBM | same | same | 1.00 | 1.00 | 1.00 | 1.00 | Comparison row |
| XGBoost | same | same | Not available in the inspected project | — | — | — | Defined in the notebook but emits **no row** in the CSV |

### 9.2 Reliability caveats (evidence-based)

- **Trivially separable data.** The notebook explicitly computes
  `Patterns associated with multiple disease labels: 0` — each unique symptom pattern maps to
  exactly **one** disease. A perfect score is therefore expected and is **not** evidence of
  clinical utility.
- **No cross-validation** was found in the notebook (a single
  `train_test_split(test_size=0.20, random_state=42)`); no external validation set; no calibration
  analysis; no confusion matrix persisted to disk.
- **No accuracy metric exists for the NER stage** — only timing and execution success.
- **Distinguish the concepts:** `model_score` = tree-vote proportion; a true "predicted
  probability" would require calibration; "classification performance" is what the CSVs show;
  "clinical reliability" is **not established** for any component.

---

## 10. SQLite Schema and Database Operations

**Verified read-only** via `sqlite3.connect("file:Data/caregraph_doctors.db?mode=ro", uri=True)`:

```sql
CREATE TABLE "doctors" (
  "Doctor_ID" TEXT, "Doctor_Name" TEXT, "Specialization" TEXT,
  "Age" INTEGER, "Experience_Years" INTEGER, "Description" TEXT,
  "Address" TEXT, "City" TEXT, "State" TEXT, "Latitude" REAL,
  "Longitude" REAL, "Consultation_Fee_INR" INTEGER,
  "Rating_Out_of_5" REAL, "Data_Status" TEXT
);

CREATE INDEX idx_doctors_specialization ON doctors(Specialization);
CREATE INDEX idx_doctors_city           ON doctors(City);
CREATE INDEX idx_doctors_specialty_city ON doctors(Specialization, City);
```

- **No PRIMARY KEY / UNIQUE constraint** is declared (see §17, gap #3).
- **Row count:** 150.
- **Specialties (15):** Allergist, Cardiologist, Dermatologist, Endocrinologist,
  Gastroenterologist, Gynecologist, Hepatologist, Internal Medicine, Neurologist, Osteopathic,
  Otolaryngologist, Pediatrician, Phlebologist, Pulmonologist, Rheumatologist.
- **Cities:** Mumbai, Thane.
- **Path resolution:** `DEFAULT_DB = <root>/Data/caregraph_doctors.db`, overridable via
  `CAREGRAPH_DOCTOR_DB`; relative paths are resolved against the project root.
- **Connections:** `sqlite3.connect`, `row_factory = sqlite3.Row`, and a
  `@contextmanager _connection()` that commits on success and always closes.
- **Parameterization:** all queries use `?` bound parameters; identifiers are quoted; sort keys are
  whitelisted via `SORT_ALIASES` — no user value is interpolated into SQL.
- **Error handling:** `sqlite3.Error` → generic message + `details`; a missing DB file sets
  `load_error` and every method returns an "unavailable" envelope (HTTP 503).

---

## 11. Doctor Recommendation Algorithm

Order of operations inside `DoctorRecommender.search` (matches the README):

1. **Specialty required** (`""` / whitespace / `None` → error).
2. `canonical_specialty` (alias + display normalization).
3. **City** validated against `ALLOWED_CITIES` (`mumbai` / `thane`) → display casing.
4. **Sort key** validated via `SORT_ALIASES` → one of
   `recommended | rating | fees | distance | experience`.
5. **Coordinates** validated as a pair, ranges −90..90 / −180..180, NaN/inf rejected.
6. Numeric filters validated: `max_distance_km ≥ 0` (requires coordinates), `max_fee ≥ 0`,
   `min_rating` 0–5.
7. **SQL candidate query:** `lower(trim(Specialization)) = lower(trim(?))` (+ optional city),
   parameterized.
8. **Distance:** `haversine_km(user, doctor)` per row → `distance_km` rounded to 2 dp; `None` when
   coordinates are missing.
9. **Filters applied:** distance ≤ max → fee ≤ max → rating ≥ min.
10. **Sort** by `_sort_key(...)`; missing values always last (`_neg`/`_pos` return `inf`);
    `Doctor_ID` ascending is the final deterministic tie-break.
11. **Pagination last** (`page`/`page_size`, otherwise legacy `limit` 1–50). `total` always reflects
    the filtered set.

**Capability confirmation:**

| Capability | Present? |
|---|---|
| Haversine distance | ✅ |
| Max-distance filter | ✅ |
| Highest-rating sort | ✅ |
| Lowest-fee sort | ✅ |
| Nearest-doctor sort | ✅ (requires coordinates) |
| Experience-based sort | ✅ |

**How the specialty reaches the query:** `predict_recommendations` sets `possible_specialty` from
the mapping → `attach_doctor_results` de-duplicates those specialties → calls
`doctor_recommender.search(specialty=..., city=..., limit=5)` per group.

---

## 12. Frontend and Admin Dashboard

**Routing:** state-based (`page` = `recommend` | `admin`) — no React Router dependency.

### Find a doctor (`DoctorRecommendation.jsx`)

- **State:** `symptomText`, `analyzing`, `analyzeError`, `analysis`, `recommendation`,
  `specialties`, `cities`, `selectedSpecialty`, `selectedCity`, `form`/`applied` filters,
  `locationStatus`, `result`, `message`, `notice`, `loadingDoctors`, `doctorsError`, `page`,
  `filterError`; a `requestId` ref guards against out-of-order responses.
- **Hooks:** `useEffect` loads specialties; `useCallback loadDoctors` re-runs on
  specialty/city/applied/page changes; `useMemo` derives suggested specialties and confirmed
  symptoms.
- **Handlers:** `handleAnalyze` (POST `/api/analyze`; auto-selects the first suggested specialty),
  `updateForm`, `handleUseMyLocation` (browser Geolocation API), `handleApply` (client-side
  validation mirroring the server), `handleClear`.
- **Validation:** coordinates must be paired and in range; max-distance and distance-sort require
  coordinates; fee ≥ 0; rating 0–5.
- **Displays:** confirmed-symptom chips, ranked candidates with score, clickable suggested
  specialties, grouped doctors (`DoctorCard`), a directory browser with
  specialty/city/sort/fee/rating/distance/lat/lon, loading + error + notice states, and pagination.

### Admin

- `AdminLogin` posts `{username, password}`; the password is never persisted (cleared immediately).
- `AdminDoctors` — searchable/filterable paginated table, create/edit modal over all 14 fields, and
  a delete confirmation naming the doctor; `401` responses trigger `onLogout()`.
- **CSRF:** `admin.csrfToken` (from login or `/me`) is sent as `X-CSRF-Token` on every mutating call.

### Connection to Flask

The Vite dev proxy maps `/api` → `http://127.0.0.1:5000` (overridable via `CAREGRAPH_BACKEND_URL`);
`VITE_API_BASE_URL` supports a separate origin.

### Styling

CSS custom properties in `:root`, component classes (`.panel`, `.card`, `.table`, `.modal`, …), and
one `@media (max-width: 620px)` responsive breakpoint.

---

## 13. API Endpoint Documentation

| Property | Details |
|---|---|
| **GET** `/api/health` | Purpose: liveness + artifact status. Request: none. Response: `{success, service, model_loaded, artifact_error}`. Status: `200` or `503`. Consumer: ops/monitoring. |
| **GET** `/api/doctors` | Purpose: directory search. Query: `specialty` (**required**), `city`, `sort`, `limit`, `user_latitude`, `user_longitude`, `max_distance_km`, `max_fee`, `min_rating`, `page`, `page_size`. Validation: all server-side per §11. Processing: `doctor_recommender.search`. DB: parameterized SELECT. Response: `{success, requested_specialty, specialty, city_filter, sort, filters, count, total, page, page_size, doctors[], notice, message?}`. Errors: `400` validation, `503` DB unavailable. Consumer: `DoctorRecommendation.jsx`. |
| **GET** `/api/specialties` | Purpose: directory facets. Response: `{success, specialties[], cities[]}`. Consumer: `DoctorRecommendation.jsx`. |
| **POST** `/api/analyze` | Purpose: full pipeline + doctors. Request: `{text (required, ≤5000 chars), top_k (1–5, default 3), city?, doctors_per_specialty (1–10, default 5)}`. Processing: `analyze_patient_text` → `predict_recommendations` → `attach_doctor_results`. Response: `{success, symptom_analysis{…}, recommendation{…}}`. Errors: `400` bad body/text, `503` artifacts missing, `500` pipeline failure. Consumer: `DoctorRecommendation.jsx`. |
| **POST** `/api/recommend` | Purpose: recommend from pre-normalized symptom labels. Request: `{symptoms: [str], top_k?, city?, doctors_per_specialty?}`. Response: `{success, recommendation{…}}`. Errors: `400`, `503`, `500`. |
| **POST** `/api/admin/login` | Request `{username, password}` → `{success, authenticated, username, csrf_token}`. `400` missing fields, `401` bad credentials, `503` not configured. |
| **POST** `/api/admin/logout` | `admin_required`; clears session → `{success, authenticated: false}`. `401`/`403`. |
| **GET** `/api/admin/me` | Session probe (no auth required) → `{success, authenticated, admin_configured, username, csrf_token}`. |
| **GET** `/api/admin/doctors` | `admin_required`. Query `q, specialization, city, page, page_size` → list + `specialties`/`cities`. `401`/`503`. |
| **POST** `/api/admin/doctors` | `admin_required` + CSRF. Body = 14 fields → `201` created, `400` validation (`errors[]`), `409` duplicate ID, `503`. |
| **GET** `/api/admin/doctors/<id>` | `admin_required` → `200` `{success, doctor}` or `404`. |
| **PUT** `/api/admin/doctors/<id>` | `admin_required` + CSRF. **Partial** update → `200/400/404/409/500`. |
| **DELETE** `/api/admin/doctors/<id>` | `admin_required` + CSRF → `200` or `404`. |

### Illustrative request/response (not executed)

```json
// POST /api/analyze
{ "text": "I have a headache and mild fever and I feel dizzy", "top_k": 3, "city": "Thane" }
```

```json
{
  "success": true,
  "symptom_analysis": {
    "confirmed_symptoms": [
      { "normalized_symptom": "headache" },
      { "normalized_symptom": "mild_fever" }
    ]
  },
  "recommendation": {
    "status": "candidate_only",
    "ranked_candidates": [
      { "disease_candidate": "hepatitis a", "model_score": 0.27, "possible_specialty": "hepatologist" }
    ],
    "doctor_recommendations": [
      { "specialty": "hepatologist", "doctor_count": 5, "doctors": ["…"] }
    ]
  }
}
```

---

## 14. Complete End-to-End Data Flows

### Flow A — symptom → specialists

```text
textarea → DoctorRecommendation.handleAnalyze
 → client.analyzeSymptoms → POST /api/analyze
 → app.analyze → symptom_pipeline.analyze_patient_text
     → extract_symptoms (MultiClinNER)              [mentions + offsets]
     → classify_symptom_context (rules)             [status / experiencer]
     → SymptomNormalizer.normalize                  [canonical label]
     → confirmed = status == "present" AND matched
 → app: confirmed = sorted{unique normalized_symptom}
 → predict_recommendations → build_feature_vector (131-dim 0/1)
 → RandomForest.predict_proba → top-k candidates + possible_specialty
 → attach_doctor_results → search per specialty
 → JSON {symptom_analysis, recommendation}
 → React renders chips, candidates, specialty buttons, doctor groups
```

### Flow B — directory browse

```text
specialty/city/filters → client.fetchDoctors → GET /api/doctors
 → DoctorRecommender.search → SQL specialty match → Haversine
 → distance/fee/rating filters → sort → pagination
 → JSON → DoctorRecommendation renders DoctorCard list + pager
```

### Flow C — admin CRUD

```text
AdminLogin → POST /api/admin/login → session cookie + csrf_token
 → AdminDoctors → GET/POST/PUT/DELETE /api/admin/doctors* with X-CSRF-Token
 → admin_required (session + CSRF) → DoctorRecommender.{list,create,update,delete}
 → SQLite (parameterized, contextual commit/close) → JSON → table refresh
```

**Disconnected / corrected stages:** there is **no** separate "disease classifier" service and
**no** specialist-mapping file beyond the one CSV; the model is loaded in-process. The proposed
"Feature Vector → Disease Classification" step is real (`build_feature_vector` → `predict_proba`),
but "Candidate Diseases" carry **tree-vote proportions**, not calibrated probabilities.

---

## 15. Configuration, Dependencies, and Startup

### Dependencies

| Component | Dependencies |
|---|---|
| Backend | `Flask>=3.0,<4.0`, `pandas>=2.0`, `joblib>=1.3`, `scikit-learn>=1.3` |
| SymptomExtraction | `git+https://github.com/nlp4bia-bsc/ner-nlp4bia.git` (pulls PyTorch/transformers); venv at `SymptomExtraction/.venv` |
| Frontend | `react`, `react-dom`; dev: `vite`, `@vitejs/plugin-react` |

### Environment variables

From `.env.example`, the README, and the code (values never displayed):
`CAREGRAPH_DOCTOR_DB`, `CAREGRAPH_SECRET_KEY`, `CAREGRAPH_ADMIN_USERNAME`,
`CAREGRAPH_ADMIN_PASSWORD_HASH`, `CAREGRAPH_ADMIN_PASSWORD`, `CAREGRAPH_COOKIE_SECURE`,
`CAREGRAPH_FRONTEND_ORIGIN`, `CAREGRAPH_BACKEND_PORT`, `CAREGRAPH_DEBUG`, and the frontend
build-time `VITE_API_BASE_URL` (plus Vite's `CAREGRAPH_BACKEND_URL`).

### Entry points and paths

- **Backend entry:** `python Backend/app.py` (or `python app.py` from `Backend/`) →
  `127.0.0.1:5000`; `debug` defaults to **true**.
- **Model path:** `<root>/Recommendation/caregraph_recommendation_artifacts/…`.
- **Database path:** `<root>/Data/caregraph_doctors.db`.
- **Frontend:** `npm run dev` (5173) / `npm run build` / `npm run preview` (4173).

*No startup command was executed, nothing was installed, and no training/evaluation was run.*

---

## 16. Existing Tests and Verification Status

| Test file | Focus | Notes |
|---|---|---|
| `tests/_support.py` | Temp-DB helpers + production schema | `copy_real_db()` guarantees tests never mutate real data |
| `tests/test_distance.py` | Haversine + coordinate validation (13 tests) | 1° latitude ≈ 111.19 km; Mumbai→Delhi ≈ 1100–1200 km; symmetry; NaN/inf/None → `None` |
| `tests/test_doctor_search.py` | Strict specialty, distance, filters, all five sorts, pagination, real-DB sanity | Known-coordinate fixture with exact ordering assertions |
| `tests/test_admin_crud.py` | Auth (login/logout/me), 401/403 CSRF, full CRUD, partial update, duplicate 409, validation 400, public API | Env vars set before importing `app`; DB swapped to a copy |

- The README documents **68 tests**. They were **not re-executed during this read-only pass** —
  no pass/fail result is claimed for this run.
- `SymptomExtraction/test_symptoms.py` is a manual smoke script, not an assertion suite; it
  **writes** `symptom_test_results.json` when run (it was not run).
- Recorded artifact `symptom_test_results.json`: 5/5 executed, 0 failures, model init 105.79 s,
  mean inference 0.1468 s.
- **ML verification:** classifier metrics exist as reported numbers only; the NER stage has **no**
  precision/recall/F1 evidence anywhere in the project.

---

## 17. Confirmed Limitations and Integration Gaps

| # | Issue | Affected file | Evidence | Impact | Confirmed / Inferred |
|---|---|---|---|---|---|
| 1 | Import source workbook missing | `import_doctors_to_sqlite.py` | `Data/` has no `CareGraph_Doctor_Directory_Mumbai_Thane_Synthetic.xlsx` (only `Specialist.xlsx`) | Rebuilding the DB from source fails with `FileNotFoundError` | **Confirmed** |
| 2 | Dead legacy module | `Backend/doctor_recommender.py` | Not imported by `app.py`; still instantiates a singleton at import | Confusion; wasted import work | **Confirmed** |
| 3 | No PK/UNIQUE on `Doctor_ID` | `caregraph_doctors.db` | Verified `sqlite_master` DDL | Uniqueness is enforced only in application logic, not by the DB | **Confirmed** |
| 4 | Time phrases suppress candidates | `symptom_context.py` + `symptom_pipeline.py` | `HISTORICAL_PATTERNS` include `yesterday`/`had`; confirmed requires `status == "present"` | Text like "…since yesterday" yields **no** ranked candidates (the UI placeholder itself contains "since yesterday") | **Confirmed** |
| 5 | Bare "fever" does not normalize | `symptom_normalizer.py` | `SYNONYMS` maps "temperature"/"feeling feverish" but not "fever"; vocabulary has `mild_fever`/`high_fever` | "I have a fever." is not a confirmed symptom | **Confirmed** |
| 6 | Return-key inconsistency | `symptom_pipeline.py` | Blank input returns `unmatched_symptoms`; the normal path returns `unmatched_normalizations` | Consumers must handle two key names | **Confirmed** |
| 7 | Optimum-score dataset | Notebook + `classifier_comparison_results.csv` | `Patterns associated with multiple disease labels: 0`; all models 1.0 | Metrics are not evidence of clinical performance | **Confirmed** |
| 8 | Specialty label is a disease name | `disease_specialty_mapping_needs_review.csv` | Row `tuberculosis,tuberculosis` | The UI can show a disease as a "specialty"; the notebook flags it for review | **Confirmed** |
| 9 | City allow-list hard-coded | `doctor_recommender_sqlite.py` | `ALLOWED_CITIES = {"mumbai", "thane"}` | Adding a city requires a code change | **Confirmed** |
| 10 | Odd feature names | `model_metadata.json` | `"dischromic _patches"`, `"foul_smell_of urine"`, `"spotting_ urination"` | Normalized consistently by `feature_key` on both sides so it works, but trips up external tooling | **Confirmed** |
| 11 | Malformed CSV row | `classifier_comparison_results.csv` | The Linear SVM pipeline repr appears in the `Model` column | Cosmetic / report parsing only | **Confirmed** |
| 12 | XGBoost result absent | notebook | XGBoost defined, no CSV row | Comparison table incomplete | **Confirmed** |
| 13 | No NER evaluation | `SymptomExtraction/` | Only execution + timing recorded | Extraction quality is unmeasured | **Confirmed** |
| 14 | No clinical validation for context rules | `symptom_context.py` | Module docstring: "rule-based prototype, not clinically validated" | Context rules may misfire on complex text | **Confirmed (by design)** |
| 15 | Debug default on | `app.py` | `CAREGRAPH_DEBUG` defaults to `"true"` | Must be overridden for production | **Confirmed** |
| 16 | No rate limiting / audit log / lockout | `admin_auth.py`, `admin_routes.py` | No such code present | Brute-force exposure (mitigated only by generic errors) | **Confirmed** |
| 17 | SPA has no URL routing | `App.jsx` | State-based navigation | Deep links / refresh lose the page (session restored via `/api/admin/me`) | **Confirmed** |
| 18 | `/api/recommend` trusts client labels | `app.py` | Accepts `symptoms` as-is | A client can bypass the extraction pipeline | **Confirmed** |

*No fixes were applied.*

---

## 18. Recommended Future Evaluations and Improvements

> These are recommendations only — nothing was implemented.

1. **Recover or regenerate** the doctor-directory Excel (or make `import_doctors_to_sqlite.py`
   accept a CSV) so the DB is reproducible.
2. **Add a PRIMARY KEY / UNIQUE index** on `Doctor_ID` to enforce uniqueness at the storage layer.
3. **Evaluate the NER stage properly:** build a labelled sample and report entity-level
   precision/recall/F1 plus error analysis on negation/uncertainty phrasing.
4. **Evaluate the classifier honestly:** stratified k-fold cross-validation, a held-out external
   set, a saved confusion matrix, and probability calibration (e.g. reliability curve / Brier
   score). Re-run with grouped splits that prevent identical-pattern leakage.
5. **Add negative/edge-case tests** for phrases such as "since yesterday",
   "I had X, now I feel fine", and "my father has X".
6. **Harmonize pipeline output keys** and document the `confirmed_symptoms` contract.
7. **Handle the historical-vs-current trade-off explicitly** (e.g. an `--include_historical` flag)
   so "since yesterday" text still yields candidates when appropriate.
8. **Extend `SYNONYMS`** for common labels (notably "fever") and consider a data-driven alias table.
9. **Externalize the city allow-list** and consider a seeded/derived facet list.
10. **Operational hardening:** rate limiting, audit logging with no PHI, `CAREGRAPH_DEBUG=false` by
    default for deploys, and optional account lockout.
11. **Fix the two data-quality artifacts** (the mapping CSV's `tuberculosis→tuberculosis`; the
    malformed comparison CSV row) and have a clinician review the whole mapping.
12. **Medical review is mandatory** before any real-world use of the disease→specialty mapping or
    doctor recommendations.

---

## 19. Read-Only Inspection Confirmation

### Inspection actions actually performed

1. `LS` on the project root and on `frontend/`, `SymptomExtraction/`, and `Recommendation/`.
2. **Full file reads:**
   - Backend: `app.py`, `doctor_recommender_sqlite.py`, `admin_auth.py`, `admin_routes.py`,
     `import_doctors_to_sqlite.py`, `doctor_recommender.py`, `requirements.txt`, and all four files
     in `Backend/tests/`.
   - SymptomExtraction: `symptom_extractor.py`, `symptom_context.py`, `symptom_normalizer.py`,
     `symptom_pipeline.py`, `test_symptoms.py`, `test_symptoms_normalization.py`,
     `inspect_symptoms.py`, `symptom_test_results.json`, `symptom_vocabulary.csv` (sampled),
     `requirements.txt`, `README.md`.
   - Recommendation: `model_metadata.json`, `disease_specialty_mapping_needs_review.csv`,
     `classifier_comparison_results.csv`.
   - Root: `README.md`, `README_SQLite.md`, `.env.example`.
   - Frontend: `index.html`, `package.json`, `vite.config.js`, `src/main.jsx`, `src/App.jsx`,
     `src/api/client.js`, `src/pages/DoctorRecommendation.jsx`, `src/pages/AdminLogin.jsx`,
     `src/pages/AdminDoctors.jsx`, `src/components/DoctorCard.jsx`.
3. **`Grep`** over `Recommendation/CareGraph_Doctor_Recommendation.ipynb` (train/test split,
   classifier definitions, `RandomForestClassifier` parameters, `joblib.dump`, artifact writing,
   mapping checks) and over `frontend/src/styles.css`.
4. **One read-only SQLite inspection:**
   `sqlite3.connect("file:Data/caregraph_doctors.db?mode=ro", uri=True)` → `sqlite_master` DDL,
   `COUNT(*)`, distinct specialties/cities. The connection was explicitly opened in **read-only**
   mode.

### Confirmation of no modifications

- No files were created, edited, deleted, renamed, or moved during the analysis
  (this document is a separate, explicitly requested deliverable).
- **No database writes** — the SQLite connection was opened with `mode=ro` and only
  `SELECT` / `sqlite_master` reads were issued.
- No model was trained, retrained, or replaced; no artifact was written.
- No packages were installed or uninstalled; no configuration or environment variable was changed.
- No API keys, passwords, tokens, or hashes were read out or printed.
- Anything not directly evidenced by the inspected files is labelled
  **"Not available in the inspected project"** or explicitly flagged as **hypothetical / inferred**.

---

*End of document.*
