# CareGraph — Doctor Recommendation System

CareGraph turns free-text symptoms into a **specialty** (via the existing symptom
extraction pipeline and disease classifier) and then recommends doctors from a
local SQLite directory, with location filtering, sorting, filters, and a
password-protected admin dashboard for managing the directory.

> **Educational prototype.** Model output is not a diagnosis. All doctor records
> in the bundled database are **synthetic demo data** (fictional doctors and
> clinics, approximate coordinates) — not verified providers.

## Project layout

```text
CareGraph/
├── Backend/
│   ├── app.py                      # Flask API: /api/health, /api/analyze, /api/recommend,
│   │                               #   /api/doctors, /api/specialties; registers admin blueprint
│   ├── doctor_recommender_sqlite.py# SQLite access layer: strict specialty match, Haversine
│   │                               #   distances, filters, sorting, pagination, admin CRUD
│   ├── doctor_recommender.py       # legacy Excel/Pandas recommender (kept, no longer the active path)
│   ├── admin_auth.py               # env-var admin credentials, sessions, CSRF
│   ├── admin_routes.py             # protected /api/admin/* CRUD endpoints
│   ├── import_doctors_to_sqlite.py # rebuild the DB from the Excel source
│   ├── requirements.txt
│   └── tests/                      # unittest suite (uses temporary databases)
├── Data/
│   ├── caregraph_doctors.db        # 150 synthetic doctors, 15 specialties, Mumbai/Thane
│   └── ...                         # symptom weights, disease/specialty datasets
├── Recommendation/
│   └── caregraph_recommendation_artifacts/   # disease_classifier.joblib + metadata + mapping
├── SymptomExtraction/              # symptom pipeline + .venv
└── frontend/                       # React + Vite single-page app
```

## 1. Backend

### Install and run (Windows PowerShell)

```powershell
cd C:\Users\Shreyas\Desktop\CareGraph\Backend
..\SymptomExtraction\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt

# Configure the admin account (required for the admin dashboard)
$env:CAREGRAPH_SECRET_KEY    = "replace-with-a-long-random-value"
$env:CAREGRAPH_ADMIN_USERNAME = "admin"
$env:CAREGRAPH_ADMIN_PASSWORD = "choose-a-local-dev-password"   # hashed on startup

python app.py
```

The API starts on `http://127.0.0.1:5000`.

### Environment variables

| Variable | Purpose | Default |
| --- | --- | --- |
| `CAREGRAPH_DOCTOR_DB` | Path to the SQLite doctor database | `Data/caregraph_doctors.db` |
| `CAREGRAPH_SECRET_KEY` | Flask session signing key (**required in production**) | random per process |
| `CAREGRAPH_ADMIN_USERNAME` | Admin login name | `admin` |
| `CAREGRAPH_ADMIN_PASSWORD_HASH` | Preferred: a Werkzeug password hash | — |
| `CAREGRAPH_ADMIN_PASSWORD` | Dev convenience: plaintext, hashed at startup | — |
| `CAREGRAPH_COOKIE_SECURE` | Set `true` when serving over HTTPS | `false` |
| `CAREGRAPH_FRONTEND_ORIGIN` | Single allowed CORS origin (only needed if not using the Vite proxy) | — |
| `CAREGRAPH_BACKEND_PORT` | API port | `5000` |
| `CAREGRAPH_DEBUG` | Flask debug mode | `true` |

Generate a password hash instead of storing a plaintext password:

```powershell
python -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('your-password'))"
```

See [.env.example](.env.example) for a template. Never commit real secrets.

## 2. Public API

### `POST /api/analyze`

Runs the existing symptom pipeline and attaches directory matches.

```json
{ "text": "I have a headache and nausea", "top_k": 3, "city": "Thane" }
```

Response fields of interest: `symptom_analysis.confirmed_symptoms[]`,
`recommendation.ranked_candidates[]` (`disease_candidate`, `model_score`,
`possible_specialty`), and `recommendation.doctor_recommendations[]` (one labelled
group per distinct specialty).

### `POST /api/recommend`

Same as `/api/analyze` but for clients that already hold normalized symptom labels.

### `GET /api/doctors`

| Parameter | Meaning |
| --- | --- |
| `specialty` | **Required.** Matched strictly (case/whitespace differences and a small set of justified aliases only). A missing or unknown specialty is a validation error — never substituted. |
| `user_latitude`, `user_longitude` | Optional but must be supplied **together**. Latitude −90..90, longitude −180..180, no NaN/inf. Invalid input is rejected with a clear error; nothing is silently filled in. |
| `max_distance_km` | Optional (≥ 0). Requires coordinates. |
| `max_fee` | Optional (≥ 0). |
| `min_rating` | Optional (0–5). |
| `sort` | `recommended` (default), `rating`, `fees`, `distance`/`nearest`, `experience`. `distance` requires coordinates. |
| `city` | Optional: `Mumbai` or `Thane`. |
| `page`, `page_size` | Optional pagination (applied **last**; `total` always reflects the filtered result set). |
| `limit` | Legacy alternative to pagination (1–50). |

Response shape:

```json
{
  "success": true,
  "specialty": "Neurologist",
  "count": 2,
  "total": 8,
  "page": 1,
  "page_size": 20,
  "sort": "distance",
  "filters": { "user_latitude": 19.2183, "user_longitude": 72.9781,
               "max_distance_km": 25, "max_fee": null, "min_rating": null },
  "doctors": [ { "doctor_id": "CG-0045", "name": "…", "distance_km": 1.7, "…": "…" } ],
  "notice": "Demo directory only: …"
}
```

Rules that are enforced server-side:

- **Distances** use the Haversine great-circle formula (Earth radius 6371.0088 km)
  and are returned as `distance_km` rounded to 2 dp. Records without coordinates
  get `distance_km: null` and are only excluded when a distance filter is applied.
- **Order of operations:** specialty match → distance calculation → distance filter
  → fee/rating filters → sort → pagination.
- **Sorting** is numeric. Missing values always sort after present values, and
  `Doctor_ID` ascending is the deterministic final tie-break. `recommended` =
  rating desc, fee asc, distance asc (when coordinates are known), experience desc.
- When nothing matches: `"No doctors are currently available for this specialty
  with the selected filters."` A specialty is **never** auto-substituted.

### `GET /api/specialties`

Returns the specialties and cities actually present in the directory.

```json
{ "success": true, "specialties": ["Allergist", "Cardiologist", "…"], "cities": ["Mumbai", "Thane"] }
```

## 3. Admin API (`/api/admin`)

Login is required for every route except `POST /login` and `GET /me`.
All mutating requests must send the session's CSRF token in the `X-CSRF-Token` header.

| Method | Path | Notes |
| --- | --- | --- |
| `POST` | `/api/admin/login` | `{username, password}` → `{success, authenticated, username, csrf_token}`. `401` on bad credentials, `503` if not configured. |
| `POST` | `/api/admin/logout` | Clears the session. |
| `GET` | `/api/admin/me` | Session status; `401`-free, used to restore state after a page reload. |
| `GET` | `/api/admin/doctors` | List/search: `q`, `specialization`, `city`, `page`, `page_size`. |
| `POST` | `/api/admin/doctors` | Create. `201`, or `400` validation, `409` duplicate ID. |
| `GET` | `/api/admin/doctors/<id>` | `200` or `404`. |
| `PUT` | `/api/admin/doctors/<id>` | Partial update (omitted fields are preserved). |
| `DELETE` | `/api/admin/doctors/<id>` | `200` or `404`. |

Security properties: credentials come only from environment variables, passwords
are compared against Werkzeug hashes (never stored or returned), sessions use
HttpOnly/SameSite cookies (`Secure` configurable), admin endpoints return generic
errors without stack traces, and all SQL uses bound parameters with explicit
transactions.

## 4. Frontend (React + Vite)

The dev server proxies `/api` to the Flask backend, so the browser only talks to
one origin.

```powershell
cd C:\Users\Shreyas\Desktop\CareGraph\frontend
npm install
npm run dev      # http://localhost:5173
```

Production build:

```powershell
npm run build    # emits frontend/dist
```

Pages:

1. **Find a doctor** — symptom textarea with an *Analyze* button (calls
   `POST /api/analyze`), detected symptoms, possible conditions, clickable
   suggested specialties, grouped doctor results, and a directory browser with
   specialty/city selectors, *Use my location* (browser Geolocation API), manual
   latitude/longitude, max distance, max fee, min rating, a five-option sort
   dropdown, and Apply/Clear.
2. **Admin login** — signs in against `/api/admin/login`; the password is never
   stored in `localStorage`.
3. **Manage doctors** — searchable/filterable table with add, edit, and delete
   (delete shows a confirmation naming the doctor and ID).

If the frontend is served from a different origin, set `VITE_API_BASE_URL` when
building, and set `CAREGRAPH_FRONTEND_ORIGIN` on the backend to that origin.

## 5. Tests

```powershell
cd C:\Users\Shreyas\Desktop\CareGraph\Backend
..\SymptomExtraction\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The suite (68 tests) covers Haversine distance and coordinate validation, strict
specialty matching, all filters, all five sort orders, pagination/totals, admin
authentication, CSRF enforcement, and full admin CRUD. It runs against temporary
databases — the real `Data/caregraph_doctors.db` is never modified.

## 6. Safety and data quality

- Directory records are synthetic, coordinates are approximate, and
  `Data_Status` marks each row as a demo record. The API and UI surface this.
- The disease-to-specialty mapping is flagged for medical review; model scores are
  not validated referral probabilities.
- Patient symptom text is not written to logs.
- Out of scope: appointments/booking, payments, prescriptions, EMR, and any
  auto-diagnosis behaviour.
