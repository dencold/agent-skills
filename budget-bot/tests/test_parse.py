import sys
import pathlib
import unittest
from decimal import Decimal

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from parse import (
    Account, AmbiguousAccountError, MalformedRowError, UnknownFileError,
    load_accounts, match_account, parse_file, parse_folder, _to_decimal,
)

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"


class TestLoadAccounts(unittest.TestCase):
    def test_loads_both_accounts_with_their_sign_conventions(self):
        accounts = load_accounts(FIXTURES / "accounts.toml")
        self.assertEqual([a.name for a in accounts], ["Test Visa", "Test Checking"])
        self.assertEqual(accounts[0].amount_sign, "negative_is_charge")
        self.assertEqual(accounts[1].amount_sign, "positive_is_charge")


class TestMatchAccount(unittest.TestCase):
    def setUp(self):
        self.accounts = load_accounts(FIXTURES / "accounts.toml")

    def test_matches_by_header_signature(self):
        header = ["Transaction Date", "Post Date", "Description", "Amount"]
        self.assertEqual(match_account(header, "whatever.csv", self.accounts).name, "Test Visa")

    def test_unknown_header_raises_with_the_header_attached(self):
        with self.assertRaises(UnknownFileError) as ctx:
            match_account(["Nope", "Wrong"], "mystery.csv", self.accounts)
        self.assertEqual(ctx.exception.header, ["Nope", "Wrong"])
        self.assertEqual(ctx.exception.filename, "mystery.csv")

    def test_two_accounts_sharing_a_format_are_split_by_filename_hint(self):
        shared = ["Transaction Date", "Post Date", "Description", "Amount"]
        base = self.accounts[0]
        sapphire = Account(**{**base.__dict__, "name": "Sapphire", "filename_hint": "sapphire"})
        freedom = Account(**{**base.__dict__, "name": "Freedom", "filename_hint": "freedom"})
        matched = match_account(shared, "Chase_Freedom_0831.CSV", [sapphire, freedom])
        self.assertEqual(matched.name, "Freedom")

    def test_two_accounts_sharing_a_format_without_hints_raise(self):
        base = self.accounts[0]
        a = Account(**{**base.__dict__, "name": "Card A"})
        b = Account(**{**base.__dict__, "name": "Card B"})
        with self.assertRaises(AmbiguousAccountError) as ctx:
            match_account(base.signature, "export.csv", [a, b])
        self.assertEqual(sorted(ctx.exception.candidates), ["Card A", "Card B"])


class TestParseFile(unittest.TestCase):
    def setUp(self):
        self.accounts = load_accounts(FIXTURES / "accounts.toml")

    def test_descriptor_is_byte_identical_to_the_source(self):
        rows = parse_file(FIXTURES / "negative_is_charge.csv", self.accounts[0])
        self.assertEqual(rows[0].transaction, "SQ *BLUE BOTTLE 1123")
        self.assertEqual(rows[1].transaction, "ACME, INC. #42")

    def test_dates_normalized_to_iso(self):
        rows = parse_file(FIXTURES / "negative_is_charge.csv", self.accounts[0])
        self.assertEqual(rows[0].timestamp, "2026-08-04")

    def test_negative_is_charge_flips_to_charges_positive(self):
        rows = parse_file(FIXTURES / "negative_is_charge.csv", self.accounts[0])
        self.assertEqual(rows[0].amount, Decimal("6.50"))
        self.assertEqual(rows[2].amount, Decimal("-500.00"))

    def test_positive_is_charge_passes_through(self):
        rows = parse_file(FIXTURES / "positive_is_charge.csv", self.accounts[1])
        self.assertEqual(rows[0].amount, Decimal("14.25"))
        self.assertEqual(rows[1].amount, Decimal("-3200.00"))

    def test_notes_and_category_left_empty_by_the_parser(self):
        rows = parse_file(FIXTURES / "negative_is_charge.csv", self.accounts[0])
        self.assertTrue(all(r.notes == "" and r.category == "" for r in rows))

    def test_account_name_stamped_on_every_row(self):
        rows = parse_file(FIXTURES / "negative_is_charge.csv", self.accounts[0])
        self.assertTrue(all(r.account == "Test Visa" for r in rows))

    def test_malformed_date_halts_with_the_line_number(self):
        bad = FIXTURES.parent / "bad.csv"
        bad.write_text("Transaction Date,Post Date,Description,Amount\n"
                       "not-a-date,08/05/2026,COFFEE,-6.50\n")
        try:
            with self.assertRaises(MalformedRowError) as ctx:
                parse_file(bad, self.accounts[0])
            self.assertEqual(ctx.exception.line_number, 2)
        finally:
            bad.unlink()

    def test_blank_amount_halts_with_the_line_number(self):
        bad = FIXTURES.parent / "bad_amount.csv"
        bad.write_text("Transaction Date,Post Date,Description,Amount\n"
                       "08/04/2026,08/05/2026,COFFEE,\n")
        try:
            with self.assertRaises(MalformedRowError) as ctx:
                parse_file(bad, self.accounts[0])
            self.assertEqual(ctx.exception.line_number, 2)
        finally:
            bad.unlink()

    def test_parenthesized_negative_flips_through_negative_is_charge(self):
        paren = FIXTURES.parent / "paren_amount.csv"
        paren.write_text("Transaction Date,Post Date,Description,Amount\n"
                          "08/04/2026,08/05/2026,COFFEE,(6.50)\n")
        try:
            rows = parse_file(paren, self.accounts[0])
            self.assertEqual(rows[0].amount, Decimal("6.50"))
        finally:
            paren.unlink()


class TestToDecimal(unittest.TestCase):
    def test_strips_dollar_sign(self):
        self.assertEqual(_to_decimal("$14.25"), Decimal("14.25"))

    def test_strips_thousands_comma(self):
        self.assertEqual(_to_decimal("1,234.56"), Decimal("1234.56"))

    def test_parses_parenthesized_negative(self):
        self.assertEqual(_to_decimal("(6.50)"), Decimal("-6.50"))

    def test_empty_string_raises(self):
        with self.assertRaises(ValueError):
            _to_decimal("")


class TestParseFolder(unittest.TestCase):
    def test_reports_accounts_that_matched_no_file(self):
        accounts = load_accounts(FIXTURES / "accounts.toml")
        ghost = Account(**{**accounts[0].__dict__, "name": "Missing Card",
                           "signature": ["Totally", "Different"]})
        rows, missing = parse_folder(FIXTURES, accounts + [ghost])
        self.assertIn("Missing Card", missing)
        self.assertEqual(len(rows), 5)


if __name__ == "__main__":
    unittest.main()
