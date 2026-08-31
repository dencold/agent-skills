import sys
import pathlib
import tempfile
import unittest
from decimal import Decimal
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import bootstrap
from bootstrap import (
    AMBIGUITY_MINORITY_COUNT, build_map,
)
from ledger import Row, write_ledger


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
        entry = entries["SPROUTS"]
        self.assertEqual(entry.category, "Grocery")
        self.assertIn("SPROUTS", report.recency_overrides)
        # The superseded majority is settled history, not a live split --
        # it must not surface as an alternate or trip the ambiguity flag.
        self.assertFalse(entry.ambiguous)
        self.assertEqual(entry.alternates, {})
        self.assertEqual(entry.describe(), "Grocery")

    def test_blank_and_unknown_categories_are_reported_not_imported(self):
        rows = rows_for("SAFEWAY", "Grocery", 5) + \
               rows_for("MYSTERY", "", 2, month="02") + \
               rows_for("TYPO SHOP", "Groceries", 3, month="03")
        entries, report = build_map(rows, known_categories={"Grocery", "Dining"})
        self.assertNotIn("MYSTERY", entries)
        self.assertNotIn("TYPO SHOP", entries)
        self.assertEqual(report.blank_category_rows, 2)
        self.assertEqual(report.unknown_categories, {"Groceries": 3})


class TestMainCategoriesFlag(unittest.TestCase):
    def test_categories_file_is_parsed_and_reaches_build_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)

            history_path = tmp_path / "history.csv"
            write_ledger(history_path, rows_for("SAFEWAY", "Grocery", 3))

            categories_path = tmp_path / "categories.txt"
            categories_path.write_text(
                "Grocery\n# a comment line\n\nDining\n", encoding="utf-8"
            )

            out_path = tmp_path / "merchant-map.csv"

            with mock.patch("bootstrap.build_map", wraps=build_map) as spy:
                bootstrap.main([
                    str(history_path),
                    "--out", str(out_path),
                    "--categories", str(categories_path),
                ])

            _, kwargs = spy.call_args
            self.assertEqual(kwargs["known_categories"], {"Grocery", "Dining"})

    def test_without_the_flag_known_categories_stays_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)

            history_path = tmp_path / "history.csv"
            write_ledger(history_path, rows_for("SAFEWAY", "Grocery", 3))

            out_path = tmp_path / "merchant-map.csv"

            with mock.patch("bootstrap.build_map", wraps=build_map) as spy:
                bootstrap.main([str(history_path), "--out", str(out_path)])

            _, kwargs = spy.call_args
            self.assertIsNone(kwargs["known_categories"])


from bootstrap import holdout_coverage


class TestHoldout(unittest.TestCase):
    def test_merchant_seen_in_training_is_counted_correct(self):
        rows = rows_for("SAFEWAY", "Grocery", 10, year="2026", month="06") + \
               rows_for("SAFEWAY", "Grocery", 3, year="2026", month="07")
        result = holdout_coverage(rows, "2026-07")
        self.assertEqual(result["test_rows"], 3)
        self.assertEqual(result["correct"], 3)
        self.assertEqual(result["coverage"], 1.0)

    def test_merchant_absent_from_training_is_not_counted_correct(self):
        rows = rows_for("SAFEWAY", "Grocery", 10, year="2026", month="06") + \
               rows_for("BRAND NEW CAFE", "Dining", 2, year="2026", month="07")
        result = holdout_coverage(rows, "2026-07")
        self.assertEqual(result["correct"], 0)
        self.assertEqual(result["coverage"], 0.0)

    def test_wrong_prediction_is_reported_as_a_miss(self):
        rows = rows_for("SPROUTS", "Grocery", 10, year="2026", month="06") + \
               rows_for("SPROUTS", "Dining", 2, year="2026", month="07")
        result = holdout_coverage(rows, "2026-07")
        self.assertEqual(result["correct"], 0)
        self.assertEqual(result["misses"][0][0], "SPROUTS")

    def test_empty_holdout_month_does_not_divide_by_zero(self):
        rows = rows_for("SAFEWAY", "Grocery", 10, year="2026", month="06")
        result = holdout_coverage(rows, "2026-07")
        self.assertEqual(result["test_rows"], 0)
        self.assertEqual(result["coverage"], 0.0)

    def test_data_after_the_holdout_month_does_not_leak_into_training(self):
        # A holdout month that is not the last one in the file: rows from
        # a later month must not backfill the map, or a merchant that was
        # genuinely unknown at holdout time would look known.
        rows = rows_for("NEWCAFE", "Dining", 5, year="2026", month="08") + \
               rows_for("NEWCAFE", "Dining", 2, year="2026", month="06")
        result = holdout_coverage(rows, "2026-06")
        self.assertEqual(result["test_rows"], 2)
        self.assertEqual(result["correct"], 0)
        self.assertEqual(result["coverage"], 0.0)

    def test_ambiguous_merchants_correct_guess_does_not_count_as_coverage(self):
        # Training data with a genuine split (>=20% share, >=3 occurrences
        # for the runner-up) makes the merchant ambiguous, so the map's
        # exact-tier proposal is flagged for review rather than silent --
        # even though its guess happens to match the holdout row's actual
        # category, it must not count as automated coverage.
        rows = rows_for("COSTCO WHSE", "Grocery", 18, year="2026", month="03") + \
               rows_for("COSTCO WHSE", "Household", 13, year="2026", month="04") + \
               rows_for("COSTCO WHSE", "Grocery", 1, year="2026", month="07")
        result = holdout_coverage(rows, "2026-07")
        self.assertEqual(result["test_rows"], 1)
        self.assertEqual(result["auto_assigned"], 0)
        self.assertEqual(result["correct"], 0)


if __name__ == "__main__":
    unittest.main()
