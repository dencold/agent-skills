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

    def test_category_with_semicolon_round_trips(self):
        save_map(self.tmp, {
            "STORE": MapEntry("STORE", "Grocery", 10, True, 0, {"Fees; Interest": 3}),
        })
        back = load_map(self.tmp)
        self.assertEqual(back["STORE"].alternates, {"Fees; Interest": 3})

    def test_category_with_colon_round_trips(self):
        save_map(self.tmp, {
            "RESTAURANT": MapEntry("RESTAURANT", "Dining", 10, True, 0, {"Tips: Restaurant": 5}),
        })
        back = load_map(self.tmp)
        self.assertEqual(back["RESTAURANT"].alternates, {"Tips: Restaurant": 5})

    def test_category_with_comma_round_trips(self):
        save_map(self.tmp, {
            "STORE": MapEntry("STORE", "Groceries", 10, True, 0, {"Gas, Oil": 2}),
        })
        back = load_map(self.tmp)
        self.assertEqual(back["STORE"].alternates, {"Gas, Oil": 2})

    def test_malformed_count_raises_with_context(self):
        save_map(self.tmp, {
            "STORE": MapEntry("STORE", "Grocery", 10, False, 0, {}),
        })
        # Manually edit the file to introduce malformed data
        content = self.tmp.read_text()
        content = content.replace("STORE,Grocery,10,false,0,",
                                  'STORE,Grocery,10,false,0,"Household,many"')
        self.tmp.write_text(content)
        # load_map should raise ValueError with merchant context
        with self.assertRaises(ValueError) as cm:
            load_map(self.tmp)
        self.assertIn("STORE", str(cm.exception))
        self.assertIn("Household", str(cm.exception))


class TestAmbiguousColumn(unittest.TestCase):
    """SKILL.md tells the user to hand-edit this cell, so it reads what they type."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()) / "merchant-map.csv"

    def _write(self, ambiguous="false", seen="10", streak="0"):
        self.tmp.write_text(
            "merchant,category,seen,ambiguous,streak,alternates\n"
            f"COSTCO,Grocery,{seen},{ambiguous},{streak},\n")

    def test_yes_reads_as_true(self):
        for word in ("yes", "Yes", "Y", "1", "TRUE"):
            with self.subTest(word=word):
                self._write(ambiguous=word)
                self.assertTrue(load_map(self.tmp)["COSTCO"].ambiguous)

    def test_no_reads_as_false(self):
        for word in ("no", "N", "0", "False", ""):
            with self.subTest(word=word):
                self._write(ambiguous=word)
                self.assertFalse(load_map(self.tmp)["COSTCO"].ambiguous)

    def test_anything_else_raises_naming_merchant_and_cell(self):
        self._write(ambiguous="maybe")
        with self.assertRaises(ValueError) as ctx:
            load_map(self.tmp)
        self.assertIn("COSTCO", str(ctx.exception))
        self.assertIn("maybe", str(ctx.exception))
        self.assertIn("ambiguous", str(ctx.exception))

    def test_bad_seen_raises_with_merchant_context(self):
        self._write(seen="lots")
        with self.assertRaises(ValueError) as ctx:
            load_map(self.tmp)
        self.assertIn("COSTCO", str(ctx.exception))
        self.assertIn("seen", str(ctx.exception))

    def test_bad_streak_raises_with_merchant_context(self):
        self._write(streak="three")
        with self.assertRaises(ValueError) as ctx:
            load_map(self.tmp)
        self.assertIn("COSTCO", str(ctx.exception))
        self.assertIn("streak", str(ctx.exception))


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

    def test_streak_is_not_bumped_twice_within_one_run(self):
        # The streak counts consecutive runs the user accepted a category.
        # Five Costco trips in one month are one confirmation, not five.
        entries = {"COSTCO": MapEntry("COSTCO", "Grocery", 30, True, 0, {"Household": 13})}
        record_decision(entries, "COSTCO", "Grocery")
        for _ in range(4):
            record_decision(entries, "COSTCO", "Grocery", count_streak=False)
        self.assertEqual(entries["COSTCO"].streak, 1)
        self.assertEqual(entries["COSTCO"].seen, 35)

    def test_disagreement_resets_the_streak_even_mid_run(self):
        entries = {"COSTCO": MapEntry("COSTCO", "Grocery", 30, True, 3, {})}
        record_decision(entries, "COSTCO", "Grocery")
        record_decision(entries, "COSTCO", "Household", count_streak=False)
        self.assertEqual(entries["COSTCO"].streak, 0)

    def test_alternate_that_overtakes_the_stored_category_is_promoted(self):
        # Six overrides of a five-row Grocery entry: "usually Grocery" is
        # now false, and the review table would pre-fill the minority.
        entries = {"COSTCO": MapEntry("COSTCO", "Grocery", 5, False, 0, {})}
        for _ in range(6):
            record_decision(entries, "COSTCO", "Household")
        entry = entries["COSTCO"]
        self.assertEqual(entry.category, "Household")
        self.assertEqual(entry.alternates, {"Grocery": 5})
        self.assertEqual(entry.seen, 11)
        self.assertEqual(entry.describe(),
                         "Household (usually; 5 of 11 were Grocery)")

    def test_a_minority_alternate_does_not_take_over(self):
        entries = {"TARGET": MapEntry("TARGET", "Household", 20, False, 0, {})}
        for _ in range(3):
            record_decision(entries, "TARGET", "Gifts")
        self.assertEqual(entries["TARGET"].category, "Household")


class TestDescribe(unittest.TestCase):
    def test_ambiguous_entry_shows_the_split(self):
        entry = MapEntry("COSTCO", "Grocery", 31, True, 0, {"Household": 13})
        self.assertEqual(entry.describe(), "Grocery (usually; 13 of 31 were Household)")

    def test_unambiguous_entry_is_just_the_category(self):
        self.assertEqual(MapEntry("BLUE BOTTLE", "Dining", 14, False).describe(), "Dining")


if __name__ == "__main__":
    unittest.main()
