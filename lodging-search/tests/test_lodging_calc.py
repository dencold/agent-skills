import sys
import pathlib
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from lodging_calc import rooms_needed


class TestRoomsNeeded(unittest.TestCase):
    def test_family_of_four_fits_one_room(self):
        result = rooms_needed(adults=2, kids_ages=[6, 9])
        self.assertEqual(result["rooms"], 1)
        self.assertEqual(result["counted_occupants"], 4)
        self.assertFalse(result["forces_multi_room"])

    def test_family_of_five_needs_two_rooms(self):
        result = rooms_needed(adults=2, kids_ages=[4, 6, 9])
        self.assertEqual(result["rooms"], 2)
        self.assertEqual(result["counted_occupants"], 5)
        self.assertTrue(result["forces_multi_room"])

    def test_infant_not_counted_but_flagged_for_verification(self):
        result = rooms_needed(adults=2, kids_ages=[1, 6, 9])
        self.assertEqual(result["counted_occupants"], 4)
        self.assertEqual(result["uncounted_infants"], 1)
        self.assertEqual(result["rooms"], 1)
        self.assertTrue(result["verify_infant_policy"])

    def test_no_infants_means_no_verification_flag(self):
        result = rooms_needed(adults=2, kids_ages=[6, 9])
        self.assertEqual(result["uncounted_infants"], 0)
        self.assertFalse(result["verify_infant_policy"])

    def test_three_person_occupancy_cap_forces_second_room(self):
        result = rooms_needed(adults=2, kids_ages=[6, 9], max_occupancy=3)
        self.assertEqual(result["rooms"], 2)

    def test_large_group_rounds_up(self):
        result = rooms_needed(adults=4, kids_ages=[5, 7, 9, 11, 13])
        self.assertEqual(result["counted_occupants"], 9)
        self.assertEqual(result["rooms"], 3)

    def test_rejects_zero_adults(self):
        with self.assertRaises(ValueError):
            rooms_needed(adults=0, kids_ages=[6])


if __name__ == "__main__":
    unittest.main()
