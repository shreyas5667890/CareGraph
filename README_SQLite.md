# CareGraph Doctor Recommendation — SQLite edition

This is the SQLite-backed doctor directory used by the CareGraph API. The full
setup, API reference, admin dashboard, and frontend instructions now live in
[README.md](README.md); this file only covers the database specifics.

## Contents
- `Data/caregraph_doctors.db`: SQLite database populated from the synthetic Mumbai–Thane directory (150 records, 15 specialties).
- `Backend/doctor_recommender_sqlite.py`: database-backed search, filtering, sorting and admin CRUD.
- `Backend/import_doctors_to_sqlite.py`: rebuild the database after updating the Excel source.
- `Backend/app.py`: Flask API. It already imports the SQLite module (`from doctor_recommender_sqlite import doctor_recommender`), so no manual edit is needed.

## Use the SQLite recommender directly

From the project root:
```powershell
python -c "from Backend.doctor_recommender_sqlite import doctor_recommender; print(doctor_recommender.search('Neurologist', city='Thane', limit=5))"
```
If `Backend` is the current working directory, use:
```powershell
python -c "from doctor_recommender_sqlite import doctor_recommender; print(doctor_recommender.search('Neurologist', city='Thane', limit=5))"
```

`search()` also accepts `sort_by`, `user_latitude`, `user_longitude`,
`max_distance_km`, `max_fee`, `min_rating`, `page`, and `page_size` — see the
`GET /api/doctors` table in [README.md](README.md) for the exact semantics.

## Update data later

Edit the source workbook, then run:
```powershell
python Backend/import_doctors_to_sqlite.py
```
The SQLite database is rebuilt from the `Doctors` worksheet.

## Example API calls
```http
GET /api/doctors?specialty=Neurologist
GET /api/doctors?specialty=Cardiologist&city=Mumbai&sort=fees&limit=5
GET /api/doctors?specialty=Neurologist&user_latitude=19.2183&user_longitude=72.9781&max_distance_km=25&sort=distance
```

## Safety and data provenance
The included doctors are fictional demo records; addresses are illustrative and
coordinates approximate. Do not use them as real referrals. Before real
patient-facing use, replace them with verified providers and obtain appropriate
consent for any personal or clinical data handled by the application.
