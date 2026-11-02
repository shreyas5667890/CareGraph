"""Service-level tests for availability and persistent appointment booking.

Determinism: every test doctor's schedule is written explicitly with
``set_doctor_availability`` instead of relying on the generated default, so the
expected slots are exact. All calls pass an explicit database path and
recommender, leaving no dependence on environment variables.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import unittest
from datetime import date, datetime, timedelta

from _support import create_doctor_db, temp_db_path  # noqa: F401  (adds Backend to sys.path)

import schema
from appointments import (
    STATUS_BOOKED,
    STATUS_CANCELLED,
    cancel_appointment,
    create_appointment,
    get_day_availability,
    get_patient_appointment,
    list_open_dates,
    list_patient_appointments,
    reschedule_appointment,
    set_doctor_availability,
)
from doctor_recommender_sqlite import DoctorRecommender

ROWS = [
    {
        "Doctor_ID": "BK1",
        "Doctor_Name": "Dr Booker",
        "Specialization": "Cardiologist",
        "Age": 50,
        "Experience_Years": 20,
        "Description": "Synthetic test record.",
        "Address": "Test Clinic",
        "City": "Thane",
        "State": "Maharashtra",
        "Latitude": 19.2183,
        "Longitude": 72.9781,
        "Consultation_Fee_INR": 750,
        "Rating_Out_of_5": 4.5,
        "Data_Status": "Synthetic demo record",
    },
    {
        "Doctor_ID": "BK2",
        "Doctor_Name": "Dr Second",
        "Specialization": "Cardiologist",
        "Age": 40,
        "Experience_Years": 10,
        "Description": "Synthetic test record.",
        "Address": "Test Clinic 2",
        "City": "Thane",
        "State": "Maharashtra",
        "Latitude": 19.22,
        "Longitude": 72.98,
        "Consultation_Fee_INR": 900,
        "Rating_Out_of_5": 4.0,
        "Data_Status": "Synthetic demo record",
    },
]

TEST_DB = create_doctor_db(temp_db_path(), ROWS)
schema.initialize(TEST_DB)
REC = DoctorRecommender(TEST_DB)

# A full week of 09:00-12:00 in 30-minute slots (6 slots/day) for BK1, and a
# single narrow block for BK2 so per-doctor schedules are demonstrably distinct.
WEEKLY = [{"day_of_week": d, "start_time": "09:00", "end_time": "12:00"} for d in range(7)]
BK2_BLOCKS = [{"day_of_week": d, "start_time": "15:00", "end_time": "16:00"} for d in range(7)]


def add_patient(patient_id: str, username: str | None = None) -> None:
    con = sqlite3.connect(TEST_DB)
    try:
        con.execute(
            'INSERT OR IGNORE INTO "patients" '
            '("Patient_ID", "Username", "Password_Hash", "Full_Name", "Email", "Created_At") '
            "VALUES (?, ?, ?, ?, ?, ?)",
            (patient_id, username or patient_id.lower(), "not-a-real-hash",
             "Test Patient", "", "2026-01-01T00:00:00+00:00"),
        )
        con.commit()
    finally:
        con.close()


def clear_appointments() -> None:
    con = sqlite3.connect(TEST_DB)
    try:
        con.execute('DELETE FROM "appointments"')
        con.commit()
    finally:
        con.close()


def tearDownModule():
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


class AppointmentTestCase(unittest.TestCase):
    def setUp(self):
        add_patient("P1")
        add_patient("P2")
        clear_appointments()
        payload, status = set_doctor_availability("BK1", WEEKLY, recommender=REC, path=TEST_DB)
        self.assertEqual(status, 200, payload)
        payload, status = set_doctor_availability("BK2", BK2_BLOCKS, recommender=REC, path=TEST_DB)
        self.assertEqual(status, 200, payload)
        self.tomorrow = (date.today() + timedelta(days=1)).isoformat()

    def book(self, doctor_id="BK1", day=None, start="09:00", patient="P1", ctype=None):
        return create_appointment(
            patient, doctor_id, day or self.tomorrow, start, ctype,
            recommender=REC, path=TEST_DB,
        )


class AvailabilityTests(AppointmentTestCase):
    def test_slots_come_from_the_configured_schedule(self):
        payload, status = get_day_availability("BK1", self.tomorrow, recommender=REC, path=TEST_DB)
        self.assertEqual(status, 200)
        starts = [s["start"] for s in payload["slots"]]
        self.assertEqual(starts, ["09:00", "09:30", "10:00", "10:30", "11:00", "11:30"])
        self.assertEqual(payload["availability_source"], "admin_configured")
        self.assertEqual(payload["available_count"], 6)

    def test_schedules_differ_per_doctor(self):
        first, _ = get_day_availability("BK1", self.tomorrow, recommender=REC, path=TEST_DB)
        second, _ = get_day_availability("BK2", self.tomorrow, recommender=REC, path=TEST_DB)
        self.assertNotEqual([s["start"] for s in first["slots"]],
                            [s["start"] for s in second["slots"]])

    def test_unknown_doctor_returns_404(self):
        _, status = get_day_availability("NOPE", self.tomorrow, recommender=REC, path=TEST_DB)
        self.assertEqual(status, 404)

    def test_invalid_date_returns_400(self):
        _, status = get_day_availability("BK1", "not-a-date", recommender=REC, path=TEST_DB)
        self.assertEqual(status, 400)

    def test_past_date_returns_400(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        _, status = get_day_availability("BK1", yesterday, recommender=REC, path=TEST_DB)
        self.assertEqual(status, 400)

    def test_too_far_ahead_returns_400(self):
        far = (date.today() + timedelta(days=365)).isoformat()
        _, status = get_day_availability("BK1", far, recommender=REC, path=TEST_DB)
        self.assertEqual(status, 400)

    def test_all_slots_today_are_available_for_the_demo(self):
        payload, status = get_day_availability("BK1", date.today().isoformat(),
                                               recommender=REC, path=TEST_DB)
        self.assertEqual(status, 200)
        self.assertTrue(payload["slots"])
        for slot in payload["slots"]:
            self.assertTrue(slot["available"], slot)
            self.assertIsNone(slot["reason"], slot)

    def test_open_dates_summarises_bookability(self):
        payload, status = list_open_dates("BK1", start=self.tomorrow, days=3,
                                          recommender=REC, path=TEST_DB)
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["dates"]), 3)
        self.assertTrue(all(d["bookable"] for d in payload["dates"]))
        self.assertTrue(all(d["available_count"] == 6 for d in payload["dates"]))

    def test_availability_blocks_reject_overlaps(self):
        blocks = [
            {"day_of_week": 0, "start_time": "09:00", "end_time": "12:00"},
            {"day_of_week": 0, "start_time": "09:00", "end_time": "10:00"},
        ]
        payload, status = set_doctor_availability("BK1", blocks, recommender=REC, path=TEST_DB)
        self.assertEqual(status, 409, payload)

    def test_availability_blocks_are_validated(self):
        bad = [{"day_of_week": 9, "start_time": "09:00", "end_time": "10:00"}]
        payload, status = set_doctor_availability("BK1", bad, recommender=REC, path=TEST_DB)
        self.assertEqual(status, 400)
        self.assertIn("errors", payload)


class CreateAppointmentTests(AppointmentTestCase):
    def test_successful_booking_returns_full_confirmation(self):
        payload, status = self.book()
        self.assertEqual(status, 201, payload)
        appointment = payload["appointment"]
        self.assertTrue(appointment["appointment_id"].startswith("APT-"))
        self.assertEqual(appointment["patient_id"], "P1")
        self.assertEqual(appointment["doctor_id"], "BK1")
        self.assertEqual(appointment["doctor_name"], "Dr Booker")
        self.assertEqual(appointment["specialty"], "Cardiologist")
        self.assertEqual(appointment["date"], self.tomorrow)
        self.assertEqual(appointment["start_time"], "09:00")
        self.assertEqual(appointment["end_time"], "09:30")
        self.assertEqual(appointment["duration_minutes"], 30)
        self.assertEqual(appointment["status"], STATUS_BOOKED)
        self.assertEqual(appointment["consultation_type"], "In-person")
        self.assertEqual(appointment["fee_inr"], 750)
        self.assertTrue(appointment["created_at"])
        self.assertIsNone(appointment["cancelled_at"])

    def test_booking_persists_across_connections(self):
        payload, _ = self.book()
        appointment_id = payload["appointment"]["appointment_id"]
        con = sqlite3.connect(TEST_DB)
        try:
            row = con.execute('SELECT * FROM "appointments" WHERE "Appointment_ID" = ?',
                              (appointment_id,)).fetchone()
        finally:
            con.close()
        self.assertIsNotNone(row)

    def test_booked_slot_is_no_longer_available(self):
        self.book(start="09:00")
        payload, _ = get_day_availability("BK1", self.tomorrow, recommender=REC, path=TEST_DB)
        slot = next(s for s in payload["slots"] if s["start"] == "09:00")
        self.assertFalse(slot["available"])
        self.assertEqual(slot["reason"], "booked")
        self.assertEqual(payload["available_count"], 5)

    def test_double_booking_the_same_slot_is_rejected(self):
        first, status = self.book(start="10:00")
        self.assertEqual(status, 201)
        second, status = self.book(start="10:00", patient="P2")
        self.assertEqual(status, 409, second)
        payload = list_patient_appointments("P2", recommender=REC, path=TEST_DB)
        self.assertEqual(payload["counts"]["total"], 0)

    def test_different_patients_can_book_different_slots(self):
        _, first = self.book(start="09:00", patient="P1")
        _, second = self.book(start="09:30", patient="P2")
        self.assertEqual(first, 201)
        self.assertEqual(second, 201)

    def test_time_outside_availability_is_rejected(self):
        payload, status = self.book(start="13:00")
        self.assertEqual(status, 400, payload)

    def test_past_date_is_rejected(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        _, status = self.book(day=yesterday)
        self.assertEqual(status, 400)

    def test_any_remaining_slot_today_can_be_booked_for_the_demo(self):
        today = date.today().isoformat()
        # Open the whole day, then confirm an early slot is still bookable today
        # regardless of the current clock time (demo behaviour).
        payload, status = set_doctor_availability(
            "BK1", [{"day_of_week": date.today().weekday(), "start_time": "00:00",
                     "end_time": "23:00"}], recommender=REC, path=TEST_DB)
        self.assertEqual(status, 200)
        payload, status = self.book(day=today, start="00:00")
        self.assertEqual(status, 201, payload)

    def test_invalid_date_and_time_are_rejected(self):
        _, status = create_appointment("P1", "BK1", "31-12-2026", "09:00",
                                       recommender=REC, path=TEST_DB)
        self.assertEqual(status, 400)
        _, status = create_appointment("P1", "BK1", self.tomorrow, "9am",
                                       recommender=REC, path=TEST_DB)
        self.assertEqual(status, 400)

    def test_invalid_consultation_type_is_rejected(self):
        _, status = self.book(ctype="Carrier pigeon")
        self.assertEqual(status, 400)

    def test_teleconsultation_is_accepted(self):
        payload, status = self.book(ctype="Teleconsultation")
        self.assertEqual(status, 201, payload)
        self.assertEqual(payload["appointment"]["consultation_type"], "Teleconsultation")

    def test_unknown_doctor_is_rejected(self):
        _, status = create_appointment("P1", "NOPE", self.tomorrow, "09:00",
                                       recommender=REC, path=TEST_DB)
        self.assertEqual(status, 404)

    def test_missing_patient_is_rejected(self):
        _, status = create_appointment("", "BK1", self.tomorrow, "09:00",
                                       recommender=REC, path=TEST_DB)
        self.assertEqual(status, 401)

    def test_too_far_ahead_is_rejected(self):
        far = (date.today() + timedelta(days=200)).isoformat()
        _, status = self.book(day=far)
        self.assertEqual(status, 400)

    def test_concurrent_bookings_of_one_slot_produce_exactly_one_winner(self):
        results: list[tuple[int, dict]] = []
        lock = threading.Lock()

        def attempt(patient: str):
            payload, status = create_appointment(
                patient, "BK1", self.tomorrow, "11:00", None,
                recommender=REC, path=TEST_DB,
            )
            with lock:
                results.append((status, payload))

        threads = [threading.Thread(target=attempt, args=(f"P{i}",)) for i in range(2, 8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(results), 6)
        successes = [r for r in results if r[0] == 201]
        conflicts = [r for r in results if r[0] == 409]
        self.assertEqual(len(successes), 1, results)
        self.assertEqual(len(conflicts), 5, results)

        payload, _ = get_day_availability("BK1", self.tomorrow, recommender=REC, path=TEST_DB)
        slot = next(s for s in payload["slots"] if s["start"] == "11:00")
        self.assertFalse(slot["available"])


class CancelAndRescheduleTests(AppointmentTestCase):
    def test_cancellation_updates_status_and_releases_the_slot(self):
        payload, _ = self.book(start="09:00")
        appointment_id = payload["appointment"]["appointment_id"]

        payload, status = cancel_appointment("P1", appointment_id, recommender=REC, path=TEST_DB)
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["appointment"]["status"], STATUS_CANCELLED)
        self.assertTrue(payload["appointment"]["cancelled_at"])

        availability, _ = get_day_availability("BK1", self.tomorrow, recommender=REC, path=TEST_DB)
        slot = next(s for s in availability["slots"] if s["start"] == "09:00")
        self.assertTrue(slot["available"])

        rebooked, status = self.book(start="09:00", patient="P2")
        self.assertEqual(status, 201, rebooked)

    def test_cancelling_twice_is_rejected(self):
        payload, _ = self.book()
        appointment_id = payload["appointment"]["appointment_id"]
        cancel_appointment("P1", appointment_id, recommender=REC, path=TEST_DB)
        _, status = cancel_appointment("P1", appointment_id, recommender=REC, path=TEST_DB)
        self.assertEqual(status, 409)

    def test_another_patient_cannot_cancel(self):
        payload, _ = self.book()
        appointment_id = payload["appointment"]["appointment_id"]
        _, status = cancel_appointment("P2", appointment_id, recommender=REC, path=TEST_DB)
        self.assertEqual(status, 404)

    def test_another_patient_cannot_read(self):
        payload, _ = self.book()
        appointment_id = payload["appointment"]["appointment_id"]
        _, status = get_patient_appointment("P2", appointment_id, recommender=REC, path=TEST_DB)
        self.assertEqual(status, 404)
        body, status = get_patient_appointment("P1", appointment_id, recommender=REC, path=TEST_DB)
        self.assertEqual(status, 200)
        self.assertEqual(body["appointment"]["appointment_id"], appointment_id)

    def test_rescheduling_moves_the_appointment_and_frees_the_old_slot(self):
        payload, _ = self.book(start="09:00")
        appointment_id = payload["appointment"]["appointment_id"]

        payload, status = reschedule_appointment(
            "P1", appointment_id, self.tomorrow, "10:30", recommender=REC, path=TEST_DB
        )
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["appointment"]["start_time"], "10:30")
        self.assertEqual(payload["appointment"]["end_time"], "11:00")
        self.assertTrue(payload["appointment"]["rescheduled_at"])

        availability, _ = get_day_availability("BK1", self.tomorrow, recommender=REC, path=TEST_DB)
        by_start = {s["start"]: s for s in availability["slots"]}
        self.assertTrue(by_start["09:00"]["available"])
        self.assertFalse(by_start["10:30"]["available"])

    def test_rescheduling_onto_a_taken_slot_conflicts(self):
        self.book(start="09:00", patient="P2")
        payload, _ = self.book(start="09:30", patient="P1")
        appointment_id = payload["appointment"]["appointment_id"]
        _, status = reschedule_appointment(
            "P1", appointment_id, self.tomorrow, "09:00", recommender=REC, path=TEST_DB
        )
        self.assertEqual(status, 409)

    def test_rescheduling_onto_the_same_slot_is_allowed(self):
        payload, _ = self.book(start="09:00")
        appointment_id = payload["appointment"]["appointment_id"]
        _, status = reschedule_appointment(
            "P1", appointment_id, self.tomorrow, "09:00", recommender=REC, path=TEST_DB
        )
        self.assertEqual(status, 200)

    def test_cancelled_appointment_cannot_be_rescheduled(self):
        payload, _ = self.book()
        appointment_id = payload["appointment"]["appointment_id"]
        cancel_appointment("P1", appointment_id, recommender=REC, path=TEST_DB)
        _, status = reschedule_appointment(
            "P1", appointment_id, self.tomorrow, "11:00", recommender=REC, path=TEST_DB
        )
        self.assertEqual(status, 409)

    def test_cancelled_slot_is_released_for_rescheduling(self):
        first, _ = self.book(start="09:00", patient="P2")
        # P1 books a different slot then moves into the slot P2 vacates.
        payload, _ = self.book(start="09:30", patient="P1")
        appointment_id = payload["appointment"]["appointment_id"]
        cancel_appointment("P2", first["appointment"]["appointment_id"],
                           recommender=REC, path=TEST_DB)
        _, status = reschedule_appointment(
            "P1", appointment_id, self.tomorrow, "09:00", recommender=REC, path=TEST_DB
        )
        self.assertEqual(status, 200)


class AppointmentHistoryTests(AppointmentTestCase):
    def test_history_is_grouped_and_sorted(self):
        payload, _ = self.book(start="09:00", patient="P1")
        cancelled = payload["appointment"]["appointment_id"]
        cancel_appointment("P1", cancelled, recommender=REC, path=TEST_DB)
        self.book(start="10:00", patient="P1")
        self.book(start="09:00", patient="P2")

        history = list_patient_appointments("P1", recommender=REC, path=TEST_DB)
        self.assertTrue(history["success"])
        self.assertEqual(history["counts"], {"upcoming": 1, "past": 0, "cancelled": 1, "total": 2})
        self.assertEqual(history["upcoming"][0]["start_time"], "10:00")
        self.assertEqual(history["cancelled"][0]["appointment_id"], cancelled)
        self.assertEqual(history["cancelled"][0]["category"], "cancelled")

    def test_history_is_scoped_to_the_patient(self):
        self.book(start="09:00", patient="P1")
        self.book(start="09:30", patient="P2")
        p1 = list_patient_appointments("P1", recommender=REC, path=TEST_DB)
        p2 = list_patient_appointments("P2", recommender=REC, path=TEST_DB)
        self.assertEqual(p1["counts"]["total"], 1)
        self.assertEqual(p2["counts"]["total"], 1)

    def test_history_without_a_patient_is_rejected(self):
        payload = list_patient_appointments("", recommender=REC, path=TEST_DB)
        self.assertFalse(payload["success"])

    def test_elapsed_appointments_are_categorised_as_past(self):
        yesterday = (date.today() - timedelta(days=2)).isoformat()
        con = sqlite3.connect(TEST_DB)
        try:
            con.execute(
                'INSERT INTO "appointments" ("Appointment_ID", "Patient_ID", "Doctor_ID", '
                '"Appointment_Date", "Start_Time", "End_Time", "Status", "Consultation_Type", '
                '"Fee_INR", "Created_At") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                ("APT-OLD", "P1", "BK1", yesterday, "09:00", "09:30",
                 STATUS_BOOKED, "In-person", 750, "2026-01-01T00:00:00+00:00"),
            )
            con.commit()
        finally:
            con.close()

        history = list_patient_appointments(
            "P1", recommender=REC, path=TEST_DB,
            now=datetime.now(),
        )
        self.assertEqual(history["counts"]["past"], 1)
        self.assertEqual(history["past"][0]["appointment_id"], "APT-OLD")


if __name__ == "__main__":
    unittest.main()
