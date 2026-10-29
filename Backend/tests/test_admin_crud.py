"""Admin authentication and doctor CRUD tests.

These run against a disposable copy of the SQLite database so the real
development data is never modified.
"""
from __future__ import annotations

import os
import sqlite3
import unittest

from _support import BACKEND_DIR, copy_real_db, temp_db_path  # noqa: F401

TEST_DB = copy_real_db(temp_db_path())

# Configure the app before importing it so the test database and a known
# development admin account are used.
os.environ["CAREGRAPH_DOCTOR_DB"] = TEST_DB
os.environ["CAREGRAPH_ADMIN_USERNAME"] = "testadmin"
os.environ["CAREGRAPH_ADMIN_PASSWORD"] = "test-password-123"
os.environ["CAREGRAPH_SECRET_KEY"] = "unit-test-secret-key"
os.environ["CAREGRAPH_DEBUG"] = "false"
os.environ.pop("CAREGRAPH_ADMIN_PASSWORD_HASH", None)

import admin_routes  # noqa: E402
import app as app_module  # noqa: E402
from doctor_recommender_sqlite import DoctorRecommender  # noqa: E402

# Point the running app at the disposable database.
admin_routes.doctor_recommender = DoctorRecommender(TEST_DB)
app_module.doctor_recommender = admin_routes.doctor_recommender

ADMIN = {"username": "testadmin", "password": "test-password-123"}
TABLE = "doctors"


def new_doctor(doctor_id: str, **overrides) -> dict:
    payload = {
        "Doctor_ID": doctor_id,
        "Doctor_Name": "Dr Test Record",
        "Specialization": "Neurologist",
        "Age": 44,
        "Experience_Years": 12,
        "Description": "Created by the automated test suite.",
        "Address": "Test Clinic, Thane",
        "City": "Thane",
        "State": "Maharashtra",
        "Latitude": 19.2183,
        "Longitude": 72.9781,
        "Consultation_Fee_INR": 750,
        "Rating_Out_of_5": 4.4,
        "Data_Status": "Synthetic demo record",
    }
    payload.update(overrides)
    return payload


def purge(doctor_id: str) -> None:
    con = sqlite3.connect(TEST_DB)
    try:
        con.execute(f'DELETE FROM "{TABLE}" WHERE Doctor_ID = ?', (doctor_id,))
        con.commit()
    finally:
        con.close()


def exists_in_db(doctor_id: str) -> bool:
    return DoctorRecommender(TEST_DB).get_doctor(doctor_id) is not None


def tearDownModule():
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


class AdminApiTestCase(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()

    def login(self, credentials: dict | None = None) -> str:
        response = self.client.post("/api/admin/login", json=credentials or ADMIN)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()["csrf_token"]

    @staticmethod
    def csrf(token: str) -> dict:
        return {"X-CSRF-Token": token}


class AdminAuthenticationTests(AdminApiTestCase):
    def test_login_succeeds_with_valid_credentials(self):
        response = self.client.post("/api/admin/login", json=ADMIN)
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["success"])
        self.assertTrue(body["authenticated"])
        self.assertTrue(body["csrf_token"])

    def test_invalid_credentials_are_rejected(self):
        response = self.client.post(
            "/api/admin/login", json={"username": "testadmin", "password": "wrong"}
        )
        self.assertEqual(response.status_code, 401)

    def test_unknown_username_is_rejected(self):
        response = self.client.post(
            "/api/admin/login", json={"username": "nobody", "password": "test-password-123"}
        )
        self.assertEqual(response.status_code, 401)

    def test_missing_fields_are_rejected(self):
        self.assertEqual(self.client.post("/api/admin/login", json={}).status_code, 400)

    def test_responses_never_contain_passwords(self):
        body = self.client.post("/api/admin/login", json=ADMIN).get_json()
        self.assertNotIn("password", body)
        me = self.client.get("/api/admin/me").get_json()
        self.assertNotIn("password", me)
        self.assertNotIn("password_hash", me)

    def test_me_reports_session_and_logout_clears_it(self):
        self.assertFalse(self.client.get("/api/admin/me").get_json()["authenticated"])
        self.login()
        self.assertTrue(self.client.get("/api/admin/me").get_json()["authenticated"])

        token = self.login()
        response = self.client.post("/api/admin/logout", headers=self.csrf(token))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.client.get("/api/admin/me").get_json()["authenticated"])

    def test_unauthenticated_requests_are_rejected(self):
        payload = new_doctor("TEST-AUTH")
        cases = [
            ("get", "/api/admin/doctors", None),
            ("get", "/api/admin/doctors/CG-0001", None),
            ("post", "/api/admin/doctors", payload),
            ("put", "/api/admin/doctors/CG-0001", {"Doctor_Name": "Hacked"}),
            ("delete", "/api/admin/doctors/CG-0001", None),
        ]
        for method, url, body in cases:
            response = getattr(self.client, method)(url, json=body)
            self.assertEqual(response.status_code, 401, f"{method.upper()} {url}")
        self.assertTrue(exists_in_db("CG-0001"))

    def test_csrf_token_is_required_for_mutations(self):
        self.login()
        response = self.client.post("/api/admin/doctors", json=new_doctor("TEST-CSRF"))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(exists_in_db("TEST-CSRF"))

    def test_wrong_csrf_token_is_rejected(self):
        self.login()
        response = self.client.post(
            "/api/admin/doctors",
            json=new_doctor("TEST-CSRF2"),
            headers={"X-CSRF-Token": "not-the-token"},
        )
        self.assertEqual(response.status_code, 403)


class AdminCrudTests(AdminApiTestCase):
    def test_full_create_read_update_delete_flow(self):
        token = self.login()
        doctor_id = "TEST-CRUD"

        try:
            created = self.client.post(
                "/api/admin/doctors", json=new_doctor(doctor_id), headers=self.csrf(token)
            )
            self.assertEqual(created.status_code, 201, created.get_data(as_text=True))
            self.assertEqual(created.get_json()["doctor"]["doctor_id"], doctor_id)
            # Persisted, not just echoed back.
            self.assertTrue(exists_in_db(doctor_id))

            fetched = self.client.get(f"/api/admin/doctors/{doctor_id}")
            self.assertEqual(fetched.status_code, 200)
            self.assertEqual(fetched.get_json()["doctor"]["name"], "Dr Test Record")

            updated = self.client.put(
                f"/api/admin/doctors/{doctor_id}",
                json={"Consultation_Fee_INR": 1234, "Doctor_Name": "Dr Updated"},
                headers=self.csrf(token),
            )
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(updated.get_json()["doctor"]["consultation_fee_inr"], 1234)
            # The new value survives a fresh database connection.
            self.assertEqual(DoctorRecommender(TEST_DB).get_doctor(doctor_id)["name"], "Dr Updated")

            deleted = self.client.delete(
                f"/api/admin/doctors/{doctor_id}", headers=self.csrf(token)
            )
            self.assertEqual(deleted.status_code, 200)
            self.assertFalse(exists_in_db(doctor_id))
        finally:
            purge(doctor_id)

    def test_partial_update_preserves_other_fields(self):
        token = self.login()
        doctor_id = "TEST-PARTIAL"
        try:
            self.client.post(
                "/api/admin/doctors", json=new_doctor(doctor_id), headers=self.csrf(token)
            )
            self.client.put(
                f"/api/admin/doctors/{doctor_id}",
                json={"Rating_Out_of_5": 3.9},
                headers=self.csrf(token),
            )
            stored = DoctorRecommender(TEST_DB).get_doctor(doctor_id)
            self.assertEqual(stored["rating_out_of_5"], 3.9)
            self.assertEqual(stored["name"], "Dr Test Record")
            self.assertEqual(stored["city"], "Thane")
            self.assertEqual(stored["consultation_fee_inr"], 750)
        finally:
            purge(doctor_id)

    def test_duplicate_doctor_id_is_rejected(self):
        token = self.login()
        doctor_id = "TEST-DUPLICATE"
        try:
            first = self.client.post(
                "/api/admin/doctors", json=new_doctor(doctor_id), headers=self.csrf(token)
            )
            self.assertEqual(first.status_code, 201)
            duplicate = self.client.post(
                "/api/admin/doctors", json=new_doctor(doctor_id), headers=self.csrf(token)
            )
            self.assertEqual(duplicate.status_code, 409)
        finally:
            purge(doctor_id)

    def test_invalid_values_are_rejected(self):
        token = self.login()
        invalid_payloads = [
            new_doctor("TEST-BAD", Latitude=200, Longitude=72),      # latitude range
            new_doctor("TEST-BAD", Latitude=19, Longitude=200),      # longitude range
            new_doctor("TEST-BAD", Latitude=19, Longitude=None),     # coordinates must pair
            new_doctor("TEST-BAD", Consultation_Fee_INR=-50),        # fee must be >= 0
            new_doctor("TEST-BAD", Rating_Out_of_5=9),               # rating 0..5
            new_doctor("TEST-BAD", Experience_Years=-3),             # experience >= 0
            new_doctor("TEST-BAD", Age="forty"),                     # age must be numeric
            new_doctor("TEST-BAD", Doctor_Name="   "),               # name required
            new_doctor("TEST-BAD", Specialization=""),               # specialty required
        ]
        for payload in invalid_payloads:
            response = self.client.post(
                "/api/admin/doctors", json=payload, headers=self.csrf(token)
            )
            self.assertEqual(response.status_code, 400, payload)
            self.assertIn("errors", response.get_json())
        self.assertFalse(exists_in_db("TEST-BAD"))

    def test_create_without_doctor_id_is_rejected(self):
        token = self.login()
        payload = new_doctor("X")
        payload.pop("Doctor_ID")
        response = self.client.post(
            "/api/admin/doctors", json=payload, headers=self.csrf(token)
        )
        self.assertEqual(response.status_code, 400)

    def test_delete_missing_doctor_returns_404(self):
        token = self.login()
        response = self.client.delete(
            "/api/admin/doctors/DOES-NOT-EXIST", headers=self.csrf(token)
        )
        self.assertEqual(response.status_code, 404)

    def test_get_missing_doctor_returns_404(self):
        self.login()
        response = self.client.get("/api/admin/doctors/DOES-NOT-EXIST")
        self.assertEqual(response.status_code, 404)

    def test_update_missing_doctor_returns_404(self):
        token = self.login()
        response = self.client.put(
            "/api/admin/doctors/DOES-NOT-EXIST",
            json={"Doctor_Name": "Nobody"},
            headers=self.csrf(token),
        )
        self.assertEqual(response.status_code, 404)

    def test_admin_list_supports_search_and_filters(self):
        self.login()
        listing = self.client.get("/api/admin/doctors?page=1&page_size=5")
        self.assertEqual(listing.status_code, 200)
        body = listing.get_json()
        self.assertEqual(len(body["doctors"]), 5)
        self.assertGreater(body["total"], 100)
        self.assertIn("Neurologist", body["specialties"])

        filtered = self.client.get("/api/admin/doctors?specialization=Cardiologist&page_size=100")
        self.assertTrue(all(
            d["specialization"] == "Cardiologist" for d in filtered.get_json()["doctors"]
        ))

        searched = self.client.get("/api/admin/doctors?q=CG-0114")
        names = [d["doctor_id"] for d in searched.get_json()["doctors"]]
        self.assertEqual(names, ["CG-0114"])


class PublicDoctorApiTests(AdminApiTestCase):
    def test_doctors_endpoint_returns_error_without_specialty(self):
        response = self.client.get("/api/doctors")
        self.assertEqual(response.status_code, 400)

    def test_doctors_endpoint_filters_by_specialty(self):
        response = self.client.get("/api/doctors?specialty=Neurologist&limit=50")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(all(d["specialization"] == "Neurologist" for d in body["doctors"]))

    def test_doctors_endpoint_applies_distance_and_sorting(self):
        response = self.client.get(
            "/api/doctors?specialty=Neurologist&user_latitude=19.2183"
            "&user_longitude=72.9781&sort=distance&limit=50"
        )
        self.assertEqual(response.status_code, 200)
        distances = [d["distance_km"] for d in response.get_json()["doctors"]]
        self.assertEqual(distances, sorted(distances))

    def test_doctors_endpoint_rejects_invalid_coordinates(self):
        response = self.client.get(
            "/api/doctors?specialty=Neurologist&user_latitude=999&user_longitude=72"
        )
        self.assertEqual(response.status_code, 400)

    def test_specialties_endpoint(self):
        response = self.client.get("/api/specialties")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Neurologist", response.get_json()["specialties"])


if __name__ == "__main__":
    unittest.main()
