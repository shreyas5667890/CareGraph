"""Tests for the configurable doctor-ranking and comparison engine.

These are service-level tests: they build a throwaway SQLite database with the
production schema and pass an explicit recommender, so no environment variable
or real data is touched.
"""
from __future__ import annotations

import unittest

from _support import create_doctor_db, temp_db_path  # noqa: F401  (adds Backend to sys.path)

import schema
from doctor_recommender_sqlite import DoctorRecommender
from ranking import (
    MAX_COMPARE_DOCTORS,
    RANKING_WEIGHTS,
    RankingWeights,
    affordability_score,
    compare_doctors,
    distance_score,
    experience_score,
    rank_doctors,
    rating_score,
    score_doctor,
    specialty_score,
)

USER_LAT = 19.2183
USER_LON = 72.9781


def doctor(doctor_id: str, **overrides) -> dict:
    row = {
        "Doctor_ID": doctor_id,
        "Doctor_Name": f"Dr {doctor_id}",
        "Specialization": "Cardiologist",
        "Age": 45,
        "Experience_Years": 15,
        "Description": "Synthetic test record.",
        "Address": "Test Clinic",
        "City": "Thane",
        "State": "Maharashtra",
        "Latitude": USER_LAT,
        "Longitude": USER_LON,
        "Consultation_Fee_INR": 800,
        "Rating_Out_of_5": 4.0,
        "Data_Status": "Synthetic demo record",
    }
    row.update(overrides)
    return row


# The three test doctors differ on every scored component so ordering is clear.
ROWS = [
    doctor("C1", Consultation_Fee_INR=400, Rating_Out_of_5=4.9, Experience_Years=25),
    doctor("C2", Consultation_Fee_INR=1500, Rating_Out_of_5=3.0, Experience_Years=2),
    doctor("C3", Consultation_Fee_INR=2000, Rating_Out_of_5=4.4, Experience_Years=30),
    doctor("D1", Specialization="Dermatologist"),
]

TEST_DB = create_doctor_db(temp_db_path(), ROWS)
schema.initialize(TEST_DB)
REC = DoctorRecommender(TEST_DB)


def tearDownModule():
    import os
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


class WeightConfigurationTests(unittest.TestCase):
    def test_default_weights_match_the_specification(self):
        self.assertEqual(
            RANKING_WEIGHTS.as_dict(),
            {"specialty": 0.35, "distance": 0.25, "rating": 0.15,
             "affordability": 0.15, "experience": 0.10},
        )

    def test_default_weights_total_one(self):
        self.assertAlmostEqual(RANKING_WEIGHTS.total(), 1.0, places=6)

    def test_weights_are_configurable_in_one_place(self):
        custom = RankingWeights(specialty=0.5, distance=0.5, rating=0.0,
                                affordability=0.0, experience=0.0)
        self.assertAlmostEqual(custom.total(), 1.0, places=6)
        result = score_doctor(
            {"specialization": "Cardiologist", "distance_km": 0.0},
            requested_specialty="Cardiologist",
            weights=custom,
        )
        # Only specialty and distance carry weight, both perfect -> 100.
        self.assertEqual(result["recommendation_score"], 100.0)
        self.assertEqual(set(result["components_used"]), {"specialty", "distance"})


class ComponentNormalisationTests(unittest.TestCase):
    def test_specialty_component(self):
        self.assertEqual(specialty_score("Cardiologist", "cardiologist"), 100.0)
        self.assertEqual(specialty_score("Cardiologist", "Dermatologist"), 0.0)
        # No target specialty -> scored against itself, keeping comparison usable.
        self.assertEqual(specialty_score("Cardiologist", None), 100.0)

    def test_distance_component(self):
        self.assertEqual(distance_score(0), 100.0)
        self.assertEqual(distance_score(25), 0.0)
        self.assertEqual(distance_score(12.5), 50.0)
        self.assertIsNone(distance_score(None))

    def test_rating_component(self):
        self.assertEqual(rating_score(5), 100.0)
        self.assertEqual(rating_score(0), 0.0)
        self.assertIsNone(rating_score(None))

    def test_affordability_component(self):
        self.assertEqual(affordability_score(0), 100.0)
        self.assertEqual(affordability_score(2000), 0.0)
        self.assertEqual(affordability_score(1000), 50.0)
        self.assertIsNone(affordability_score(None))

    def test_experience_component(self):
        self.assertEqual(experience_score(30), 100.0)
        self.assertEqual(experience_score(15), 50.0)
        self.assertIsNone(experience_score(None))


class ScoreDoctorTests(unittest.TestCase):
    def test_full_data_score_matches_hand_calculation(self):
        result = score_doctor(
            {
                "specialization": "Cardiologist",
                "distance_km": 0.0,
                "rating_out_of_5": 4.0,
                "consultation_fee_inr": 800,
                "experience_years": 15,
            },
            requested_specialty="Cardiologist",
        )
        # 100*.35 + 100*.25 + 80*.15 + 60*.15 + 50*.10 = 86.0
        self.assertEqual(result["recommendation_score"], 86.0)
        self.assertEqual(result["components_missing"], [])

    def test_missing_values_are_not_treated_as_zero(self):
        # A doctor with only a specialty match must not crash and must not be
        # penalised for unknown values.
        result = score_doctor(
            {"specialization": "Cardiologist"},
            requested_specialty="Cardiologist",
        )
        self.assertEqual(result["recommendation_score"], 100.0)
        self.assertEqual(result["components_used"], ["specialty"])
        self.assertEqual(set(result["components_missing"]),
                         {"distance", "rating", "affordability", "experience"})
        self.assertEqual(result["applied_weights"], {"specialty": 1.0})

    def test_remaining_weights_are_renormalised(self):
        result = score_doctor(
            {"specialization": "Cardiologist", "rating_out_of_5": 5.0},
            requested_specialty="Cardiologist",
        )
        # specialty (0.35) and rating (0.15) are both perfect; weights renormalise
        # but the score stays on the 0-100 scale.
        self.assertEqual(result["recommendation_score"], 100.0)
        self.assertAlmostEqual(sum(result["applied_weights"].values()), 1.0, places=5)
        self.assertEqual(result["components_missing"],
                         ["affordability", "distance", "experience"])

    def test_missing_specialty_scores_zero_but_other_components_still_count(self):
        # A recorded specialty that does not match the target is scored 0, not
        # treated as unknown, so the remaining components still contribute.
        result = score_doctor(
            {"specialization": "Dermatologist", "rating_out_of_5": 5.0},
            requested_specialty="Cardiologist",
        )
        self.assertEqual(result["score_breakdown"]["specialty"], 0.0)
        self.assertEqual(result["components_missing"], ["affordability", "distance", "experience"])
        self.assertEqual(result["recommendation_score"], 30.0)

    def test_nothing_scoreable_falls_back_to_zero_deterministically(self):
        result = score_doctor({}, requested_specialty=None)
        self.assertEqual(result["recommendation_score"], 0.0)
        self.assertEqual(result["applied_weights"], {})
        self.assertEqual(result["components_used"], [])


class RankDoctorsTests(unittest.TestCase):
    def test_only_requested_specialty_is_returned(self):
        result = rank_doctors("Cardiologist", recommender=REC)
        self.assertTrue(result["success"])
        ids = {d["doctor_id"] for d in result["doctors"]}
        self.assertNotIn("D1", ids)
        self.assertEqual(ids, {"C1", "C2", "C3"})

    def test_ordering_is_descending_by_score(self):
        result = rank_doctors(
            "Cardiologist", recommender=REC,
            user_latitude=USER_LAT, user_longitude=USER_LON,
        )
        scores = [d["recommendation_score"] for d in result["doctors"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_ranking_is_deterministic(self):
        first = rank_doctors("Cardiologist", recommender=REC,
                             user_latitude=USER_LAT, user_longitude=USER_LON)
        second = rank_doctors("Cardiologist", recommender=REC,
                              user_latitude=USER_LAT, user_longitude=USER_LON)
        self.assertEqual([d["doctor_id"] for d in first["doctors"]],
                         [d["doctor_id"] for d in second["doctors"]])
        self.assertEqual([d["recommendation_score"] for d in first["doctors"]],
                         [d["recommendation_score"] for d in second["doctors"]])

    def test_best_overall_doctor_ranks_first(self):
        result = rank_doctors("Cardiologist", recommender=REC,
                              user_latitude=USER_LAT, user_longitude=USER_LON)
        self.assertEqual(result["doctors"][0]["doctor_id"], "C1")

    def test_records_with_missing_values_are_ranked_not_dropped(self):
        rows = [
            doctor("FULL"),
            doctor("SPARSE", Latitude=None, Longitude=None, Rating_Out_of_5=None,
                   Consultation_Fee_INR=None, Experience_Years=None),
        ]
        path = create_doctor_db(temp_db_path(), rows)
        schema.initialize(path)
        try:
            result = rank_doctors("Cardiologist", recommender=DoctorRecommender(path))
            by_id = {d["doctor_id"]: d for d in result["doctors"]}
            self.assertEqual(set(by_id), {"FULL", "SPARSE"})
            # The sparse record is scored on the data it does have, and the
            # missing components are reported instead of counted as zero.
            self.assertEqual(by_id["SPARSE"]["components_used"], ["specialty"])
            self.assertIn("distance", by_id["SPARSE"]["components_missing"])
            self.assertIn("rating", by_id["SPARSE"]["components_missing"])
        finally:
            import os
            os.remove(path)

    def test_ties_are_broken_by_doctor_id(self):
        rows = [
            doctor("Z9", Consultation_Fee_INR=800, Rating_Out_of_5=4.0, Experience_Years=15),
            doctor("A1", Consultation_Fee_INR=800, Rating_Out_of_5=4.0, Experience_Years=15),
        ]
        path = create_doctor_db(temp_db_path(), rows)
        schema.initialize(path)
        try:
            result = rank_doctors("Cardiologist", recommender=DoctorRecommender(path),
                                  user_latitude=USER_LAT, user_longitude=USER_LON)
            self.assertEqual([d["doctor_id"] for d in result["doctors"]], ["A1", "Z9"])
        finally:
            import os
            os.remove(path)

    def test_distance_components_reflect_missing_coordinates(self):
        without = rank_doctors("Cardiologist", recommender=REC)
        self.assertFalse(without["distance_available"])
        for record in without["doctors"]:
            self.assertIn("distance", record["components_missing"])

        with_coords = rank_doctors("Cardiologist", recommender=REC,
                                   user_latitude=USER_LAT, user_longitude=USER_LON)
        self.assertTrue(with_coords["distance_available"])

    def test_eligibility_filter_applied_before_ranking(self):
        result = rank_doctors("Cardiologist", recommender=REC, max_fee=500)
        ids = [d["doctor_id"] for d in result["doctors"]]
        self.assertIn("C1", ids)
        self.assertNotIn("C2", ids)
        self.assertNotIn("C3", ids)

    def test_limit_is_applied_after_ranking(self):
        result = rank_doctors("Cardiologist", recommender=REC, limit=2)
        self.assertEqual(len(result["doctors"]), 2)
        self.assertEqual(result["doctors"][0]["doctor_id"], "C1")

    def test_missing_specialty_is_rejected(self):
        result = rank_doctors("", recommender=REC)
        self.assertFalse(result["success"])

    def test_unknown_specialty_returns_empty_list(self):
        result = rank_doctors("Astrologer", recommender=REC)
        self.assertTrue(result["success"])
        self.assertEqual(result["doctors"], [])

    def test_score_disclaimer_is_present(self):
        result = rank_doctors("Cardiologist", recommender=REC)
        self.assertTrue(result["score_disclaimer"])
        self.assertTrue(result["notice"])


class CompareDoctorsTests(unittest.TestCase):
    def test_three_doctors_are_allowed(self):
        payload, status = compare_doctors(["C1", "C2", "C3"], recommender=REC)
        self.assertEqual(status, 200)
        self.assertTrue(payload["success"])
        self.assertEqual(payload["max_doctors"], MAX_COMPARE_DOCTORS)
        self.assertEqual(len(payload["doctors"]), 3)

    def test_single_and_two_doctors_are_allowed(self):
        for ids in (["C1"], ["C1", "C2"]):
            payload, status = compare_doctors(ids, recommender=REC)
            self.assertEqual(status, 200, ids)
            self.assertEqual(len(payload["doctors"]), len(ids))

    def test_fourth_doctor_is_rejected(self):
        payload, status = compare_doctors(["C1", "C2", "C3", "D1"], recommender=REC)
        self.assertEqual(status, 400)
        self.assertFalse(payload["success"])

    def test_duplicates_are_rejected(self):
        payload, status = compare_doctors(["C1", "c1"], recommender=REC)
        self.assertEqual(status, 400)
        self.assertFalse(payload["success"])

    def test_empty_selection_is_rejected(self):
        payload, status = compare_doctors([], recommender=REC)
        self.assertEqual(status, 400)

    def test_non_string_ids_are_rejected(self):
        payload, status = compare_doctors(["C1", 42], recommender=REC)
        self.assertEqual(status, 400)

    def test_unknown_doctor_returns_404_with_missing_ids(self):
        payload, status = compare_doctors(["C1", "NOPE"], recommender=REC)
        self.assertEqual(status, 404)
        self.assertEqual(payload["missing_doctor_ids"], ["NOPE"])

    def test_comparison_uses_the_same_score_as_ranking(self):
        ranked = rank_doctors("Cardiologist", recommender=REC,
                              user_latitude=USER_LAT, user_longitude=USER_LON)
        payload, status = compare_doctors(
            ["C1", "C2"], recommender=REC,
            requested_specialty="Cardiologist",
            user_latitude=USER_LAT, user_longitude=USER_LON,
        )
        self.assertEqual(status, 200)
        by_id = {d["doctor_id"]: d["recommendation_score"] for d in ranked["doctors"]}
        for record in payload["doctors"]:
            self.assertEqual(record["recommendation_score"], by_id[record["doctor_id"]])

    def test_invalid_coordinates_are_rejected(self):
        payload, status = compare_doctors(
            ["C1"], recommender=REC, user_latitude=200, user_longitude=USER_LON
        )
        self.assertEqual(status, 400)

    def test_partial_coordinates_are_rejected(self):
        payload, status = compare_doctors(["C1"], recommender=REC, user_latitude=USER_LAT)
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
