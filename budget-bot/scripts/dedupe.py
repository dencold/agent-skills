"""Suppress transactions already exported in an earlier run.

Bank CSVs carry no stable transaction id, so identity is the composite of
account, date, amount, and normalized merchant. Two $4.50 coffees at one
shop on one day are two real transactions, so occurrences are counted
rather than merely tested for presence.

Exact repeats are dropped. Anything close but not identical -- a pending
charge that posted four days after it was first downloaded -- is surfaced
for the user to rule on and stays in the output meanwhile. Silently
deleting a real transaction is the worst thing this pipeline could do; a
duplicate shows up in a category total, a missing row does not.
"""

import csv
import hashlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from normalize import normalize_merchant

NEAR_MATCH_DAYS = 4

EMITTED_COLUMNS = ["hash", "account", "timestamp", "amount", "merchant", "category"]


@dataclass
class Emitted:
    hash: str
    account: str
    timestamp: str
    amount: Decimal
    merchant: str
    # Not part of identity. Carried so the monthly run can compare
    # category totals against the previous month.
    category: str = ""


@dataclass
class DedupeResult:
    new: list = field(default_factory=list)
    exact_duplicates: list = field(default_factory=list)
    near_matches: list = field(default_factory=list)


def row_hash(row):
    """Stable identity for a transaction across re-downloads."""
    merchant = normalize_merchant(row.transaction)
    payload = f"{row.account}|{row.timestamp}|{row.amount:.2f}|{merchant}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _days_apart(a, b):
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)


def partition(rows, emitted):
    """Split rows into new, already-exported, and possible duplicates."""
    remaining = Counter(e.hash for e in emitted)
    result = DedupeResult()

    for row in rows:
        digest = row_hash(row)
        if remaining[digest] > 0:
            remaining[digest] -= 1
            result.exact_duplicates.append(row)
            continue

        result.new.append(row)

        merchant = normalize_merchant(row.transaction)
        for previous in emitted:
            if (previous.account == row.account
                    and previous.amount == row.amount
                    and previous.merchant == merchant
                    and _days_apart(previous.timestamp, row.timestamp) <= NEAR_MATCH_DAYS
                    and previous.timestamp != row.timestamp):
                result.near_matches.append((row, previous))
                break

    return result


def load_emitted(path):
    """Read the emitted ledger. A missing file is an empty history."""
    try:
        handle = open(path, newline="", encoding="utf-8")
    except FileNotFoundError:
        return []

    with handle:
        return [
            Emitted(
                hash=record["hash"],
                account=record["account"],
                timestamp=record["timestamp"],
                amount=Decimal(record["amount"]),
                merchant=record["merchant"],
                category=record.get("category") or "",
            )
            for record in csv.DictReader(handle)
        ]


def append_emitted(path, rows):
    """Record rows as exported. Called only after the output CSV is written."""
    exists = path.exists()
    with open(path, "a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if not exists:
            writer.writerow(EMITTED_COLUMNS)
        for row in rows:
            writer.writerow([
                row_hash(row), row.account, row.timestamp,
                f"{row.amount:.2f}", normalize_merchant(row.transaction),
                row.category,
            ])
