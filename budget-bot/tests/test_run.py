import sys
import json
import pathlib
import tempfile
import unittest
from datetime import date
from decimal import Decimal

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import run
from dedupe import Emitted
from ledger import Row, read_ledger
from merchant_map import MapEntry, load_map, save_map

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"


def make_row(timestamp="2026-08-04", transaction="SQ *BLUE BOTTLE 1123",
             amount="6.50", account="Test Visa", category="Dining"):
    return Row(timestamp=timestamp, transaction=transaction,
               amount=Decimal(amount), account=account, category=category)


class TestSummarize(unittest.TestCase):
    def test_missing_account_is_surfaced(self):
        summary = run.summarize([make_row()], [], ["Amex"])
        self.assertEqual(summary["missing_accounts"], ["Amex"])

    def test_totals_are_grouped_by_account_and_category(self):
        summary = run.summarize(
            [make_row(amount="6.50"), make_row(amount="10.00", account="Amex",
                                               category="Grocery")],
            [], [])
        self.assertEqual(summary["by_account"]["Test Visa"], Decimal("6.50"))
        self.assertEqual(summary["by_category"]["Grocery"], Decimal("10.00"))
        self.assertEqual(summary["total"], Decimal("16.50"))

    def test_category_that_doubled_against_prior_month_is_flagged(self):
        prior = [Emitted(hash="x", account="Test Visa", timestamp="2026-07-04",
                         amount=Decimal("50.00"), merchant="BLUE BOTTLE",
                         category="Dining")]
        summary = run.summarize([make_row(amount="500.00")], prior, [],
                                today=date(2026, 8, 15))
        self.assertIn("Dining", summary["doubled"])

    def test_category_within_normal_range_is_not_flagged(self):
        prior = [Emitted(hash="x", account="Test Visa", timestamp="2026-07-04",
                         amount=Decimal("50.00"), merchant="BLUE BOTTLE",
                         category="Dining")]
        summary = run.summarize([make_row(amount="55.00")], prior, [],
                                today=date(2026, 8, 15))
        self.assertEqual(summary["doubled"], [])


class TestReviewAndCommit(unittest.TestCase):
    def setUp(self):
        self.state = pathlib.Path(tempfile.mkdtemp())
        self.work = self.state / "work.json"
        self.out = self.state / "out.csv"
        (self.state / "accounts.toml").write_text(
            (FIXTURES / "accounts.toml").read_text())
        save_map(self.state / "merchant-map.csv", {
            "BLUE BOTTLE": MapEntry("BLUE BOTTLE", "Dining", 14, False),
        })

    def test_review_writes_a_work_file_with_every_parsed_row(self):
        run.review(FIXTURES, self.state, self.work)
        payload = json.loads(self.work.read_text())
        self.assertEqual(len(payload["rows"]), 5)
        self.assertTrue(any(r["needs_review"] for r in payload["rows"]))

    def test_commit_refuses_while_a_row_has_no_category(self):
        run.review(FIXTURES, self.state, self.work)
        with self.assertRaises(SystemExit):
            run.commit(self.work, self.state, self.out, overrides={})

    def test_commit_applies_overrides_and_writes_all_three_files(self):
        run.review(FIXTURES, self.state, self.work)
        payload = json.loads(self.work.read_text())
        overrides = {r["n"]: "Household" for r in payload["rows"] if not r["proposed"]}
        run.commit(self.work, self.state, self.out, overrides=overrides)

        written = read_ledger(self.out)
        self.assertEqual(len(written), 5)
        self.assertTrue(all(r.category for r in written))
        self.assertTrue((self.state / "emitted.csv").exists())
        self.assertIn("BLUE BOTTLE", load_map(self.state / "merchant-map.csv"))

    def test_descriptors_survive_the_whole_pipeline_verbatim(self):
        run.review(FIXTURES, self.state, self.work)
        payload = json.loads(self.work.read_text())
        overrides = {r["n"]: "Household" for r in payload["rows"] if not r["proposed"]}
        run.commit(self.work, self.state, self.out, overrides=overrides)
        descriptors = {r.transaction for r in read_ledger(self.out)}
        self.assertIn("SQ *BLUE BOTTLE 1123", descriptors)
        self.assertIn("ACME, INC. #42", descriptors)

    def test_since_bypasses_the_emitted_ledger(self):
        run.review(FIXTURES, self.state, self.work)
        payload = json.loads(self.work.read_text())
        overrides = {r["n"]: "Household" for r in payload["rows"] if not r["proposed"]}
        run.commit(self.work, self.state, self.out, overrides=overrides)

        run.review(FIXTURES, self.state, self.work, since="2026-08-01")
        payload = json.loads(self.work.read_text())
        self.assertEqual(len(payload["rows"]), 5,
                         "--since replaces the ledger rather than adding to it")

    def test_review_records_the_date_range_it_covered(self):
        run.review(FIXTURES, self.state, self.work)
        payload = json.loads(self.work.read_text())
        self.assertEqual(payload["date_range"], ["2026-08-04", "2026-08-11"])

    def test_second_run_of_the_same_files_emits_nothing_new(self):
        run.review(FIXTURES, self.state, self.work)
        payload = json.loads(self.work.read_text())
        overrides = {r["n"]: "Household" for r in payload["rows"] if not r["proposed"]}
        run.commit(self.work, self.state, self.out, overrides=overrides)

        run.review(FIXTURES, self.state, self.work)
        payload = json.loads(self.work.read_text())
        self.assertEqual(payload["rows"], [])
        self.assertEqual(payload["exact_duplicates"], 5)


if __name__ == "__main__":
    unittest.main()
