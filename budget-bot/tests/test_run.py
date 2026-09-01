import contextlib
import io
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


def _quiet(func, *args, **kwargs):
    """Call an entry point without letting its stdout/stderr reach the runner.

    review(), commit(), and main() all print progress and warnings for a
    human running the tool interactively -- exactly what a test suite
    should not show. Redirecting keeps `unittest`'s dot-per-test output
    pristine; a test whose whole point is the printed text should capture
    and assert on it instead of discarding it.
    """
    with contextlib.redirect_stdout(io.StringIO()), \
         contextlib.redirect_stderr(io.StringIO()):
        return func(*args, **kwargs)


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


class TestParseOverrides(unittest.TestCase):
    def test_rejects_a_pair_missing_the_equals_sign(self):
        with self.assertRaises(run.OverrideError):
            run._parse_overrides(["3Household"])

    def test_rejects_a_non_numeric_row_number(self):
        with self.assertRaises(run.OverrideError):
            run._parse_overrides(["abc=Household"])

    def test_accepts_a_well_formed_pair(self):
        self.assertEqual(run._parse_overrides(["3=Household"]), {3: "Household"})


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
        _quiet(run.review, FIXTURES, self.state, self.work)
        payload = json.loads(self.work.read_text())
        self.assertEqual(len(payload["rows"]), 5)
        self.assertTrue(any(r["needs_review"] for r in payload["rows"]))

    def test_commit_refuses_while_a_row_has_no_category(self):
        _quiet(run.review, FIXTURES, self.state, self.work)
        with self.assertRaises(SystemExit):
            _quiet(run.commit, self.work, self.state, self.out, overrides={})

    def test_commit_refuses_an_override_for_a_row_number_that_does_not_exist(self):
        _quiet(run.review, FIXTURES, self.state, self.work)
        payload = json.loads(self.work.read_text())
        overrides = {r["n"]: "Household" for r in payload["rows"] if not r["proposed"]}
        # Row 999 was never in the work file -- a mistyped or stale
        # correction must be refused, never silently dropped in favor of
        # the tool's own proposal.
        overrides[999] = "Gifts"

        with self.assertRaises(SystemExit):
            _quiet(run.commit, self.work, self.state, self.out, overrides=overrides)

        self.assertFalse(self.out.exists(),
                         "commit must refuse before writing anything")
        self.assertFalse((self.state / "emitted.csv").exists())

    def test_commit_applies_overrides_and_writes_all_three_files(self):
        _quiet(run.review, FIXTURES, self.state, self.work)
        payload = json.loads(self.work.read_text())
        overrides = {r["n"]: "Household" for r in payload["rows"] if not r["proposed"]}
        _quiet(run.commit, self.work, self.state, self.out, overrides=overrides)

        written = read_ledger(self.out)
        self.assertEqual(len(written), 5)
        self.assertTrue(all(r.category for r in written))
        self.assertTrue((self.state / "emitted.csv").exists())
        self.assertIn("BLUE BOTTLE", load_map(self.state / "merchant-map.csv"))

    def test_descriptors_survive_the_whole_pipeline_verbatim(self):
        _quiet(run.review, FIXTURES, self.state, self.work)
        payload = json.loads(self.work.read_text())
        overrides = {r["n"]: "Household" for r in payload["rows"] if not r["proposed"]}
        _quiet(run.commit, self.work, self.state, self.out, overrides=overrides)
        descriptors = {r.transaction for r in read_ledger(self.out)}
        self.assertIn("SQ *BLUE BOTTLE 1123", descriptors)
        self.assertIn("ACME, INC. #42", descriptors)

    def test_since_bypasses_the_emitted_ledger(self):
        _quiet(run.review, FIXTURES, self.state, self.work)
        payload = json.loads(self.work.read_text())
        overrides = {r["n"]: "Household" for r in payload["rows"] if not r["proposed"]}
        _quiet(run.commit, self.work, self.state, self.out, overrides=overrides)

        _quiet(run.review, FIXTURES, self.state, self.work, since="2026-08-01")
        payload = json.loads(self.work.read_text())
        self.assertEqual(len(payload["rows"]), 5,
                         "--since replaces the ledger rather than adding to it")

    def test_review_records_the_date_range_it_covered(self):
        _quiet(run.review, FIXTURES, self.state, self.work)
        payload = json.loads(self.work.read_text())
        self.assertEqual(payload["date_range"], ["2026-08-04", "2026-08-11"])

    def test_second_run_of_the_same_files_emits_nothing_new(self):
        _quiet(run.review, FIXTURES, self.state, self.work)
        payload = json.loads(self.work.read_text())
        overrides = {r["n"]: "Household" for r in payload["rows"] if not r["proposed"]}
        _quiet(run.commit, self.work, self.state, self.out, overrides=overrides)

        _quiet(run.review, FIXTURES, self.state, self.work)
        payload = json.loads(self.work.read_text())
        self.assertEqual(payload["rows"], [])
        self.assertEqual(payload["exact_duplicates"], 5)

    def test_main_reports_a_malformed_set_argument_without_a_traceback(self):
        # Anything other than SystemExit-via-argparse escaping main() would
        # mean the user sees a raw Python traceback for a typo.
        code = _quiet(run.main, [
            "commit", "--work", str(self.work), "--state", str(self.state),
            "--out", str(self.out), "--set", "abcHousehold",
        ])
        self.assertEqual(code, 1)

    def test_near_match_surfaces_possible_duplicate_through_review(self):
        # A genuine near-match end to end: the same merchant and amount,
        # a few days apart, on an account this suite configures itself so
        # the shared fixtures (asserted on exactly elsewhere) stay untouched.
        first_drop = pathlib.Path(tempfile.mkdtemp())
        (first_drop / "export.csv").write_text(
            "Transaction Date,Post Date,Description,Amount\n"
            "08/04/2026,08/05/2026,PENDING COFFEE SHOP,-6.50\n"
        )
        _quiet(run.review, first_drop, self.state, self.work)
        payload = json.loads(self.work.read_text())
        overrides = {r["n"]: "Dining" for r in payload["rows"]}
        _quiet(run.commit, self.work, self.state, self.out, overrides=overrides)

        second_drop = pathlib.Path(tempfile.mkdtemp())
        (second_drop / "export.csv").write_text(
            "Transaction Date,Post Date,Description,Amount\n"
            "08/07/2026,08/08/2026,PENDING COFFEE SHOP,-6.50\n"
        )
        _quiet(run.review, second_drop, self.state, self.work)
        payload = json.loads(self.work.read_text())

        self.assertEqual(len(payload["rows"]), 1)
        row = payload["rows"][0]
        self.assertEqual(row["possible_duplicate_of"], "2026-08-04")
        self.assertTrue(row["needs_review"])


if __name__ == "__main__":
    unittest.main()
