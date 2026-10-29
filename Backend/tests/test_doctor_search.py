"""Doctor search tests: strict specialty matching, distance, filters and sorting.

These tests run against a purpose-built temporary SQLite database with known
values so ordering assertions are exact. The real database is never modified.
"""
from __future__ import annotations

import os
import unittest

from _support import (  # noqa: F401  (also adds Backend to sys.path)
    BACKEND_DIR,
    REAL_DB,
    create_doctor_db,
    temp_db_path,
)
from doctor_recommender_sqlite import DoctorRecommender

USER_LAT = 19.0000
USER_LON = 72.0000


def _doctor(**overrides) -> dict:
    row = {
        "Doctor_ID": "",
        "Doctor_Name": "Dr Test",
        "Specialization": "Neurologist",
        "Age": 40,
        "Experience_Years": 10,
        "Description": "Test record",
        "Address": "Test Clinic",
        "City": "Mumbai",
        "State": "Maharashtra",
        "Latitude": None,
        "Longitude": None,
        "Consultation_Fee_INR": None,
        "Rating_Out_of_5": None,
        "Data_Status": "Synthetic demo record",
    }
    row.update(overrides)
    return row


# Distances from (19.0000, 72.0000):
#   N2 19.0100 -> ~1.11 km      N3 19.0200 -> ~2.22 km
#   N1 19.0300 -> ~3.34 km      N5 19.0400 -> ~4.45 km
#   N4 19.0500 -> ~5.56 km      N6 -> coordinates missing
DOCTORS = [
    _doctor(Doctor_ID="N1", Doctor_Name="Dr One", Experience_Years=10,
            Latitude=19.0300, Longitude=72.0000,
            Consultation_Fee_INR=900, Rating_Out_of_5=4.5),
    _doctor(Doctor_ID="N2", Doctor_Name="Dr Two", Experience_Years=5,
            Latitude=19.0100, Longitude=72.0000,
            Consultation_Fee_INR=900, Rating_Out_of_5=4.5),
    _doctor(Doctor_ID="N3", Doctor_Name="Dr Three", Experience_Years=20,
            Latitude=19.0200, Longitude=72.0000,
            Consultation_Fee_INR=500, Rating_Out_of_5=4.0),
    _doctor(Doctor_ID="N4", Doctor_Name="Dr Four", Experience_Years=1,
            Latitude=19.0500, Longitude=72.0000,
            Consultation_Fee_INR=100, Rating_Out_of_5=None),
    _doctor(Doctor_ID="N5", Doctor_Name="Dr Five", Experience_Years=30,
            Latitude=19.0400, Longitude=72.0000,
            Consultation_Fee_INR=None, Rating_Out_of_5=4.9),
    _doctor(Doctor_ID="N6", Doctor_Name="Dr Six", Experience_Years=12,
            Latitude=None, Longitude=None,
            Consultation_Fee_INR=700, Rating_Out_of_5=4.2),
    _doctor(Doctor_ID="C1", Doctor_Name="Dr Heart", Specialization="Cardiologist",
            Experience_Years=25, Latitude=19.0010, Longitude=72.0000,
            Consultation_Fee_INR=1200, Rating_Out_of_5=3.5),
]


def _ids(result: dict) -> list[str]:
    return [doctor["doctor_id"] for doctor in result["doctors"]]


class DoctorSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_path = create_doctor_db(temp_db_path(), DOCTORS)
        cls.recommender = DoctorRecommender(cls.db_path)

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(cls.db_path):
            os.remove(cls.db_path)

    def search(self, **kwargs):
        return self.recommender.search(**kwargs)

    # ------------------------------------------------------------ specialty
    def test_neurologist_search_returns_only_neurologists(self):
        result = self.search(specialty="Neurologist")
        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 7 - 1)  # C1 must not appear
        self.assertTrue(all(d["specialization"] == "Neurologist" for d in result["doctors"]))
        self.assertNotIn("C1", _ids(result))

    def test_cardiologist_search_returns_only_cardiologists(self):
        result = self.search(specialty="Cardiologist")
        self.assertTrue(result["success"])
        self.assertEqual(_ids(result), ["C1"])

    def test_specialty_matching_ignores_case_and_whitespace(self):
        result = self.search(specialty="  nEuRoLoGiSt  ")
        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 6)

    def test_no_specialty_is_substituted(self):
        result = self.search(specialty="Neuro")
        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["total"], 0)

    def test_unknown_specialty_returns_safe_empty_result(self):
        result = self.search(specialty="Astronaut")
        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 0)
        self.assertIn("message", result)

    def test_missing_specialty_is_a_validation_error(self):
        for value in ("", "   ", None):
            result = self.search(specialty=value)
            self.assertFalse(result["success"])
            self.assertIn("specialty", result["error"].lower())

    def test_invalid_sort_is_rejected(self):
        result = self.search(specialty="Neurologist", sort_by="by-vibes")
        self.assertFalse(result["success"])

    # ------------------------------------------------------------- distance
    def test_distances_are_calculated(self):
        result = self.search(
            specialty="Neurologist", user_latitude=USER_LAT, user_longitude=USER_LON,
            sort_by="distance",
        )
        by_id = {d["doctor_id"]: d for d in result["doctors"]}
        self.assertEqual(by_id["N2"]["distance_km"], 1.11)
        self.assertEqual(by_id["N3"]["distance_km"], 2.22)
        self.assertIsNone(by_id["N6"]["distance_km"])
        self.assertIsNone(by_id["N6"]["latitude"])

    def test_identical_user_and_clinic_coordinates(self):
        result = self.search(
            specialty="Neurologist", user_latitude=19.0300, user_longitude=72.0000,
            sort_by="distance",
        )
        self.assertEqual(result["doctors"][0]["doctor_id"], "N1")
        self.assertEqual(result["doctors"][0]["distance_km"], 0.0)

    def test_max_distance_filter(self):
        result = self.search(
            specialty="Neurologist", user_latitude=USER_LAT, user_longitude=USER_LON,
            max_distance_km=3,
        )
        self.assertEqual(sorted(_ids(result)), ["N2", "N3"])

    def test_max_distance_with_no_matches_returns_empty_with_message(self):
        result = self.search(
            specialty="Neurologist", user_latitude=USER_LAT, user_longitude=USER_LON,
            max_distance_km=1,
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 0)
        self.assertIn("message", result)

    def test_missing_clinic_coordinates_excluded_by_distance_filter(self):
        without_filter = self.search(
            specialty="Neurologist", user_latitude=USER_LAT, user_longitude=USER_LON,
            page=1, page_size=50,
        )
        self.assertIn("N6", _ids(without_filter))
        with_filter = self.search(
            specialty="Neurologist", user_latitude=USER_LAT, user_longitude=USER_LON,
            max_distance_km=500, page=1, page_size=50,
        )
        self.assertNotIn("N6", _ids(with_filter))

    def test_distant_doctors_are_not_excluded_automatically(self):
        result = self.search(
            specialty="Neurologist", user_latitude=USER_LAT, user_longitude=USER_LON,
            page=1, page_size=50,
        )
        self.assertEqual(result["total"], 6)

    # ------------------------------------------------------ coordinate errors
    def test_invalid_user_coordinates_are_rejected(self):
        for latitude, longitude in ((91.0, 72.0), (19.0, 181.0), ("abc", "def")):
            result = self.search(
                specialty="Neurologist", user_latitude=latitude, user_longitude=longitude,
            )
            self.assertFalse(result["success"], (latitude, longitude))

    def test_partial_user_coordinates_are_rejected(self):
        result = self.search(specialty="Neurologist", user_latitude=19.0)
        self.assertFalse(result["success"])
        self.assertIn("together", result["error"])

    def test_max_distance_requires_coordinates(self):
        result = self.search(specialty="Neurologist", max_distance_km=10)
        self.assertFalse(result["success"])

    def test_nearest_sort_requires_coordinates(self):
        result = self.search(specialty="Neurologist", sort_by="distance")
        self.assertFalse(result["success"])
        self.assertIn("location", result["error"].lower())

    # -------------------------------------------------------------- sorting
    def test_recommended_sorting_without_coordinates(self):
        result = self.search(specialty="Neurologist", page=1, page_size=50)
        self.assertEqual(_ids(result), ["N5", "N1", "N2", "N6", "N3", "N4"])

    def test_recommended_sorting_prefers_rating_then_fee(self):
        result = self.search(specialty="Neurologist", page=1, page_size=50)
        first = result["doctors"][0]
        self.assertEqual(first["doctor_id"], "N5")   # highest rating
        self.assertEqual(first["rating_out_of_5"], 4.9)
        # N1/N2 tie on rating and fee, so experience breaks the tie without coordinates.
        self.assertEqual(_ids(result)[1:3], ["N1", "N2"])

    def test_recommended_sorting_uses_distance_as_tie_breaker(self):
        result = self.search(
            specialty="Neurologist", user_latitude=USER_LAT, user_longitude=USER_LON,
            page=1, page_size=50,
        )
        # N1 and N2 tie on rating and fee, so the nearer clinic (N2) wins.
        self.assertEqual(_ids(result), ["N5", "N2", "N1", "N6", "N3", "N4"])

    def test_highest_rating_sorting(self):
        result = self.search(specialty="Neurologist", sort_by="rating", page=1, page_size=50)
        self.assertEqual(_ids(result), ["N5", "N1", "N2", "N6", "N3", "N4"])

    def test_lowest_fee_sorting_is_numeric(self):
        result = self.search(specialty="Neurologist", sort_by="fees", page=1, page_size=50)
        self.assertEqual(_ids(result), ["N4", "N3", "N6", "N1", "N2", "N5"])
        fees = [d["consultation_fee_inr"] for d in result["doctors"] if d["consultation_fee_inr"] is not None]
        self.assertEqual(fees, sorted(fees))

    def test_most_experienced_sorting(self):
        result = self.search(specialty="Neurologist", sort_by="experience", page=1, page_size=50)
        self.assertEqual(_ids(result), ["N5", "N3", "N6", "N1", "N2", "N4"])

    def test_nearest_doctor_sorting(self):
        result = self.search(
            specialty="Neurologist", sort_by="distance",
            user_latitude=USER_LAT, user_longitude=USER_LON, page=1, page_size=50,
        )
        self.assertEqual(_ids(result), ["N2", "N3", "N1", "N5", "N4", "N6"])

    def test_records_with_missing_values_sort_last(self):
        result = self.search(specialty="Neurologist", sort_by="fees", page=1, page_size=50)
        self.assertEqual(_ids(result)[-1], "N5")   # missing fee
        result = self.search(specialty="Neurologist", sort_by="rating", page=1, page_size=50)
        self.assertEqual(_ids(result)[-1], "N4")   # missing rating

    # ------------------------------------------------------------- filtering
    def test_fee_filter(self):
        result = self.search(specialty="Neurologist", max_fee=700, page=1, page_size=50)
        self.assertEqual(_ids(result), ["N6", "N3", "N4"])
        for doctor in result["doctors"]:
            self.assertLessEqual(doctor["consultation_fee_inr"], 700)

    def test_min_rating_filter(self):
        result = self.search(specialty="Neurologist", min_rating=4.5, page=1, page_size=50)
        self.assertEqual(_ids(result), ["N5", "N1", "N2"])
        for doctor in result["doctors"]:
            self.assertGreaterEqual(doctor["rating_out_of_5"], 4.5)

    def test_invalid_filter_values_are_rejected(self):
        self.assertFalse(self.search(specialty="Neurologist", max_fee=-1)["success"])
        self.assertFalse(self.search(specialty="Neurologist", min_rating=5.5)["success"])
        self.assertFalse(self.search(specialty="Neurologist", max_fee="cheap")["success"])

    def test_filters_and_sorting_combine(self):
        result = self.search(
            specialty="Neurologist",
            user_latitude=USER_LAT, user_longitude=USER_LON,
            max_distance_km=6, max_fee=1000, min_rating=4.0, sort_by="distance",
            page=1, page_size=50,
        )
        # N4 (no rating), N5 (no fee) and N6 (no coordinates) are all excluded.
        self.assertEqual(_ids(result), ["N2", "N3", "N1"])

    # ------------------------------------------------------------ pagination
    def test_pagination_applies_after_filtering_and_sorting(self):
        page_one = self.search(specialty="Neurologist", page=1, page_size=2)
        self.assertEqual(_ids(page_one), ["N5", "N1"])
        self.assertEqual(page_one["total"], 6)
        self.assertEqual(page_one["count"], 2)

        page_two = self.search(specialty="Neurologist", page=2, page_size=2)
        self.assertEqual(_ids(page_two), ["N2", "N6"])

        page_three = self.search(specialty="Neurologist", page=3, page_size=2)
        self.assertEqual(_ids(page_three), ["N3", "N4"])

        page_four = self.search(specialty="Neurologist", page=4, page_size=2)
        self.assertEqual(_ids(page_four), [])
        self.assertEqual(page_four["total"], 6)

    def test_pagination_total_reflects_filters(self):
        result = self.search(specialty="Neurologist", max_fee=700, page=1, page_size=1)
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["count"], 1)


class RealDirectoryTests(unittest.TestCase):
    """Sanity checks against the shipped SQLite database (read-only)."""

    @classmethod
    def setUpClass(cls):
        cls.recommender = DoctorRecommender(REAL_DB)

    def test_known_specialty_returns_only_that_specialty(self):
        for specialty in ("Neurologist", "Cardiologist", "Dermatologist"):
            result = self.recommender.search(specialty=specialty, limit=50)
            self.assertTrue(result["success"])
            self.assertGreater(result["count"], 0)
            self.assertTrue(
                all(d["specialization"] == specialty for d in result["doctors"]),
                specialty,
            )

    def test_specialties_are_listed(self):
        specialties = self.recommender.list_specialties()
        self.assertIn("Neurologist", specialties)
        self.assertIn("Cardiologist", specialties)
        self.assertEqual(len(specialties), len(set(specialties)))


if __name__ == "__main__":
    unittest.main()
