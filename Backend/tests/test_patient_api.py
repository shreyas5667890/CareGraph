"""HTTP integration tests for ranking, comparison, availability and the
patient appointment API.

A disposable copy of a small doctor database is used and every module-level
recommender is pointed at it, so the real development data is never touched.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from _support import create_doctor_db, temp_db_path  # noqa: F401  (adds Backend to sys.path)

TEST_DB = create_doctor_db(
    temp_db_path(),
    [
        {
            "Doctor_ID": doctor_id,
            "Doctor_Name": f"Dr {doctor_id}",
            "Specialization": specialization,
            "Age": 45,
            "Experience_Years": experience,
            "Description": "Synthetic test record.",
            "Address": "Test Clinic",
            "City": "Thane",
            "State": "Maharashtra",
            "Latitude": 19.2183 + offset,
            "Longitude": 72.9781 + offset,
            "Consultation_Fee_INR": fee,
            "Rating_Out_of_5": rating,
            "Data_Status": "Synthetic demo record",
        }
        for doctor_id, specialization, fee, rating, experience, offset in (
            ("C1", "Cardiologist", 400, 4.9, 25, 0.0),
            ("C2", "Cardiologist", 1500, 3.0, 2, 0.05),
            ("C3", "Cardiologist", 2000, 4.4, 30, 0.10),
            ("D1", "Dermatologist", 600, 4.2, 12, 0.02),
        )
    ],
)

os.environ["CAREGRAPH_DOCTOR_DB"] = TEST_DB
os.environ["CAREGRAPH_SECRET_KEY"] = "unit-test-secret-key"
os.environ["CAREGRAPH_ADMIN_USERNAME"] = "testadmin"
os.environ["CAREGRAPH_ADMIN_PASSWORD"] = "test-password-123"
os.environ["CAREGRAPH_DEBUG"] = "false"
os.environ.pop("CAREGRAPH_ADMIN_PASSWORD_HASH", None)

import unittest  # noqa: E402

import schema  # noqa: E402
import appointments  # noqa: E402
import app as app_module  # noqa: E402
import care_routes  # noqa: E402
import ranking  # noqa: E402
from doctor_recommender_sqlite import DoctorRecommender  # noqa: E402

REC = DoctorRecommender(TEST_DB)
appointments.default_recommender = REC
ranking.default_recommender = REC
care_routes.doctor_recommender = REC

# During a full test run ``app`` may already be imported by another module, so
# its startup schema initialisation does not run against this database. Create
# the additive patient/availability/appointment objects here explicitly.
schema.initialize(TEST_DB)

# Every path=None connection must use this disposable database.
schema.database_path = lambda: Path(TEST_DB)

WEEKLY = [{"day_of_week": d, "start_time": "09:00", "end_time": "12:00"} for d in range(7)]
ADMIN = {"username": "testadmin", "password": "test-password-123"}


def tearDownModule():
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        con = sqlite3.connect(TEST_DB)
        try:
            con.execute('DELETE FROM "appointments"')
            con.execute('DELETE FROM "patients"')
            con.commit()
        finally:
            con.close()
        payload, status = appointments.set_doctor_availability("C1", WEEKLY)
        self.assertEqual(status, 200, payload)
        self.tomorrow = (date.today() + timedelta(days=1)).isoformat()
        self.client = app_module.app.test_client()

    def register_and_login(self, username="alice", client=None):
        client = client or self.client
        created = client.post(
            "/api/patient/register",
            json={"username": username, "password": "patient-pass-123",
                  "full_name": "Test Patient"},
        )
        self.assertEqual(created.status_code, 201, created.get_json())
        logged_in = client.post(
            "/api/patient/login",
            json={"username": username, "password": "patient-pass-123"},
        )
        self.assertEqual(logged_in.status_code, 200, logged_in.get_json())
        return logged_in.get_json()["csrf_token"]

    def book(self, token, client=None, doctor_id="C1", day=None, start="09:00"):
        client = client or self.client
        return client.post(
            "/api/patient/appointments",
            json={"doctor_id": doctor_id, "date": day or self.tomorrow, "start_time": start},
            headers={"X-CSRF-Token": token},
        )


class RankingApiTests(ApiTestCase):
    def test_ranked_doctors_are_returned_with_scores(self):
        response = self.client.get(
            "/api/ranked-doctors",
            query_string={"specialty": "Cardiologist", "user_latitude": 19.2183,
                          "user_longitude": 72.9781},
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["success"])
        self.assertTrue(body["distance_available"])
        self.assertEqual([d["doctor_id"] for d in body["doctors"]], ["C1", "C3", "C2"])
        for record in body["doctors"]:
            self.assertIn("recommendation_score", record)
            self.assertIn("score_breakdown", record)
        self.assertEqual(body["weights_total"], 1.0)
        self.assertTrue(body["score_disclaimer"])

    def test_ranked_doctors_requires_a_specialty(self):
        response = self.client.get("/api/ranked-doctors")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()["success"])

    def test_ranked_doctors_applies_eligibility_filters(self):
        response = self.client.get(
            "/api/ranked-doctors",
            query_string={"specialty": "Cardiologist", "max_fee": 500},
        )
        body = response.get_json()
        self.assertEqual([d["doctor_id"] for d in body["doctors"]], ["C1"])

    def test_compare_returns_the_same_scores_as_ranking(self):
        ranked = self.client.get(
            "/api/ranked-doctors",
            query_string={"specialty": "Cardiologist", "user_latitude": 19.2183,
                          "user_longitude": 72.9781},
        ).get_json()
        by_id = {d["doctor_id"]: d["recommendation_score"] for d in ranked["doctors"]}

        response = self.client.post(
            "/api/compare-doctors",
            json={"doctor_ids": ["C1", "C2"], "specialty": "Cardiologist",
                  "user_latitude": 19.2183, "user_longitude": 72.9781},
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(len(body["doctors"]), 2)
        for record in body["doctors"]:
            self.assertEqual(record["recommendation_score"], by_id[record["doctor_id"]])

    def test_compare_rejects_a_fourth_doctor(self):
        response = self.client.post("/api/compare-doctors",
                                    json={"doctor_ids": ["C1", "C2", "C3", "D1"]})
        self.assertEqual(response.status_code, 400)

    def test_compare_rejects_duplicates(self):
        response = self.client.post("/api/compare-doctors", json={"doctor_ids": ["C1", "C1"]})
        self.assertEqual(response.status_code, 400)

    def test_compare_reports_unknown_doctors(self):
        response = self.client.post("/api/compare-doctors", json={"doctor_ids": ["C1", "NOPE"]})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["missing_doctor_ids"], ["NOPE"])

    def test_doctor_detail_endpoint(self):
        response = self.client.get("/api/doctors/C1")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["doctor"]["doctor_id"], "C1")
        self.assertEqual(body["doctor"]["name"], "Dr C1")

    def test_unknown_doctor_detail_returns_404(self):
        self.assertEqual(self.client.get("/api/doctors/NOPE").status_code, 404)

    def test_detail_and_slots_routes_do_not_collide(self):
        detail = self.client.get("/api/doctors/C1")
        slots = self.client.get("/api/doctors/C1/slots", query_string={"date": self.tomorrow})
        self.assertEqual(detail.status_code, 200)
        self.assertIn("doctor", detail.get_json())
        self.assertEqual(slots.status_code, 200)
        body = slots.get_json()
        self.assertEqual(body["doctor_id"], "C1")
        self.assertEqual([s["start"] for s in body["slots"]],
                         ["09:00", "09:30", "10:00", "10:30", "11:00", "11:30"])

    def test_slots_reject_a_past_date(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        response = self.client.get("/api/doctors/C1/slots", query_string={"date": yesterday})
        self.assertEqual(response.status_code, 400)

    def test_open_dates_endpoint(self):
        response = self.client.get("/api/doctors/C1/open-dates",
                                   query_string={"from": self.tomorrow, "days": 2})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.get_json()["dates"]), 2)

    def test_admin_can_replace_a_doctors_availability(self):
        login = self.client.post("/api/admin/login", json=ADMIN)
        self.assertEqual(login.status_code, 200, login.get_json())
        token = login.get_json()["csrf_token"]
        response = self.client.put(
            "/api/admin/doctors/C2/availability",
            json={"blocks": [{"day_of_week": d, "start_time": "16:00", "end_time": "17:00"}
                             for d in range(7)]},
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(response.status_code, 200, response.get_json())

        slots = self.client.get("/api/doctors/C2/slots",
                                query_string={"date": self.tomorrow}).get_json()
        self.assertEqual([s["start"] for s in slots["slots"]], ["16:00", "16:30"])
        self.assertEqual(slots["availability_source"], "admin_configured")

    def test_availability_edit_requires_admin_login(self):
        response = self.client.put("/api/admin/doctors/C2/availability",
                                   json={"blocks": []})
        self.assertEqual(response.status_code, 401)


class PatientAccountApiTests(ApiTestCase):
    def test_register_login_me_logout_round_trip(self):
        token = self.register_and_login("bob")
        me = self.client.get("/api/patient/me").get_json()
        self.assertTrue(me["authenticated"])
        self.assertEqual(me["patient"]["username"], "bob")
        self.assertTrue(me["patient"]["patient_id"].startswith("PAT-"))
        self.assertEqual(me["csrf_token"], token)

        logout = self.client.post("/api/patient/logout", headers={"X-CSRF-Token": token})
        self.assertEqual(logout.status_code, 200)
        self.assertFalse(self.client.get("/api/patient/me").get_json()["authenticated"])

    def test_duplicate_username_is_rejected(self):
        self.register_and_login("carol")
        second = self.client.post("/api/patient/register",
                                  json={"username": "carol", "password": "patient-pass-123"})
        self.assertEqual(second.status_code, 409)

    def test_weak_registration_is_rejected(self):
        response = self.client.post("/api/patient/register",
                                    json={"username": "x", "password": "short"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("errors", response.get_json())

    def test_login_with_wrong_password_returns_401(self):
        self.register_and_login("dave")
        response = self.client.post("/api/patient/login",
                                    json={"username": "dave", "password": "wrong-password"})
        self.assertEqual(response.status_code, 401)

    def test_me_without_a_session_reports_unauthenticated(self):
        body = self.client.get("/api/patient/me").get_json()
        self.assertFalse(body["authenticated"])
        self.assertIsNone(body["patient"])

    def test_appointments_require_authentication(self):
        self.assertEqual(self.client.get("/api/patient/appointments").status_code, 401)
        self.assertEqual(
            self.client.post("/api/patient/appointments",
                             json={"doctor_id": "C1", "date": self.tomorrow,
                                   "start_time": "09:00"}).status_code,
            401,
        )

    def test_mutations_require_a_csrf_token(self):
        self.register_and_login("erin")
        response = self.client.post(
            "/api/patient/appointments",
            json={"doctor_id": "C1", "date": self.tomorrow, "start_time": "09:00"},
        )
        self.assertEqual(response.status_code, 403)

    def test_csrf_token_from_another_session_is_rejected(self):
        self.register_and_login("frank")
        other = app_module.app.test_client()
        self.register_and_login("grace", client=other)
        other_token = other.get("/api/patient/me").get_json()["csrf_token"]
        response = self.client.post(
            "/api/patient/appointments",
            json={"doctor_id": "C1", "date": self.tomorrow, "start_time": "09:00"},
            headers={"X-CSRF-Token": other_token},
        )
        self.assertEqual(response.status_code, 403)


class BookingApiTests(ApiTestCase):
    def test_full_booking_history_and_cancellation_flow(self):
        token = self.register_and_login("heidi")

        response = self.book(token, start="09:00")
        self.assertEqual(response.status_code, 201, response.get_json())
        appointment = response.get_json()["appointment"]
        self.assertTrue(appointment["appointment_id"].startswith("APT-"))
        self.assertEqual(appointment["doctor_name"], "Dr C1")
        self.assertEqual(appointment["specialty"], "Cardiologist")
        self.assertEqual(appointment["date"], self.tomorrow)
        self.assertEqual(appointment["start_time"], "09:00")
        self.assertEqual(appointment["end_time"], "09:30")
        self.assertEqual(appointment["consultation_type"], "In-person")
        self.assertEqual(appointment["fee_inr"], 400)
        self.assertEqual(appointment["status"], "booked")

        history = self.client.get("/api/patient/appointments").get_json()
        self.assertEqual(history["counts"], {"upcoming": 1, "past": 0, "cancelled": 0, "total": 1})
        self.assertEqual(history["upcoming"][0]["appointment_id"], appointment["appointment_id"])

        cancel = self.client.post(
            f"/api/patient/appointments/{appointment['appointment_id']}/cancel",
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(cancel.status_code, 200, cancel.get_json())
        self.assertEqual(cancel.get_json()["appointment"]["status"], "cancelled")

        history = self.client.get("/api/patient/appointments").get_json()
        self.assertEqual(history["counts"], {"upcoming": 0, "past": 0, "cancelled": 1, "total": 1})

    def test_appointment_persists_across_requests_and_sessions(self):
        token = self.register_and_login("ivan")
        appointment_id = self.book(token).get_json()["appointment"]["appointment_id"]

        # A brand-new client with a fresh login must still see the record.
        other = app_module.app.test_client()
        self.client = other
        login = other.post("/api/patient/login",
                           json={"username": "ivan", "password": "patient-pass-123"})
        self.assertEqual(login.status_code, 200)
        history = other.get("/api/patient/appointments").get_json()
        self.assertEqual(history["upcoming"][0]["appointment_id"], appointment_id)

    def test_double_booking_returns_409(self):
        first_token = self.register_and_login("judy")
        second_client = app_module.app.test_client()
        second_token = self.register_and_login("karl", client=second_client)

        self.assertEqual(self.book(first_token, start="10:00").status_code, 201)
        conflict = self.book(second_token, client=second_client, start="10:00")
        self.assertEqual(conflict.status_code, 409)
        self.assertFalse(conflict.get_json()["success"])

    def test_booking_a_slot_that_is_not_offered_returns_400(self):
        token = self.register_and_login("laura")
        response = self.book(token, start="14:00")
        self.assertEqual(response.status_code, 400)

    def test_booking_an_unknown_doctor_returns_404(self):
        token = self.register_and_login("mallory")
        response = self.book(token, doctor_id="NOPE")
        self.assertEqual(response.status_code, 404)

    def test_booking_a_past_date_returns_400(self):
        token = self.register_and_login("nina")
        response = self.book(token, day=(date.today() - timedelta(days=1)).isoformat())
        self.assertEqual(response.status_code, 400)

    def test_slot_becomes_unavailable_after_booking(self):
        token = self.register_and_login("oscar")
        self.book(token, start="11:00")
        slots = self.client.get("/api/doctors/C1/slots",
                                query_string={"date": self.tomorrow}).get_json()
        slot = next(s for s in slots["slots"] if s["start"] == "11:00")
        self.assertFalse(slot["available"])
        self.assertEqual(slot["reason"], "booked")

    def test_cancelled_slot_becomes_available_again(self):
        token = self.register_and_login("peggy")
        appointment_id = self.book(token, start="11:30").get_json()["appointment"]["appointment_id"]
        self.client.post(f"/api/patient/appointments/{appointment_id}/cancel",
                         headers={"X-CSRF-Token": token})
        slots = self.client.get("/api/doctors/C1/slots",
                                query_string={"date": self.tomorrow}).get_json()
        slot = next(s for s in slots["slots"] if s["start"] == "11:30")
        self.assertTrue(slot["available"])

    def test_one_patient_cannot_see_another_patients_appointment(self):
        first_token = self.register_and_login("quinn")
        appointment_id = self.book(first_token).get_json()["appointment"]["appointment_id"]

        other = app_module.app.test_client()
        other_token = self.register_and_login("rupert", client=other)
        self.assertEqual(
            other.get(f"/api/patient/appointments/{appointment_id}").status_code, 404
        )
        self.assertEqual(
            other.post(f"/api/patient/appointments/{appointment_id}/cancel",
                       headers={"X-CSRF-Token": other_token}).status_code,
            404,
        )

    def test_rescheduling_moves_the_appointment(self):
        token = self.register_and_login("sybil")
        appointment_id = self.book(token, start="09:00").get_json()["appointment"]["appointment_id"]
        response = self.client.post(
            f"/api/patient/appointments/{appointment_id}/reschedule",
            json={"date": self.tomorrow, "start_time": "10:00"},
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["appointment"]["start_time"], "10:00")

        slots = self.client.get("/api/doctors/C1/slots",
                                query_string={"date": self.tomorrow}).get_json()
        by_start = {s["start"]: s for s in slots["slots"]}
        self.assertTrue(by_start["09:00"]["available"])
        self.assertFalse(by_start["10:00"]["available"])

    def test_rescheduling_onto_a_taken_slot_returns_409(self):
        first_token = self.register_and_login("tina")
        second_client = app_module.app.test_client()
        second_token = self.register_and_login("umar", client=second_client)
        self.book(first_token, start="09:30")
        appointment_id = self.book(second_token, client=second_client,
                                   start="10:00").get_json()["appointment"]["appointment_id"]
        response = second_client.post(
            f"/api/patient/appointments/{appointment_id}/reschedule",
            json={"date": self.tomorrow, "start_time": "09:30"},
            headers={"X-CSRF-Token": second_token},
        )
        self.assertEqual(response.status_code, 409)

    def test_patient_id_cannot_be_injected_by_the_client(self):
        token = self.register_and_login("vera")
        me = self.client.get("/api/patient/me").get_json()
        response = self.client.post(
            "/api/patient/appointments",
            json={"doctor_id": "C1", "date": self.tomorrow, "start_time": "09:00",
                  "patient_id": "PAT-HACKED"},
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["appointment"]["patient_id"],
                         me["patient"]["patient_id"])

    def test_history_does_not_leak_other_patients_records(self):
        first_token = self.register_and_login("wanda")
        self.book(first_token, start="09:00")
        other = app_module.app.test_client()
        self.register_and_login("xena", client=other)
        history = other.get("/api/patient/appointments").get_json()
        self.assertEqual(history["counts"]["total"], 0)


if __name__ == "__main__":
    unittest.main()
