import sys
import pathlib
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from merchant_map import (
    CLEAR_FLAG_STREAK, MapEntry, load_map, record_decision, save_map,
)


class TestMapRoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()) / "merchant-map.csv"

    def test_round_trip_preserves_flag_and_alternates(self):
        save_map(self.tmp, {
            "COSTCO": MapEntry("COSTCO", "Grocery", 31, True, 0, {"Household": 13}),
            "BLUE BOTTLE": MapEntry("BLUE BOTTLE", "Dining", 14, False, 2, {}),
        })
        back = load_map(self.tmp)
        self.assertTrue(back["COSTCO"].ambiguous)
        self.assertEqual(back["COSTCO"].alternates, {"Household": 13})
        self.assertFalse(back["BLUE BOTTLE"].ambiguous)
        self.assertEqual(back["BLUE BOTTLE"].streak, 2)

    def test_saved_file_is_sorted_for_diffability(self):
        save_map(self.tmp, {
            "ZEBRA": MapEntry("ZEBRA", "Dining", 1, False),
            "APPLE": MapEntry("APPLE", "Tech", 1, False),
        })
        merchants = [line.split(",")[0] for line in
                     self.tmp.read_text().splitlines()[1:]]
        self.assertEqual(merchants, ["APPLE", "ZEBRA"])

    def test_missing_file_loads_as_empty(self):
        self.assertEqual(load_map(self.tmp.parent / "nope.csv"), {})


class TestRecordDecision(unittest.TestCase):
    def test_new_merchant_is_added_unflagged(self):
        entries = {}
        entry = record_decision(entries, "BLUE BOTTLE", "Dining")
        self.assertEqual(entry.seen, 1)
        self.assertFalse(entry.ambiguous)

    def test_agreement_bumps_seen_and_streak(self):
        entries = {"BLUE BOTTLE": MapEntry("BLUE BOTTLE", "Dining", 14, False, 1, {})}
        entry = record_decision(entries, "BLUE BOTTLE", "Dining")
        self.assertEqual(entry.seen, 15)
        self.assertEqual(entry.streak, 2)

    def test_disagreement_flags_ambiguous_and_records_the_alternate(self):
        entries = {"TARGET": MapEntry("TARGET", "Household", 20, False, 4, {})}
        entry = record_decision(entries, "TARGET", "Gifts")
        self.assertTrue(entry.ambiguous)
        self.assertEqual(entry.alternates, {"Gifts": 1})
        self.assertEqual(entry.streak, 0)
        # The majority category stays the default shown next month.
        self.assertEqual(entry.category, "Household")

    def test_streak_reaching_the_threshold_is_reported_not_auto_cleared(self):
        entries = {"COSTCO": MapEntry("COSTCO", "Grocery", 30, True,
                                      CLEAR_FLAG_STREAK - 1, {"Household": 13})}
        entry = record_decision(entries, "COSTCO", "Grocery")
        self.assertEqual(entry.streak, CLEAR_FLAG_STREAK)
        self.assertTrue(entry.ambiguous, "the flag is only cleared when the user says so")
        self.assertTrue(entry.clearable())


class TestDescribe(unittest.TestCase):
    def test_ambiguous_entry_shows_the_split(self):
        entry = MapEntry("COSTCO", "Grocery", 31, True, 0, {"Household": 13})
        self.assertEqual(entry.describe(), "Grocery (usually; 13 of 31 were Household)")

    def test_unambiguous_entry_is_just_the_category(self):
        self.assertEqual(MapEntry("BLUE BOTTLE", "Dining", 14, False).describe(), "Dining")


if __name__ == "__main__":
    unittest.main()
