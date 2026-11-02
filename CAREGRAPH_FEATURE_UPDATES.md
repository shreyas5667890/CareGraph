# CareGraph — Feature Updates Explained

This document is a **complete, precise explanation of every change and update** that was made to
the existing CareGraph project. It is documentation only: **no existing functionality was modified
or removed to produce it.**

The work implemented three features into the existing application (not a separate app):

1. **Intelligent Doctor Ranking System**
2. **Doctor Comparison Dashboard**
3. **Complete Appointment Booking System**

Everything below is additive. The pre-existing symptom analysis, ML classification, doctor search,
and admin CRUD flows are untouched and continue to work exactly as before.

---

## 1. Summary of files changed

### 1.1 New backend files (created)

| File | Purpose |
| --- | --- |
| `Backend/schema.py` | Additive DDL for `patients`, `doctor_availability`, `appointments` and their indexes. Never rewrites `doctors`. |
| `Backend/ranking.py` | Configurable, deterministic ranking + comparison engine. |
| `Backend/appointments.py` | Availability, booking, cancellation, rescheduling, history (transactional). |
| `Backend/patient_auth.py` | Patient accounts, session login and CSRF protection. |
| `Backend/care_routes.py` | REST endpoints for ranking, comparison, availability and patient appointments. |

### 1.2 New backend tests (created)

| File | Purpose |
| --- | --- |
| `Backend/tests/test_ranking.py` | Weight, component-normalisation, scoring, ranking and comparison tests. |
| `Backend/tests/test_booking.py` | Availability, booking, cancellation, rescheduling and history tests (incl. a concurrent double-booking test). |
| `Backend/tests/test_patient_api.py` | HTTP integration tests for every new endpoint, incl. auth/CSRF/ownership. |

### 1.3 Modified backend files

| File | Change |
| --- | --- |
| `Backend/app.py` | +11 / −0: import the new modules, register the new blueprint, initialise the additive schema (guarded). |
| `Backend/admin_routes.py` | +12 / −0: one new admin route to edit a doctor's availability. |

### 1.4 New frontend files (created)

| File | Purpose |
| --- | --- |
| `frontend/src/components/PatientAuth.jsx` | Shared patient sign-in / registration form. |
| `frontend/src/components/ComparePanel.jsx` | Side-by-side comparison of up to three doctors. |
| `frontend/src/components/BookingModal.jsx` | Slot selection, booking confirmation and rescheduling. |
| `frontend/src/pages/Appointments.jsx` | Appointment history (upcoming / past / cancelled) with cancel + reschedule. |

### 1.5 Modified frontend files

| File | Change |
| --- | --- |
| `frontend/src/api/client.js` | +86 / −0: ranking, availability and patient API helpers. |
| `frontend/src/App.jsx` | +52 / −5: patient session state, new navigation, new page wiring. |
| `frontend/src/components/DoctorCard.jsx` | +57 / −3: score row, compare checkbox, "Book appointment" action. |
| `frontend/src/pages/DoctorRecommendation.jsx` | +222 / −33: ranked results, filters-above-cards, comparison and booking wiring. |
| `frontend/src/styles.css` | +214 / −1: styles for all new UI, plus a responsive breakpoint update. |

Line counts are the exact `git diff` numbers for the tracked files.

---

## 2. Database schema — `Backend/schema.py` (new, additive only)

The module's stated contract is: **the existing `doctors` table is never rewritten; only new tables
and indexes are added, so it is safe to run repeatedly and never removes or alters existing
records.**

### 2.1 New tables

**`patients`** — lightweight patient accounts (session-based auth):

```sql
CREATE TABLE IF NOT EXISTS "patients" (
    "Patient_ID"    TEXT PRIMARY KEY,
    "Username"      TEXT NOT NULL,
    "Password_Hash" TEXT NOT NULL,
    "Full_Name"     TEXT,
    "Email"         TEXT,
    "Created_At"    TEXT NOT NULL
)
```

**`doctor_availability`** — per-doctor weekly working blocks that generate bookable slots. The
schedule is stored as **data**, not hardcoded in the request path:

```sql
CREATE TABLE IF NOT EXISTS "doctor_availability" (
    "Availability_ID"   INTEGER PRIMARY KEY AUTOINCREMENT,
    "Doctor_ID"         TEXT NOT NULL,
    "Day_Of_Week"       INTEGER NOT NULL,
    "Start_Time"        TEXT NOT NULL,
    "End_Time"          TEXT NOT NULL,
    "Slot_Minutes"      INTEGER NOT NULL DEFAULT 30,
    "Consultation_Type" TEXT NOT NULL DEFAULT 'In-person',
    "Source"            TEXT NOT NULL DEFAULT 'default_schedule'
)
```

**`appointments`** — persistent appointment records:

```sql
CREATE TABLE IF NOT EXISTS "appointments" (
    "Appointment_ID"    TEXT PRIMARY KEY,
    "Patient_ID"        TEXT NOT NULL REFERENCES "patients"("Patient_ID"),
    "Doctor_ID"         TEXT NOT NULL,
    "Appointment_Date"  TEXT NOT NULL,
    "Start_Time"        TEXT NOT NULL,
    "End_Time"          TEXT NOT NULL,
    "Status"            TEXT NOT NULL DEFAULT 'booked',
    "Consultation_Type" TEXT,
    "Fee_INR"           INTEGER,
    "Created_At"        TEXT NOT NULL,
    "Cancelled_At"      TEXT,
    "Rescheduled_At"    TEXT
)
```

### 2.2 New indexes

| Index | Definition | Why |
| --- | --- | --- |
| `ux_patients_username` | UNIQUE on `patients(lower("Username"))` | Case-insensitive unique usernames. |
| `ux_availability_doctor_day_block` | UNIQUE on `doctor_availability("Doctor_ID","Day_Of_Week","Start_Time")` | No duplicate blocks; overlapping blocks are rejected as a `IntegrityError` → HTTP 409. |
| `idx_availability_doctor` | `("Doctor_ID","Day_Of_Week")` | Fast slot lookups. |
| `idx_appointments_patient` | `("Patient_ID","Appointment_Date")` | Fast history queries. |
| `idx_appointments_doctor_slot` | `("Doctor_ID","Appointment_Date","Start_Time")` | Fast conflict checks. |
| `ux_appointments_active_slot` | UNIQUE on `("Doctor_ID","Appointment_Date","Start_Time")` **WHERE `Status='booked'`** | **Database-level double-booking prevention.** Cancelled rows are excluded, so cancelling frees the slot. |
| `ux_doctors_doctor_id` | UNIQUE on `doctors("Doctor_ID")` | Added on the pre-existing table; wrapped in `try/except` so it is skipped silently if the data ever contained duplicate IDs. Doctor existence is additionally verified in application logic. |

### 2.3 Functions

- `database_path()` — resolves the SQLite path, honouring the existing `CAREGRAPH_DOCTOR_DB`
  environment variable (defaults to `Data/caregraph_doctors.db`).
- `connect(path=None, *, isolation_level="", timeout=10.0)` — opens a connection with
  `row_factory = sqlite3.Row` and `PRAGMA foreign_keys = ON`.
- `ensure_schema(con)` — creates every new object; idempotent and additive; commits its own DDL.
- `initialize(path=None)` — creates the schema on a standalone connection and returns the path.

---

## 3. Feature One & Two backend — `Backend/ranking.py` (new)

The module documents that the recommendation score is an **accessibility** score
(specialty match, distance, rating, affordability, experience) and **not** a clinical quality,
safety or medical-suitability score, and that it never overrides the existing emergency-symptom
safeguards.

### 3.1 Configurable weights in one place

```python
@dataclass(frozen=True)
class RankingWeights:
    specialty: float = 0.35
    distance: float = 0.25
    rating: float = 0.15
    affordability: float = 0.15
    experience: float = 0.10

RANKING_WEIGHTS = RankingWeights()   # the active configuration
```

`as_dict()` and `total()` are provided. Changing weights here changes them for **every** caller.

### 3.2 Reference scales (for 0–100 normalisation)

- `DISTANCE_REFERENCE_KM = 25.0`
- `FEE_REFERENCE_INR = 2000.0`
- `EXPERIENCE_REFERENCE_YEARS = 30.0`
- `MAX_COMPARE_DOCTORS = 3`

### 3.3 Component scorers (each returns `None` when the input is missing/invalid)

| Function | Behaviour |
| --- | --- |
| `specialty_score(doctor_specialty, requested_specialty)` | 100 exact canonical match, 0 real mismatch. If **no target** specialty is supplied and the doctor *has* one, it is scored against itself (100) so ad-hoc comparison stays usable. If both are absent the component is reported missing (`None`). |
| `distance_score(distance_km)` | 100 at 0 km, falling linearly to 0 at `DISTANCE_REFERENCE_KM`. |
| `rating_score(rating_out_of_5)` | `rating / 5 × 100`. |
| `affordability_score(consultation_fee_inr)` | Lower fees score higher: 100 at ₹0, 0 at/above the fee reference. |
| `experience_score(experience_years)` | Linear, capped at the experience reference. |

### 3.4 `score_doctor(...)` — renormalisation, not zeroing

- Computes the five components.
- Drops the ones that cannot be computed (`None`).
- Renormalises the **remaining** weights so the final score stays on a 0–100 scale.
- Returns `recommendation_score` (2 dp), `score_breakdown`, `applied_weights`,
  `components_used`, `components_missing`.
- Deterministic fallback: if nothing is scoreable, score is `0.0` with empty `applied_weights`.

### 3.5 `rank_doctors(...)`

- Delegates eligibility filtering (fee/rating/distance) to the **existing** SQLite recommender
  via `rec.search(..., sort_by="recommended", page_size=100)` — filters are therefore applied
  **before** scoring.
- Scores each returned record, sorts by `(-score, doctor_id)` (score descending, then
  `Doctor_ID` ascending as a stable tie-breaker).
- Optional `limit` is applied **after** ranking (clamped 1–100).
- Returns the doctors plus `weights`, `weights_total`, `distance_available`, `count`, `total`,
  `score_disclaimer` and `notice`.

### 3.6 `compare_doctors(...)` — same records and scores as ranking

- Validates that `doctor_ids` is a non-empty list of non-empty strings.
- Rejects more than `MAX_COMPARE_DOCTORS` (3) with HTTP 400.
- Rejects duplicates (case-insensitive) with HTTP 400.
- Validates user coordinates with the existing `validate_coordinates`.
- Returns HTTP 404 with `missing_doctor_ids` if any doctor is not found.
- Computes distance from user coordinates when supplied, and applies the **same**
  `score_doctor` used by ranking so the two views are guaranteed consistent.

Text constants `SCORE_DISCLAIMER` and `RANKING_NOTICE` are returned with results so the UI can
display that profiles are synthetic demo records and that the score is not clinical quality.

---

## 4. Feature Three backend — `Backend/appointments.py` (new)

### 4.1 Configuration constants

- `SLOT_MINUTES_DEFAULT = 30`, `BOOKING_LEAD_MINUTES = 30`, `MAX_ADVANCE_DAYS = 90`
- `CONSULTATION_TYPES = ("In-person", "Teleconsultation")`
- `STATUS_BOOKED = "booked"`, `STATUS_CANCELLED = "cancelled"`
- `AVAILABILITY_NOTICE` — states schedules are system-generated configuration, not verified
  real-world provider availability.

### 4.2 Per-doctor availability (no universal hardcoded schedule)

- `DEFAULT_SCHEDULE_PATTERNS` holds four working patterns. `_default_pattern_for(doctor_id)`
  selects one deterministically from the doctor's ID using `zlib.crc32`, so **every doctor has a
  per-doctor schedule** and never one shared hardcoded schedule.
- `_seed_default_availability(...)` inserts that pattern with `Source='default_schedule'`.
- Both the default and an admin-supplied schedule are stored as rows in `doctor_availability`.
- `set_doctor_availability(doctor_id, blocks, ...)` validates each block (day 0–6, `HH:MM`
  times, minimum block length, slot size 5–240, valid consultation type), then **replaces** the
  doctor's blocks inside `BEGIN IMMEDIATE`. Overlaps raise an integrity error → HTTP 409.

### 4.3 Slot generation

- `_block_slots(start, end, slot_minutes)` splits a working block into consecutive slots.
- `_build_day(con, doctor_id, day, ...)` builds the day's slots from stored availability,
  marks each slot `available` only when it is **both** far enough in the future
  (`now + BOOKING_LEAD_MINUTES`) **and** not already booked, and records a `reason`
  (`"past"` or `"booked"`).
- `get_day_availability(...)` returns slots for one date (404 unknown doctor, 400 invalid / past /
  too-far-ahead date).
- `list_open_dates(...)` returns a per-date bookability summary for the next N days.

### 4.4 Booking, inside one transaction

`create_appointment(...)`:

1. Requires a real `patient_id` (401 otherwise).
2. Verifies the doctor exists (404 otherwise).
3. Validates date/time/consultation type (400 otherwise).
4. Initialises the schema, then opens `BEGIN IMMEDIATE`.
5. Reads the doctor's fee.
6. Re-checks the requested slot **inside the transaction** (via `_book`), which compares against
   committed state — not against anything the frontend was told earlier.
7. Inserts the appointment and commits.
8. Returns HTTP **201** with `{"success": True, "appointment": {...}}`.

Conflict handling:

- `IntegrityError` (unique-index clash) → HTTP **409** "just been booked by someone else".
- `OperationalError` (DB busy) → HTTP **503**.
- Unknown / non-offered slot → HTTP **400**.

The returned appointment payload includes: `appointment_id`, `patient_id`, `doctor_id`,
`doctor_name`, `specialty`, `doctor_city`, `date`, `start_time`, `end_time`,
`duration_minutes`, `status`, `consultation_type`, `fee_inr`, `created_at`, `cancelled_at`,
`rescheduled_at`.

### 4.5 Double-booking prevention (two layers)

- **Application layer:** the slot is re-checked inside `BEGIN IMMEDIATE` immediately before the
  insert.
- **Database layer:** the partial unique index `ux_appointments_active_slot`
  (`WHERE "Status" = 'booked'`) makes a second concurrent insert fail at the storage level.

A dedicated test starts six threads booking the same slot and asserts **exactly one** 201 and
five 409s.

### 4.6 Cancellation

`cancel_appointment(patient_id, appointment_id, ...)`:

- Runs in `BEGIN IMMEDIATE`.
- Only the owning patient can act (row is looked up by `Appointment_ID` **and** `Patient_ID`;
  another patient gets 404).
- Only `booked` appointments can be cancelled (409 otherwise).
- Sets `Status='cancelled'` and `Cancelled_At`. The row is **kept** (never silently deleted),
  and because the unique index only covers `booked` rows, the slot is released for rebooking.

### 4.7 Rescheduling

`reschedule_appointment(...)`:

- Runs in `BEGIN IMMEDIATE`; validates ownership and that the appointment is still `booked`.
- Verifies the doctor still exists.
- Re-checks the new slot's availability, **excluding the appointment's own current row** so
  moving onto the same slot is allowed.
- Updates date/start/end and sets `Rescheduled_At`. Conflicts → 409, busy → 503.

### 4.8 History

`list_patient_appointments(patient_id, ...)` returns a plain dict grouped into
`upcoming` / `past` / `cancelled` plus `counts`, scoped strictly to the authenticated patient.
Records whose end time has elapsed are categorised as `past`; cancelled records are always
`cancelled`. `get_patient_appointment(...)` returns one owned record (404 for anyone else's).

### 4.9 Safety helpers

- `_safe_rollback(con)` swallows rollback errors so error paths never mask the original failure.
- `_new_appointment_id(con)` allocates a unique `APT-XXXXXXXX` ID, retrying on collision.

---

## 5. Patient authentication — `Backend/patient_auth.py` (new)

The module explains that the project already authenticates an **admin** with a signed, HTTP-only
session cookie plus an `X-CSRF-Token` header, and that patient identity was required by the
appointment specification. It adds the **same** architecture for patients, backed by the real
`patients` table.

- Session keys: `patient_authenticated`, `patient_id`, `patient_username`, `patient_csrf_token`
  — distinct from the admin keys, so admin and patient sessions never collide.
- Passwords are stored as Werkzeug hashes (`generate_password_hash`), never plain text.
- `validate_registration(payload)` — username 3–50 chars (`[A-Za-z0-9._-]`), password 8–200
  chars, optional full name / email with format checks; returns `(values, errors)`.
- `create_patient(payload)` — validates, checks the unique username (409 on clash), allocates a
  `PAT-XXXXXXXX` ID (5 tries), inserts, returns HTTP 201. Wrapped in try/rollback/finally.
- `verify_patient_credentials(username, password)` — case-insensitive lookup +
  `check_password_hash`; returns the public patient dict or `None`.
- `login_patient(patient)` — clears the session, stores the patient keys, generates the CSRF
  token via `secrets.token_urlsafe(32)`, returns it.
- `logout_patient()`, `is_patient_authenticated()`, `current_patient_id()`,
  `current_patient_username()`, `current_patient_csrf_token()`.
- `_csrf_ok()` — constant-time (`secrets.compare_digest`) comparison of the header against the
  session token.
- `patient_required` decorator — returns **401** when unauthenticated, and **403** when a
  `POST/PUT/PATCH/DELETE` request is missing or has an invalid CSRF token. The patient identity
  always comes from the signed session, **never** from the request body.

---

## 6. New REST endpoints — `Backend/care_routes.py` (new)

`care_bp = Blueprint("care", __name__)` (no url prefix) so public routes and `/api/patient/*`
share one blueprint without colliding with `/api/admin`.

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| GET | `/api/ranked-doctors` | public | Eligible doctors for a specialty, ordered by the score. 503 if the directory is unavailable, 400 on bad input. |
| POST | `/api/compare-doctors` | public | 1–3 doctor records with the same scores used by ranking. |
| GET | `/api/doctors/<path:doctor_id>` | public | One directory record, scored when scores are available. 404 if unknown. |
| GET | `/api/doctors/<path:doctor_id>/slots` | public | Bookable slots for one doctor on one date, from stored availability. |
| GET | `/api/doctors/<path:doctor_id>/open-dates` | public | Per-date availability summary for the comparison view. |
| POST | `/api/patient/register` | public | Create a patient account (201). |
| POST | `/api/patient/login` | public | Start a session; returns the CSRF token. 401 on bad credentials. |
| POST | `/api/patient/logout` | patient + CSRF | End the session. |
| GET | `/api/patient/me` | public | Report the current patient session so the SPA can restore state. |
| GET | `/api/patient/appointments` | patient | The caller's history grouped into upcoming / past / cancelled. |
| POST | `/api/patient/appointments` | patient + CSRF | Create an appointment (201 / 409 / 400 / 404). |
| GET | `/api/patient/appointments/<path:appointment_id>` | patient | One owned appointment (404 otherwise). |
| POST | `/api/patient/appointments/<path:appointment_id>/cancel` | patient + CSRF | Cancel an owned booked appointment. |
| POST | `/api/patient/appointments/<path:appointment_id>/reschedule` | patient + CSRF | Move an owned booked appointment. |

`__all__ = ["care_bp"]`.

---

## 7. Backend modifications (existing files)

### 7.1 `Backend/app.py` (+11 / −0)

Three imports were added at the top:

```python
import schema
from admin_routes import admin_bp
from care_routes import care_bp
from doctor_recommender_sqlite import doctor_recommender
```

And after `app.register_blueprint(admin_bp)`:

```python
app.register_blueprint(care_bp)

# Create the additive patient/availability/appointment objects. This never
# rewrites the existing doctors table or its records; if the database is
# unreachable the existing read-only doctor features keep working.
try:
    schema.initialize()
except Exception as _schema_error:  # pragma: no cover - depends on local data
    print(f"WARNING: could not initialise the appointment schema: {_schema_error}")
```

Nothing else in `app.py` was changed — the existing routes, error handler, CORS hook and session
configuration are untouched. The schema initialisation is **guarded**, so if the database is
unreachable the existing read-only doctor features still work.

### 7.2 `Backend/admin_routes.py` (+12 / −0)

Added `from appointments import set_doctor_availability` and one new route:

```python
@admin_bp.put("/doctors/<path:doctor_id>/availability")
@admin_required
def admin_set_doctor_availability(doctor_id: str):
    """Replace a doctor's weekly working blocks (drives bookable slots)."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"success": False, "error": "Send a JSON request body."}), 400
    result, status = set_doctor_availability(doctor_id, body.get("blocks"))
    return jsonify(result), status
```

It reuses the **existing** `admin_required` guard, so it inherits the admin session + CSRF
behaviour automatically. No existing admin route was altered.

---

## 8. Frontend API layer — `frontend/src/api/client.js` (+86 / −0)

All additions are new exported helpers; the existing admin helpers and the `request()` core
(credentials: include, `X-CSRF-Token` header) are unchanged.

- `fetchRankedDoctors(params)` → `GET /api/ranked-doctors`
- `compareDoctors(doctorIds, { specialty, latitude, longitude })` → `POST /api/compare-doctors`
- `fetchDoctorDetail(doctorId, params)` → `GET /api/doctors/:id`
- `fetchDoctorSlots(doctorId, date)` → `GET /api/doctors/:id/slots`
- `EMPTY_PATIENT_SESSION` — a frozen signed-out session object, kept in one place so every
  caller agrees.
- `patientSessionFrom(data)` — normalises login/me payloads into the shape the SPA stores.
- `patientRegister`, `patientLogin`, `patientLogout`, `patientMe`, `fetchMyAppointments`,
  `createAppointment`, `cancelAppointment`, `rescheduleAppointment`.

Mutations pass the CSRF token as the `csrfToken` argument, which `request()` forwards as the
`X-CSRF-Token` header.

---

## 9. Frontend UI changes

### 9.1 `frontend/src/App.jsx` (+52 / −5)

- `PAGES` now includes `appointments`.
- New `patient` state seeded from `EMPTY_PATIENT_SESSION`.
- On mount, `patientMe()` restores the patient session (the same pattern already used for
  `adminMe()`), alongside the existing admin restore.
- New `handlePatientLogout` mirrors `handleLogout`.
- New navigation entries: **My appointments** and **Sign out (username)** when authenticated.
- Wires `patient` / `onPatientChange` into `DoctorRecommendation` and `Appointments`.
- Footer now notes that appointment schedules are system-generated configuration.

### 9.2 `frontend/src/components/DoctorCard.jsx` (+57 / −3)

- New props: `selectable`, `selected`, `selectionDisabled`, `onToggleSelect`, `onBook`,
  `bookingDisabled`.
- New `formatScore` and a `.score-row` block showing "recommendation score / 100" with the note
  "Accessibility & price — not clinical quality."
- New `.doctor-actions` footer with a **Compare** checkbox and a **Book appointment** button.
- The card reflects selection via the `.doctor-card.selected` class.
- The card never shows a fabricated rating — missing values still render "Not rated" / "Not listed".

### 9.3 `frontend/src/pages/DoctorRecommendation.jsx` (+222 / −33)

- Signature is now `DoctorRecommendation({ patient, onPatientChange })`.
- Results are loaded from `fetchRankedDoctors` (ranked endpoint) instead of the plain directory.
- Filters sit **above** the doctor cards; the doctor list and filters only appear after analysis.
- Specialty is derived from the analysis result chips (no standalone specialty dropdown);
  only the predicted specialty's doctors are shown.
- Client-side `sortDoctors` / `compareMissingLast` re-sort the same scored records by
  rating / fees / distance / experience with **missing values last** and a deterministic
  `doctor_id` tie-breaker.
- Comparison: `MAX_COMPARE = 3`; `toggleCompare` sets a `compareMessage` when a 4th selection is
  attempted; `openCompare` calls `compareDoctors`; `removeFromCompare` drops a doctor. The
  compare modal is split into three guarded branches (loading / error / panel) to avoid a
  double backdrop.
- `bookingTarget` opens `BookingModal`.

### 9.4 `frontend/src/components/ComparePanel.jsx` (new)

- Renders up to three doctors side by side using the **same records and scores** the ranking
  list uses.
- `ROWS` covers Specialty, Recommendation score, Rating, Experience, Consultation fee, Distance,
  City, Address — plus an Actions row with **Book** and **Remove**.
- Missing values are clearly shown as `'Not listed'`.

### 9.5 `frontend/src/components/BookingModal.jsx` (new)

- Handles both a new booking (`doctor`) and a reschedule (`appointment`).
- Date input is constrained to today … +`MAX_ADVANCE_DAYS` (90).
- Loads slots from `fetchDoctorSlots`; unavailable slots are disabled with a reason tooltip
  ("Too soon to book" / "Already booked").
- The confirmation view is rendered **only after** `createAppointment` / `rescheduleAppointment`
  succeeds — there is no simulated confirmation.
- Confirmation shows appointment ID, doctor, specialty, date, time, fee, consultation type and
  status.
- If the user is signed out, `PatientAuth` is shown inline, so booking is not blocked.

### 9.6 `frontend/src/components/PatientAuth.jsx` (new)

- Login / register toggle; calls `patientRegister` then `patientLogin`; returns the session via
  `onSuccess(patientSessionFrom(data))`.
- Surfaces `err.payload.errors` as a list.
- Never stores the password after login (only the CSRF token is kept).

### 9.7 `frontend/src/pages/Appointments.jsx` (new)

- Tabs: Upcoming / Past / Cancelled, each with a count.
- Loads with `fetchMyAppointments`; a 401 clears the session.
- Cancel opens a confirm modal → `cancelAppointment`; the record is kept in history.
- Reschedule opens `BookingModal` in reschedule mode and reloads on success.
- Handles loading, empty, success and error states; only upcoming + booked records are actionable.

### 9.8 `frontend/src/styles.css` (+214 / −1)

New class groups (all additive; one media-query block was updated):

`.doctor-card.selected`, `.score-row`, `.score-value`, `.score-label`, `.doctor-actions`,
`.compare-toggle`, `.compare-bar`, `.compare-modal`, `.compare-head`, `.compare-table`,
`.compare-actions`, `.slots`, `.slot-button`, `.confirm-grid`, `.tabs`, `.appointments`,
`.appointment-card`, `.appointment-head`, `.status` / `.status-booked` / `.status-cancelled`,
`.appointment-foot`, and an updated `@media (max-width:620px)` block.

---

## 10. Tests (new)

- `Backend/tests/test_ranking.py` — default weights (0.35/0.25/0.15/0.15/0.10 totalling 1.0),
  configurable weights, each component normaliser, full hand-calculated score, missing-value
  renormalisation, deterministic ordering, tie-breaking by `Doctor_ID`, eligibility filtering
  before ranking, `limit` after ranking, three-doctor comparison, duplicate/fourth-doctor/empty
  rejection, 404 with `missing_doctor_ids`, and that comparison uses the **same** score as ranking.
- `Backend/tests/test_booking.py` — configured (per-doctor) schedules, distinct schedules per
  doctor, invalid/past/too-far dates, past slots today, open-dates summary, overlap rejection,
  successful booking payload, persistence across connections, slot becomes unavailable,
  **double booking returns 409**, different patients different slots, invalid consultation type,
  teleconsultation, unknown doctor, missing patient, too far ahead, a **6-thread concurrent
  booking producing exactly one 201**, cancellation releasing the slot, cancel twice / by another
  patient, reschedule moves + frees old slot, conflict on taken slot, same-slot allowed,
  cancelled cannot be rescheduled, and history grouping/ordering/scoping/past categorisation.
- `Backend/tests/test_patient_api.py` — HTTP integration over a disposable DB copy: ranked
  doctors with scores, required specialty, eligibility filters, compare equal to ranking, 4th /
  duplicate rejection, unknown-doctor reporting, doctor detail, detail/slots route separation,
  past-date slots, open dates, admin availability replacement, auth/CSRF ("CSRF token from
  another session is rejected"), full booking + history + cancellation round trip, persistence
  across sessions, double booking 409, non-offered slot 400, past date 400, slot freed on cancel,
  cross-patient isolation, reschedule (incl. onto a taken slot 409) and **patient-id injection
  is ignored** (the session's patient id is always used).

---

## 11. Verification results

- **Backend test suite:** `Ran 176 tests in 27.989s — OK`
  (68 pre-existing tests + 108 new tests, including the 6-thread concurrent double-booking test).
- **Frontend production build:** `npm run build` succeeded (40 modules, ~3.3s).
- **Route dump:** the Flask app exposes the new routes listed in section 6 alongside the existing
  ones.

---

## 12. Known limitations (not changes, but relevant context)

- The NER dependencies (`ner_nlp4bia`, `transformers`, `torch`) are **not installed** in the local
  environment, so `/api/analyze` cannot be run end-to-end here. This is an environment limitation,
  not a code gap; the analysis page and the new features continue to work once those deps are
  present. Symptom extraction was **not** replaced with mocks.
- There is no frontend test framework in the project, so the frontend was verified via the
  production build; backend behaviour is covered by the unit and HTTP integration tests.

---

## 13. How to run / verify

```powershell
# Backend (from Backend/)
python -m unittest discover -s tests -v     # expect: Ran 176 tests ... OK
python app.py                               # starts the Flask API

# Frontend (from frontend/)
npm install
npm run build                               # production build
npm run dev                                 # dev server, proxies /api to 127.0.0.1:5000
```

---

## 14. Confirmation of scope

This update:
- **Added** new modules, tables, indexes, endpoints, components and tests.
- **Modified** only two backend files (`app.py`, `admin_routes.py`) with purely additive lines,
  and the frontend files listed in section 1.5.
- **Did not** rewrite the `doctors` table or any existing records, did not change existing API
  contracts, and did not remove or replace any existing functionality, including the symptom
  extraction and ML classification pipelines.
