"""The canonical ledger row and its CSV form.

One schema, defined once, shared by the parser (which produces rows), the
bootstrap (which reads a year of them out of an exported sheet), and the
monthly run (which writes them back). Money is Decimal throughout: a
float ledger disagrees with statements by cents, and a budget that does
not reconcile is worse than no budget.
"""

import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

COLUMNS = ["Timestamp", "Transaction", "Notes", "Amount", "Category", "Account"]


class LedgerError(ValueError):
    """A file that is not a readable ledger export."""


def parse_money(raw):
    """Parse a money string into Decimal. The one money parser in the project.

    Handles `$`, thousands commas, and the parenthesized negatives
    spreadsheets emit. Both readers of financial input -- the bank-export
    parser and the sheet reader -- call this, so `(42.10)` cannot mean
    -42.10 in one file and a crash in the other.

    An empty field raises rather than defaulting to zero: a blank amount
    cell is a malformed row, not a $0 transaction. Callers attach the file
    and line number.
    """
    text = (raw or "").strip().replace("$", "").replace(",", "")
    if not text:
        raise ValueError("empty amount")
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise ValueError(f"{raw!r} is not an amount") from None
    return -value if negative else value


@dataclass(kw_only=True)
class Row:
    """One transaction in the sheet's schema.

    Keyword-only construction prevents a silent field mismatch: the dataclass
    field order (timestamp, transaction, amount, account, category, notes)
    cannot match COLUMNS (Timestamp, Transaction, Notes, Amount, Category,
    Account) because Python requires fields with defaults to trail. Positional
    construction would silently land values in the wrong fields and corrupt
    money data. Keywords make that impossible.

    `transaction` is the bank's descriptor verbatim -- never normalized.
    `notes` is the user's own annotation column and is always written empty.
    """

    timestamp: str          # YYYY-MM-DD
    transaction: str        # verbatim source descriptor
    amount: Decimal         # charges positive, income negative
    account: str
    category: str = ""
    notes: str = ""

    def sort_key(self):
        return (self.timestamp, self.account)


def _require_schema(path, fieldnames):
    """Refuse anything that is not the sheet schema, naming what is absent.

    This is the bootstrap's first read of a file the user exported by hand.
    Without the check a wrong export is accepted, its missing columns become
    empty strings, and the run dies several modules later with a bare
    "descriptor is required" naming neither the file nor the row.
    """
    present = {(name or "").strip() for name in (fieldnames or [])}
    missing = [column for column in COLUMNS if column not in present]
    if missing:
        raise LedgerError(
            f"{path}: not a ledger export -- missing column(s) "
            f"{', '.join(missing)}.\n"
            f"  Expected header: {', '.join(COLUMNS)}\n"
            f"  Found: {', '.join(fieldnames) if fieldnames else '(empty file)'}")


def read_ledger(path):
    """Read a CSV in the sheet schema. Amounts become Decimal."""
    rows = []
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        _require_schema(path, reader.fieldnames)
        for line_number, record in enumerate(reader, start=2):
            try:
                amount = parse_money(record.get("Amount"))
            except ValueError as exc:
                raise LedgerError(f"{path}:{line_number}: bad amount: {exc}") from exc
            rows.append(Row(
                timestamp=(record.get("Timestamp") or "").strip(),
                transaction=record.get("Transaction") or "",
                notes=record.get("Notes") or "",
                amount=amount,
                category=(record.get("Category") or "").strip(),
                account=(record.get("Account") or "").strip(),
            ))
    return rows


def write_ledger(path, rows):
    """Write rows in the sheet schema, sorted by timestamp then account."""
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for row in sorted(rows, key=Row.sort_key):
            writer.writerow([
                row.timestamp, row.transaction, row.notes,
                f"{row.amount:.2f}", row.category, row.account,
            ])
