import sys
import pathlib
import tempfile
import unittest
from decimal import Decimal

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from dedupe import (
    Emitted, append_emitted, load_emitted, partition, row_hash,
)
from ledger import Row


def make_row(timestamp="2026-08-04", transaction="SQ *BLUE BOTTLE 1123",
             amount="6.50", account="Test Visa"):
    return Row(timestamp=timestamp, transaction=transaction,
               amount=Decimal(amount), account=account)


def emitted_for(row):
    return Emitted(hash=row_hash(row), account=row.account,
                   timestamp=row.timestamp, amount=row.amount,
                   merchant="BLUE BOTTLE", category="Dining")


class TestRowHash(unittest.TestCase):
    def test_same_transaction_from_different_store_numbers_hashes_alike(self):
        # Both normalize to BLUE BOTTLE, so a re-download matches.
        a = make_row(transaction="SQ *BLUE BOTTLE 1123")
        b = make_row(transaction="SQ *BLUE BOTTLE 1123")
        self.assertEqual(row_hash(a), row_hash(b))

    def test_different_account_hashes_differently(self):
        self.assertNotEqual(row_hash(make_row()), row_hash(make_row(account="Amex")))


class TestPartition(unittest.TestCase):
    def test_unseen_row_is_new(self):
        result = partition([make_row()], [])
        self.assertEqual(len(result.new), 1)
        self.assertEqual(result.exact_duplicates, [])

    def test_already_emitted_row_is_dropped(self):
        row = make_row()
        result = partition([row], [emitted_for(row)])
        self.assertEqual(result.new, [])
        self.assertEqual(len(result.exact_duplicates), 1)

    def test_two_identical_charges_same_day_are_both_kept(self):
        # Two $4.50 coffees on one day are two real transactions. Only one
        # was previously emitted, so exactly one is new.
        row = make_row(amount="4.50")
        result = partition([make_row(amount="4.50"), make_row(amount="4.50")],
                           [emitted_for(row)])
        self.assertEqual(len(result.new), 1)
        self.assertEqual(len(result.exact_duplicates), 1)

    def test_pending_charge_that_posted_later_is_surfaced_not_dropped(self):
        already = emitted_for(make_row(timestamp="2026-08-30"))
        posted = make_row(timestamp="2026-09-02")
        result = partition([posted], [already])
        self.assertEqual(len(result.near_matches), 1)
        self.assertIn(posted, result.new, "near matches stay in the output until ruled on")

    def test_same_merchant_beyond_the_window_is_not_a_near_match(self):
        already = emitted_for(make_row(timestamp="2026-08-01"))
        result = partition([make_row(timestamp="2026-08-20")], [already])
        self.assertEqual(result.near_matches, [])

    def test_four_days_apart_is_a_near_match(self):
        already = emitted_for(make_row(timestamp="2026-08-01"))
        result = partition([make_row(timestamp="2026-08-05")], [already])
        self.assertEqual(len(result.near_matches), 1)

    def test_five_days_apart_is_not_a_near_match(self):
        already = emitted_for(make_row(timestamp="2026-08-01"))
        result = partition([make_row(timestamp="2026-08-06")], [already])
        self.assertEqual(result.near_matches, [])


class TestEmittedFile(unittest.TestCase):
    def test_append_then_load_round_trips(self):
        tmp = pathlib.Path(tempfile.mkdtemp()) / "emitted.csv"
        append_emitted(tmp, [make_row()])
        append_emitted(tmp, [make_row(timestamp="2026-08-09")])
        back = load_emitted(tmp)
        self.assertEqual(len(back), 2)
        self.assertEqual(back[0].merchant, "BLUE BOTTLE")
        self.assertEqual(back[1].amount, Decimal("6.50"))

    def test_missing_file_loads_as_empty(self):
        self.assertEqual(load_emitted(pathlib.Path("/nonexistent/emitted.csv")), [])

    def test_append_to_empty_file_writes_header_not_just_data(self):
        # A file can exist but be empty (e.g. touched by a prior failed run).
        # Treating "exists" as "has a header" silently loses the row: the
        # data line gets consumed by DictReader as the header on load.
        tmp = pathlib.Path(tempfile.mkdtemp()) / "emitted.csv"
        tmp.touch()
        append_emitted(tmp, [make_row()])
        back = load_emitted(tmp)
        self.assertEqual(len(back), 1)
        self.assertEqual(back[0].merchant, "BLUE BOTTLE")


if __name__ == "__main__":
    unittest.main()
