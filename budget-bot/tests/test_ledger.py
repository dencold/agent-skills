import sys
import pathlib
import tempfile
import unittest
from decimal import Decimal

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from ledger import COLUMNS, LedgerError, Row, parse_money, read_ledger, write_ledger


def make_row(timestamp="2026-08-04", transaction="SQ *BLUE BOTTLE 1123",
             amount="6.50", category="Dining", account="Chase Sapphire"):
    return Row(timestamp=timestamp, transaction=transaction, notes="",
               amount=Decimal(amount), category=category, account=account)


class TestLedgerRoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()) / "out.csv"

    def test_columns_are_exactly_the_sheet_schema(self):
        self.assertEqual(
            COLUMNS,
            ["Timestamp", "Transaction", "Notes", "Amount", "Category", "Account"],
        )

    def test_round_trip_preserves_values(self):
        write_ledger(self.tmp, [make_row()])
        back = read_ledger(self.tmp)
        self.assertEqual(len(back), 1)
        self.assertEqual(back[0].transaction, "SQ *BLUE BOTTLE 1123")
        self.assertEqual(back[0].amount, Decimal("6.50"))

    def test_amount_is_decimal_not_float(self):
        # 0.10 + 0.20 is 0.30000000000000004 in float.
        write_ledger(self.tmp, [make_row(amount="0.10"), make_row(amount="0.20")])
        back = read_ledger(self.tmp)
        self.assertEqual(sum(r.amount for r in back), Decimal("0.30"))

    def test_notes_column_written_empty(self):
        write_ledger(self.tmp, [make_row()])
        text = self.tmp.read_text()
        self.assertIn(",,", text)
        self.assertEqual(read_ledger(self.tmp)[0].notes, "")

    def test_rows_sorted_by_timestamp_then_account(self):
        write_ledger(self.tmp, [
            make_row(timestamp="2026-08-09", account="Amex"),
            make_row(timestamp="2026-08-04", account="Visa"),
            make_row(timestamp="2026-08-04", account="Amex"),
        ])
        back = read_ledger(self.tmp)
        self.assertEqual(
            [(r.timestamp, r.account) for r in back],
            [("2026-08-04", "Amex"), ("2026-08-04", "Visa"), ("2026-08-09", "Amex")],
        )

    def test_descriptor_with_comma_survives_round_trip(self):
        write_ledger(self.tmp, [make_row(transaction='ACME, INC. #42')])
        self.assertEqual(read_ledger(self.tmp)[0].transaction, 'ACME, INC. #42')


class TestParseMoney(unittest.TestCase):
    """The one money parser, shared by the sheet reader and the bank parser."""

    def test_strips_dollar_sign(self):
        self.assertEqual(parse_money("$14.25"), Decimal("14.25"))

    def test_strips_thousands_comma(self):
        self.assertEqual(parse_money("1,234.56"), Decimal("1234.56"))

    def test_parses_parenthesized_negative(self):
        self.assertEqual(parse_money("(6.50)"), Decimal("-6.50"))

    def test_empty_string_raises(self):
        with self.assertRaises(ValueError):
            parse_money("")

    def test_nonsense_raises_rather_than_leaking_invalid_operation(self):
        with self.assertRaises(ValueError):
            parse_money("twelve dollars")


class TestReadLedgerValidation(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()) / "history.csv"

    def test_wrong_header_names_the_file_and_the_missing_columns(self):
        self.tmp.write_text("Date,Description,Value\n2026-08-04,COFFEE,6.50\n")
        with self.assertRaises(LedgerError) as ctx:
            read_ledger(self.tmp)
        message = str(ctx.exception)
        self.assertIn(str(self.tmp), message)
        self.assertIn("Timestamp", message)
        self.assertIn("Amount", message)

    def test_blank_amount_raises_with_the_line_number(self):
        self.tmp.write_text(
            "Timestamp,Transaction,Notes,Amount,Category,Account\n"
            "2026-08-04,COFFEE,,,Dining,Visa\n")
        with self.assertRaises(LedgerError) as ctx:
            read_ledger(self.tmp)
        self.assertIn(":2", str(ctx.exception))

    def test_parenthesized_amount_matches_the_bank_parser(self):
        self.tmp.write_text(
            "Timestamp,Transaction,Notes,Amount,Category,Account\n"
            "2026-08-04,COFFEE,,(42.10),Dining,Visa\n")
        self.assertEqual(read_ledger(self.tmp)[0].amount, Decimal("-42.10"))


if __name__ == "__main__":
    unittest.main()
