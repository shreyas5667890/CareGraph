"""Haversine distance and coordinate validation tests."""
from __future__ import annotations

import unittest

from _support import BACKEND_DIR  # noqa: F401  (adds Backend to sys.path)

from doctor_recommender_sqlite import haversine_km, validate_coordinates


class HaversineTests(unittest.TestCase):
    def test_identical_coordinates_are_zero(self):
        self.assertEqual(haversine_km(19.0, 72.0, 19.0, 72.0), 0.0)

    def test_one_degree_of_latitude(self):
        # One degree of latitude is ~111.2 km anywhere on Earth.
        distance = haversine_km(0.0, 0.0, 1.0, 0.0)
        self.assertAlmostEqual(distance, 111.19, delta=0.5)

    def test_known_city_distance(self):
        # Mumbai -> Delhi great-circle distance is roughly 1150 km.
        distance = haversine_km(19.0760, 72.8777, 28.6139, 77.2090)
        self.assertTrue(1100 <= distance <= 1200, distance)

    def test_nearby_coordinates(self):
        # ~0.01 degrees of latitude is ~1.11 km.
        distance = haversine_km(19.0000, 72.0000, 19.0100, 72.0000)
        self.assertAlmostEqual(distance, 1.11, delta=0.05)

    def test_is_symmetric(self):
        forward = haversine_km(19.0, 72.0, 19.2, 72.9)
        backward = haversine_km(19.2, 72.9, 19.0, 72.0)
        self.assertAlmostEqual(forward, backward, places=9)

    def test_missing_coordinates_return_none(self):
        self.assertIsNone(haversine_km(None, 72.0, 19.0, 72.0))
        self.assertIsNone(haversine_km(19.0, None, 19.0, 72.0))
        self.assertIsNone(haversine_km(19.0, 72.0, None, None))

    def test_invalid_coordinates_return_none(self):
        self.assertIsNone(haversine_km(200.0, 72.0, 19.0, 72.0))
        self.assertIsNone(haversine_km(19.0, 500.0, 19.0, 72.0))
        self.assertIsNone(haversine_km(float("nan"), 72.0, 19.0, 72.0))
        self.assertIsNone(haversine_km(float("inf"), 72.0, 19.0, 72.0))
        self.assertIsNone(haversine_km("not-a-number", 72.0, 19.0, 72.0))


class CoordinateValidationTests(unittest.TestCase):
    def test_both_missing_is_accepted(self):
        self.assertEqual(validate_coordinates(None, None), (None, None, None))
        self.assertEqual(validate_coordinates("", ""), (None, None, None))

    def test_only_one_supplied_is_rejected(self):
        latitude, longitude, error = validate_coordinates(19.0, None)
        self.assertIsNone(latitude)
        self.assertIsNone(longitude)
        self.assertIn("together", error)

        _, _, error = validate_coordinates(None, 72.0)
        self.assertIn("together", error)

    def test_out_of_range_is_rejected(self):
        _, _, error = validate_coordinates(91.0, 72.0)
        self.assertIsNotNone(error)
        _, _, error = validate_coordinates(19.0, 181.0)
        self.assertIsNotNone(error)

    def test_non_numeric_is_rejected(self):
        _, _, error = validate_coordinates("abc", "def")
        self.assertIsNotNone(error)

    def test_valid_string_input_is_accepted(self):
        latitude, longitude, error = validate_coordinates("19.2183", "72.9781")
        self.assertIsNone(error)
        self.assertAlmostEqual(latitude, 19.2183)
        self.assertAlmostEqual(longitude, 72.9781)


if __name__ == "__main__":
    unittest.main()
