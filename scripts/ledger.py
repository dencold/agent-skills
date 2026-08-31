"""The canonical ledger row and its CSV form.

One schema, defined once, shared by the parser (which produces rows), the
bootstrap (which reads a year of them out of an exported sheet), and the
monthly run (which writes them back). Money is Decimal throughout: a
float ledger disagrees with statements by cents, and a budget that does
not reconcile is worse than no budget.
"""

import csv
from dataclasses import dataclass
from decimal import Decimal

COLUMNS = ["Timestamp", "Transaction", "Notes", "Amount", "Category", "Account"]


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


def read_ledger(path):
    """Read a CSV in the sheet schema. Amounts become Decimal."""
    rows = []
    with open(path, newline="", encoding="utf-8") as handle:
        for record in csv.DictReader(handle):
            raw = (record.get("Amount") or "").strip().replace("$", "").replace(",", "")
            rows.append(Row(
                timestamp=(record.get("Timestamp") or "").strip(),
                transaction=record.get("Transaction") or "",
                notes=record.get("Notes") or "",
                amount=Decimal(raw) if raw else Decimal("0"),
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
