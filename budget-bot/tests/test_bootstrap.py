import sys
import pathlib
import unittest
from decimal import Decimal

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from bootstrap import (
    AMBIGUITY_MINORITY_COUNT, build_map,
)
from ledger import Row


def rows_for(merchant, category, count, month="01", year="2025"):
    # Days cycle within 1..28 so a 41-row merchant still yields real dates.
    return [
        Row(timestamp=f"{year}-{month}-{(i % 28) + 1:02d}",
            transaction=merchant, amount=Decimal("10.00"),
            account="Test Visa", category=category)
        for i in range(count)
    ]


class TestBuildMap(unittest.TestCase):
    def test_single_category_merchant_is_unflagged(self):
        entries, _ = build_map(rows_for("SAFEWAY 1842", "Grocery", 41))
        self.assertEqual(entries["SAFEWAY"].category, "Grocery")
        self.assertFalse(entries["SAFEWAY"].ambiguous)
        self.assertEqual(entries["SAFEWAY"].seen, 41)

    def test_genuine_split_is_flagged_with_the_runner_up_counted(self):
        rows = rows_for("COSTCO WHSE", "Grocery", 18) + \
               rows_for("COSTCO WHSE", "Household", 13, month="02")
        entries, report = build_map(rows)
        self.assertTrue(entries["COSTCO WHSE"].ambiguous)
        self.assertEqual(entries["COSTCO WHSE"].category, "Grocery")
        self.assertEqual(entries["COSTCO WHSE"].alternates, {"Household": 13})
        self.assertIn("COSTCO WHSE", report.flagged)

    def test_stale_mistag_below_the_threshold_is_not_flagged(self):
        rows = rows_for("SAFEWAY", "Grocery", 41) + \
               rows_for("SAFEWAY", "Household", 1, month="02")
        entries, _ = build_map(rows)
        self.assertFalse(entries["SAFEWAY"].ambiguous)
        self.assertEqual(entries["SAFEWAY"].category, "Grocery")

    def test_minority_needs_the_minimum_count_not_just_the_share(self):
        # 2 of 8 clears 20% but not the occurrence floor.
        rows = rows_for("PEETS", "Dining", 6) + rows_for("PEETS", "Grocery", 2, month="02")
        entries, _ = build_map(rows)
        self.assertFalse(entries["PEETS"].ambiguous)
        self.assertEqual(AMBIGUITY_MINORITY_COUNT, 3)

    def test_recent_recategorization_overrides_the_all_time_majority(self):
        rows = rows_for("SPROUTS", "Dining", 24, year="2024") + \
               rows_for("SPROUTS", "Grocery", 5, year="2026", month="08")
        entries, report = build_map(rows)
        self.assertEqual(entries["SPROUTS"].category, "Grocery")
        self.assertIn("SPROUTS", report.recency_overrides)

    def test_blank_and_unknown_categories_are_reported_not_imported(self):
        rows = rows_for("SAFEWAY", "Grocery", 5) + \
               rows_for("MYSTERY", "", 2, month="02") + \
               rows_for("TYPO SHOP", "Groceries", 3, month="03")
        entries, report = build_map(rows, known_categories={"Grocery", "Dining"})
        self.assertNotIn("MYSTERY", entries)
        self.assertNotIn("TYPO SHOP", entries)
        self.assertEqual(report.blank_category_rows, 2)
        self.assertEqual(report.unknown_categories, {"Groceries": 3})


if __name__ == "__main__":
    unittest.main()
