# Budget Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `budget-bot` skill: eight per-account CSV exports in, one normalized and categorized ledger out, with Claude adjudicating only the transactions that are genuinely new or ambiguous.

**Architecture:** Deterministic work is tested Python with no model in the loop — parsing eight CSV dialects, normalizing signs and dates, hashing for dedupe, and applying a learned merchant→category map. Claude's only role is adjudicating the unresolved tail and running the review conversation. State (account configs, merchant map, emitted-row ledger) lives in `~/.claude/budget-bot/`, never in this public repo.

**Tech Stack:** Python 3.11+, standard library only (`csv`, `tomllib`, `decimal`, `hashlib`, `dataclasses`, `re`). Tests are `unittest`. Install script is zsh. No third-party dependencies.

**Spec:** `docs/superpowers/specs/2026-08-30-budget-bot-design.md`

## Global Constraints

Every task's requirements implicitly include this section.

- **Standard library only.** No pip installs, no `requirements.txt`. `lodging-scout` sets this precedent and `tomllib` (3.11+) and `decimal` cover everything needed.
- **Tests are `unittest`, not pytest**, matching `lodging-scout/tests/`. Every test module begins with the same three-line `sys.path` shim so `scripts/` is importable.
- **Run tests from the skill directory:** `cd budget-bot && python3 -m unittest discover -s tests`
- **All money is `decimal.Decimal`.** Never `float`. Parse with `Decimal(str_value)`, never `Decimal(float_value)`. A float ledger will disagree with statements by cents and destroy trust in the tool.
- **Descriptors are copied byte-for-byte.** The `Transaction` column carries the exact source string. The normalized form is an in-memory lookup key only. This is a hard spec requirement with a dedicated test.
- **Output convention:** charges positive, income negative, dates `YYYY-MM-DD`, `Notes` always empty.
- **Never commit financial data.** All fixtures are hand-written and anonymized. Real state lives in `~/.claude/budget-bot/`.
- **Commits:** imperative mood ("Add merchant normalization"), no AI-attribution or `Co-authored-by` trailers.

## File Structure

The spec sketches "four scripts". That sketch has no home for dedupe (yet lists `tests/test_dedupe.py`), no shared row type, and no orchestration — which would push deterministic glue into `SKILL.md`, i.e. into the model. This plan splits into eight focused modules. No behavior is added beyond the spec.

| File | Responsibility |
|---|---|
| `scripts/normalize.py` | Descriptor → merchant lookup key. Pure, no I/O. The keystone. |
| `scripts/ledger.py` | The canonical `Row` type and schema CSV read/write |
| `scripts/parse.py` | `accounts.toml` loading, header-signature routing, column mapping |
| `scripts/merchant_map.py` | `merchant-map.csv` load/save, ambiguity flag transitions |
| `scripts/dedupe.py` | `emitted.csv`, exact-match drop, ±4 day near-match surfacing |
| `scripts/categorize.py` | The three matching tiers |
| `scripts/bootstrap.py` | History → map, recency rule, dirty-data report, holdout validation |
| `scripts/run.py` | Two-phase CLI orchestration (`review`, then `commit`) |
| `SKILL.md` | The review conversation and when to call which script |

Dependency order is strictly one-way: `normalize` ← `ledger` ← everything else. Nothing imports `run.py`.

---

### Task 1: Project scaffold and merchant normalization

The keystone. If `bootstrap.py` and `categorize.py` normalize a string differently, every key misses and the map is worthless — so this lands first, alone, with the heaviest test coverage in the project.

**Files:**
- Create: `budget-bot/scripts/normalize.py`
- Create: `budget-bot/tests/test_normalize.py`

**Interfaces:**
- Consumes: nothing
- Produces: `normalize_merchant(descriptor: str) -> str`; constants `PROCESSOR_PREFIXES: tuple[str, ...]`

- [ ] **Step 1: Write the failing test**

Create `budget-bot/tests/test_normalize.py`:

```python
import sys
import pathlib
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from normalize import normalize_merchant


class TestNormalizeMerchant(unittest.TestCase):
    def test_strips_square_prefix_and_trailing_store_id(self):
        self.assertEqual(normalize_merchant("SQ *BLUE BOTTLE 1123"), "BLUE BOTTLE")

    def test_strips_toast_prefix(self):
        self.assertEqual(normalize_merchant("TST* CHIPOTLE 0455"), "CHIPOTLE")

    def test_strips_amazon_order_id_and_state_token(self):
        self.assertEqual(normalize_merchant("AMZN Mktp US*2H4KL9DJ3"), "AMZN MKTP")

    def test_uppercases_and_collapses_whitespace(self):
        self.assertEqual(normalize_merchant("  Blue   Bottle  "), "BLUE BOTTLE")

    def test_is_idempotent(self):
        once = normalize_merchant("SQ *BLUE BOTTLE 1123")
        self.assertEqual(normalize_merchant(once), once)

    def test_all_noise_descriptor_is_preserved_not_emptied(self):
        # 7-ELEVEN is entirely "noise" by the token rule. Returning "" would
        # collapse every such merchant into one key.
        self.assertEqual(normalize_merchant("7-ELEVEN"), "7-ELEVEN")

    def test_distinct_merchants_do_not_collide(self):
        self.assertNotEqual(
            normalize_merchant("SAFEWAY 1842"),
            normalize_merchant("SAFECO INSURANCE"),
        )

    def test_empty_descriptor_raises(self):
        with self.assertRaises(ValueError):
            normalize_merchant("")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd budget-bot && python3 -m unittest discover -s tests
```

Expected: `ModuleNotFoundError: No module named 'normalize'`

- [ ] **Step 3: Write the implementation**

Create `budget-bot/scripts/normalize.py`:

```python
"""Descriptor to merchant lookup key for the budget-bot skill.

Bank descriptors carry per-transaction noise -- store numbers, order ids,
payment-processor prefixes -- that makes every purchase look novel. This
module collapses that noise so a merchant seen twenty times is one key.

The result NEVER reaches the output CSV. It is an in-memory lookup key
for merchant-map.csv and dedupe hashes only; the ledger keeps the bank's
exact descriptor so the sheet reconciles against statements.

Both bootstrap.py and categorize.py import this. If they ever normalized
differently, every map key would miss -- which is why there is one
implementation and it is tested harder than anything else here.
"""

import re

PROCESSOR_PREFIXES = ("SQ *", "TST*", "PY *", "SP ", "PAYPAL *", "IN *")

_SPLIT = re.compile(r"[\s*]+")
_HAS_DIGIT = re.compile(r"\d")


def _is_noise(token):
    """Trailing tokens that identify a transaction rather than a merchant.

    Store numbers and order ids contain digits. Two-character trailing
    tokens are state or country codes ("AMZN MKTP US"). Both vary between
    visits to the same merchant.
    """
    return bool(_HAS_DIGIT.search(token)) or len(token) <= 2


def normalize_merchant(descriptor):
    """Collapse a bank descriptor to a stable merchant key."""
    if not descriptor or not descriptor.strip():
        raise ValueError("descriptor is required")

    key = descriptor.strip().upper()

    for prefix in PROCESSOR_PREFIXES:
        if key.startswith(prefix):
            key = key[len(prefix):].strip()
            break

    tokens = [t for t in _SPLIT.split(key) if t]
    trimmed = list(tokens)
    while len(trimmed) > 1 and _is_noise(trimmed[-1]):
        trimmed.pop()

    # Every token was noise ("7-ELEVEN", "76"). Stripping to empty would
    # collapse unrelated merchants into a single key.
    if not trimmed or all(_is_noise(t) for t in trimmed):
        return " ".join(tokens)

    return " ".join(trimmed)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd budget-bot && python3 -m unittest discover -s tests
```

Expected: `Ran 8 tests` / `OK`

- [ ] **Step 5: Commit**

```bash
git add budget-bot/scripts/normalize.py budget-bot/tests/test_normalize.py
git commit -m "Add merchant descriptor normalization"
```

---

### Task 2: The ledger row type and schema CSV I/O

**Files:**
- Create: `budget-bot/scripts/ledger.py`
- Create: `budget-bot/tests/test_ledger.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `COLUMNS: list[str]` — `["Timestamp", "Transaction", "Notes", "Amount", "Category", "Account"]`
  - `Row` dataclass with fields `timestamp: str`, `transaction: str`, `notes: str`, `amount: Decimal`, `category: str`, `account: str`
  - `read_ledger(path: pathlib.Path) -> list[Row]`
  - `write_ledger(path: pathlib.Path, rows: list[Row]) -> None` — sorts by `(timestamp, account)`
  - `Row.sort_key() -> tuple[str, str]`

- [ ] **Step 1: Write the failing test**

Create `budget-bot/tests/test_ledger.py`:

```python
import sys
import pathlib
import tempfile
import unittest
from decimal import Decimal

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from ledger import COLUMNS, Row, read_ledger, write_ledger


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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd budget-bot && python3 -m unittest discover -s tests
```

Expected: `ModuleNotFoundError: No module named 'ledger'`

- [ ] **Step 3: Write the implementation**

Create `budget-bot/scripts/ledger.py`:

```python
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


@dataclass
class Row:
    """One transaction in the sheet's schema.

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
```

Note the dataclass field order differs from `COLUMNS`: fields with defaults (`category`, `notes`) must come last in Python. Construct with keywords everywhere.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd budget-bot && python3 -m unittest discover -s tests
```

Expected: `Ran 14 tests` / `OK`

- [ ] **Step 5: Commit**

```bash
git add budget-bot/scripts/ledger.py budget-bot/tests/test_ledger.py
git commit -m "Add ledger row type and schema CSV round trip"
```

---
### Task 3: Account config, header-signature routing, and parsing

**Spec refinement — two accounts can share a header format.** Eight accounts across fewer than eight institutions means two Chase cards will export byte-identical headers, and a signature alone cannot tell them apart. This task adds an optional `filename_hint` tiebreaker and raises `AmbiguousAccountError` when a file still matches two accounts. Guessing here would silently attribute a card's spending to the wrong account.

**Files:**
- Create: `budget-bot/scripts/parse.py`
- Create: `budget-bot/tests/test_parse.py`
- Create: `budget-bot/tests/fixtures/negative_is_charge.csv`
- Create: `budget-bot/tests/fixtures/positive_is_charge.csv`
- Create: `budget-bot/tests/fixtures/accounts.toml`

**Interfaces:**
- Consumes: `ledger.Row`
- Produces:
  - `Account` dataclass: `name`, `signature: list[str]`, `date_column`, `date_format`, `description`, `amount_column`, `amount_sign`, `filename_hint: str = ""`
  - `ParseError`, `UnknownFileError` (attrs `filename`, `header`), `AmbiguousAccountError` (attrs `filename`, `candidates`), `MalformedRowError` (attrs `filename`, `line_number`, `reason`)
  - `load_accounts(path) -> list[Account]`
  - `match_account(header: list[str], filename: str, accounts) -> Account`
  - `parse_file(path, account) -> list[Row]`
  - `parse_folder(folder, accounts) -> tuple[list[Row], list[str]]` — rows, and names of accounts that matched no file

- [ ] **Step 1: Write the fixtures**

Create `budget-bot/tests/fixtures/negative_is_charge.csv` (hand-written, anonymized):

```csv
Transaction Date,Post Date,Description,Amount
08/04/2026,08/05/2026,SQ *BLUE BOTTLE 1123,-6.50
08/09/2026,08/10/2026,"ACME, INC. #42",-120.00
08/11/2026,08/12/2026,PAYMENT THANK YOU,500.00
```

Create `budget-bot/tests/fixtures/positive_is_charge.csv`:

```csv
Date,Merchant,Debit,Memo
2026-08-04,TST* CHIPOTLE 0455,14.25,
2026-08-06,PAYROLL DEPOSIT,-3200.00,
```

Create `budget-bot/tests/fixtures/accounts.toml`:

```toml
[[account]]
name          = "Test Visa"
signature     = ["Transaction Date", "Post Date", "Description", "Amount"]
date_column   = "Transaction Date"
date_format   = "%m/%d/%Y"
description   = "Description"
amount_column = "Amount"
amount_sign   = "negative_is_charge"

[[account]]
name          = "Test Checking"
signature     = ["Date", "Merchant", "Debit", "Memo"]
date_column   = "Date"
date_format   = "%Y-%m-%d"
description   = "Merchant"
amount_column = "Debit"
amount_sign   = "positive_is_charge"
```

- [ ] **Step 2: Write the failing test**

Create `budget-bot/tests/test_parse.py`:

```python
import sys
import pathlib
import unittest
from decimal import Decimal

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from parse import (
    Account, AmbiguousAccountError, MalformedRowError, UnknownFileError,
    load_accounts, match_account, parse_file, parse_folder,
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
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
cd budget-bot && python3 -m unittest discover -s tests
```

Expected: `ModuleNotFoundError: No module named 'parse'`

- [ ] **Step 4: Write the implementation**

Create `budget-bot/scripts/parse.py`:

```python
"""Route bank CSV exports to accounts and map them onto the ledger schema.

Files are routed by header signature rather than filename, so the user
drops exports in under whatever name the bank chose. Nothing here guesses:
an unrecognized header, an ambiguous match, or an unparseable row stops
the run. A transaction silently dropped from a budget is worse than a
crash, because the user would reconcile against a total that looks right.
"""

import csv
import pathlib
import tomllib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from ledger import Row

NEGATIVE_IS_CHARGE = "negative_is_charge"
POSITIVE_IS_CHARGE = "positive_is_charge"
VALID_SIGNS = (NEGATIVE_IS_CHARGE, POSITIVE_IS_CHARGE)


@dataclass
class Account:
    name: str
    signature: list
    date_column: str
    date_format: str
    description: str
    amount_column: str
    amount_sign: str
    filename_hint: str = ""


class ParseError(Exception):
    """Anything that must stop the run rather than produce partial output."""


class UnknownFileError(ParseError):
    def __init__(self, filename, header):
        self.filename = filename
        self.header = header
        super().__init__(
            f"{filename}: header matches no configured account.\n"
            f"  header: {header}\n"
            f"  Add an account entry for it, or remove the file from the drop folder."
        )


class AmbiguousAccountError(ParseError):
    def __init__(self, filename, candidates):
        self.filename = filename
        self.candidates = candidates
        super().__init__(
            f"{filename}: header matches {len(candidates)} accounts ({', '.join(candidates)}).\n"
            f"  Add a distinct filename_hint to each so files can be told apart."
        )


class MalformedRowError(ParseError):
    def __init__(self, filename, line_number, reason):
        self.filename = filename
        self.line_number = line_number
        self.reason = reason
        super().__init__(f"{filename}:{line_number}: {reason}")


def load_accounts(path):
    """Read accounts.toml. Rejects an unknown amount_sign at load time."""
    with open(path, "rb") as handle:
        data = tomllib.load(handle)

    accounts = []
    for entry in data.get("account", []):
        account = Account(
            name=entry["name"],
            signature=list(entry["signature"]),
            date_column=entry["date_column"],
            date_format=entry["date_format"],
            description=entry["description"],
            amount_column=entry["amount_column"],
            amount_sign=entry["amount_sign"],
            filename_hint=entry.get("filename_hint", ""),
        )
        if account.amount_sign not in VALID_SIGNS:
            raise ParseError(
                f"{account.name}: amount_sign must be one of {VALID_SIGNS}, "
                f"got {account.amount_sign!r}"
            )
        accounts.append(account)
    return accounts


def match_account(header, filename, accounts):
    """Find the one account whose signature this header satisfies."""
    present = set(header)
    candidates = [a for a in accounts if set(a.signature) <= present]

    if not candidates:
        raise UnknownFileError(filename, list(header))
    if len(candidates) == 1:
        return candidates[0]

    lowered = filename.lower()
    hinted = [a for a in candidates if a.filename_hint and a.filename_hint.lower() in lowered]
    if len(hinted) == 1:
        return hinted[0]

    raise AmbiguousAccountError(filename, [a.name for a in candidates])


def _to_decimal(raw):
    """Parse a money string. Handles $, thousands commas, and (parenthesized)."""
    text = (raw or "").strip().replace("$", "").replace(",", "")
    if not text:
        return Decimal("0")
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    value = Decimal(text)
    return -value if negative else value


def parse_file(path, account):
    """Map one export onto the ledger schema. Any bad row raises."""
    path = pathlib.Path(path)
    rows = []

    with open(path, newline="", encoding="utf-8-sig") as handle:
        for line_number, record in enumerate(csv.DictReader(handle), start=2):
            try:
                stamp = datetime.strptime(
                    (record[account.date_column] or "").strip(), account.date_format
                ).strftime("%Y-%m-%d")
            except (ValueError, KeyError) as exc:
                raise MalformedRowError(path.name, line_number, f"bad date: {exc}") from exc

            try:
                amount = _to_decimal(record.get(account.amount_column))
            except (InvalidOperation, TypeError) as exc:
                raise MalformedRowError(path.name, line_number, f"bad amount: {exc}") from exc

            # Output convention is charges positive. An account that reports
            # a charge as negative gets flipped exactly once, here.
            if account.amount_sign == NEGATIVE_IS_CHARGE:
                amount = -amount

            descriptor = record.get(account.description)
            if descriptor is None:
                raise MalformedRowError(
                    path.name, line_number, f"missing column {account.description!r}"
                )

            rows.append(Row(
                timestamp=stamp,
                transaction=descriptor,   # verbatim, never normalized
                amount=amount,
                account=account.name,
            ))

    return rows


def parse_folder(folder, accounts):
    """Parse every CSV in the drop folder. Returns rows and unmatched accounts."""
    folder = pathlib.Path(folder)
    rows = []
    matched = set()

    # One case-insensitive pass. Globbing "*.csv" and "*.CSV" separately
    # returns each file twice on macOS, doubling every transaction.
    exports = sorted(p for p in folder.iterdir()
                     if p.is_file() and p.suffix.lower() == ".csv")
    for path in exports:
        with open(path, newline="", encoding="utf-8-sig") as handle:
            header = next(csv.reader(handle), [])
        account = match_account(header, path.name, accounts)
        matched.add(account.name)
        rows.extend(parse_file(path, account))

    missing = [a.name for a in accounts if a.name not in matched]
    return rows, missing
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd budget-bot && python3 -m unittest discover -s tests
```

Expected: `Ran 27 tests` / `OK`

- [ ] **Step 6: Commit**

```bash
git add budget-bot/scripts/parse.py budget-bot/tests/test_parse.py budget-bot/tests/fixtures
git commit -m "Add account routing and CSV parsing"
```

---

### Task 4: The merchant map

**Spec refinement — the map needs two more columns than the spec sketches.** The spec's review line reads `Costco → Grocery (usually; 13 of 31 were Household)`, which requires the runner-up counts, and its "clear the flag after five consecutive accepts" rule requires a streak counter. Neither survives in a four-column file. Two columns are added; the file stays one line per merchant and hand-editable.

**Files:**
- Create: `budget-bot/scripts/merchant_map.py`
- Create: `budget-bot/tests/test_merchant_map.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `MAP_COLUMNS = ["merchant", "category", "seen", "ambiguous", "streak", "alternates"]`
  - `MapEntry` dataclass: `merchant: str`, `category: str`, `seen: int`, `ambiguous: bool`, `streak: int = 0`, `alternates: dict[str, int] = {}`
  - `MapEntry.describe() -> str` — the review line, e.g. `Grocery (usually; 13 of 31 were Household)`
  - `load_map(path) -> dict[str, MapEntry]`
  - `save_map(path, entries: dict[str, MapEntry]) -> None` — sorted by merchant
  - `record_decision(entries, merchant, category) -> MapEntry`
  - `CLEAR_FLAG_STREAK = 5`

- [ ] **Step 1: Write the failing test**

Create `budget-bot/tests/test_merchant_map.py`:

```python
import sys
import pathlib
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from merchant_map import (
    CLEAR_FLAG_STREAK, MapEntry, load_map, record_decision, save_map,
)


class TestMapRoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()) / "merchant-map.csv"

    def test_round_trip_preserves_flag_and_alternates(self):
        save_map(self.tmp, {
            "COSTCO": MapEntry("COSTCO", "Grocery", 31, True, 0, {"Household": 13}),
            "BLUE BOTTLE": MapEntry("BLUE BOTTLE", "Dining", 14, False, 2, {}),
        })
        back = load_map(self.tmp)
        self.assertTrue(back["COSTCO"].ambiguous)
        self.assertEqual(back["COSTCO"].alternates, {"Household": 13})
        self.assertFalse(back["BLUE BOTTLE"].ambiguous)
        self.assertEqual(back["BLUE BOTTLE"].streak, 2)

    def test_saved_file_is_sorted_for_diffability(self):
        save_map(self.tmp, {
            "ZEBRA": MapEntry("ZEBRA", "Dining", 1, False),
            "APPLE": MapEntry("APPLE", "Tech", 1, False),
        })
        merchants = [line.split(",")[0] for line in
                     self.tmp.read_text().splitlines()[1:]]
        self.assertEqual(merchants, ["APPLE", "ZEBRA"])

    def test_missing_file_loads_as_empty(self):
        self.assertEqual(load_map(self.tmp.parent / "nope.csv"), {})


class TestRecordDecision(unittest.TestCase):
    def test_new_merchant_is_added_unflagged(self):
        entries = {}
        entry = record_decision(entries, "BLUE BOTTLE", "Dining")
        self.assertEqual(entry.seen, 1)
        self.assertFalse(entry.ambiguous)

    def test_agreement_bumps_seen_and_streak(self):
        entries = {"BLUE BOTTLE": MapEntry("BLUE BOTTLE", "Dining", 14, False, 1, {})}
        entry = record_decision(entries, "BLUE BOTTLE", "Dining")
        self.assertEqual(entry.seen, 15)
        self.assertEqual(entry.streak, 2)

    def test_disagreement_flags_ambiguous_and_records_the_alternate(self):
        entries = {"TARGET": MapEntry("TARGET", "Household", 20, False, 4, {})}
        entry = record_decision(entries, "TARGET", "Gifts")
        self.assertTrue(entry.ambiguous)
        self.assertEqual(entry.alternates, {"Gifts": 1})
        self.assertEqual(entry.streak, 0)
        # The majority category stays the default shown next month.
        self.assertEqual(entry.category, "Household")

    def test_streak_reaching_the_threshold_is_reported_not_auto_cleared(self):
        entries = {"COSTCO": MapEntry("COSTCO", "Grocery", 30, True,
                                      CLEAR_FLAG_STREAK - 1, {"Household": 13})}
        entry = record_decision(entries, "COSTCO", "Grocery")
        self.assertEqual(entry.streak, CLEAR_FLAG_STREAK)
        self.assertTrue(entry.ambiguous, "the flag is only cleared when the user says so")
        self.assertTrue(entry.clearable())


class TestDescribe(unittest.TestCase):
    def test_ambiguous_entry_shows_the_split(self):
        entry = MapEntry("COSTCO", "Grocery", 31, True, 0, {"Household": 13})
        self.assertEqual(entry.describe(), "Grocery (usually; 13 of 31 were Household)")

    def test_unambiguous_entry_is_just_the_category(self):
        self.assertEqual(MapEntry("BLUE BOTTLE", "Dining", 14, False).describe(), "Dining")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd budget-bot && python3 -m unittest discover -s tests
```

Expected: `ModuleNotFoundError: No module named 'merchant_map'`

- [ ] **Step 3: Write the implementation**

Create `budget-bot/scripts/merchant_map.py`:

```python
"""The learned merchant to category map.

This file is the accumulated product of the user's own decisions, so it
is stored as flat sorted CSV -- one line per merchant, hand-editable and
diffable -- rather than anything that needs a tool to inspect.

It lives in ~/.claude/budget-bot/, never in this repo: it is a multi-year
record of where its owner shops.
"""

import csv
from dataclasses import dataclass, field

MAP_COLUMNS = ["merchant", "category", "seen", "ambiguous", "streak", "alternates"]

# Consecutive agreements before the ambiguity flag is offered for removal.
CLEAR_FLAG_STREAK = 5


@dataclass
class MapEntry:
    merchant: str
    category: str
    seen: int
    ambiguous: bool
    streak: int = 0
    alternates: dict = field(default_factory=dict)

    def clearable(self):
        """Whether the ambiguity flag has earned an offer to be removed."""
        return self.ambiguous and self.streak >= CLEAR_FLAG_STREAK

    def describe(self):
        """The category as shown in the review table."""
        if not self.ambiguous or not self.alternates:
            return self.category
        runner_up, count = max(self.alternates.items(), key=lambda kv: kv[1])
        return f"{self.category} (usually; {count} of {self.seen} were {runner_up})"


def _pack(alternates):
    return ";".join(f"{k}:{v}" for k, v in sorted(alternates.items()))


def _unpack(text):
    out = {}
    for chunk in (text or "").split(";"):
        if ":" in chunk:
            key, _, value = chunk.partition(":")
            out[key.strip()] = int(value)
    return out


def load_map(path):
    """Read the map. A missing file is an empty map, not an error."""
    try:
        handle = open(path, newline="", encoding="utf-8")
    except FileNotFoundError:
        return {}

    with handle:
        entries = {}
        for record in csv.DictReader(handle):
            merchant = (record["merchant"] or "").strip()
            if not merchant:
                continue
            entries[merchant] = MapEntry(
                merchant=merchant,
                category=(record["category"] or "").strip(),
                seen=int(record.get("seen") or 0),
                ambiguous=(record.get("ambiguous") or "").strip().lower() == "true",
                streak=int(record.get("streak") or 0),
                alternates=_unpack(record.get("alternates")),
            )
    return entries


def save_map(path, entries):
    """Write the map sorted by merchant so diffs stay readable."""
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(MAP_COLUMNS)
        for merchant in sorted(entries):
            entry = entries[merchant]
            writer.writerow([
                entry.merchant, entry.category, entry.seen,
                "true" if entry.ambiguous else "false",
                entry.streak, _pack(entry.alternates),
            ])


def record_decision(entries, merchant, category):
    """Fold one confirmed categorization back into the map.

    A disagreement with the stored category is sufficient evidence that a
    merchant genuinely splits -- Target is Household most trips and Gifts
    some -- so it sets the flag on first occurrence. The majority category
    stays the default; the alternate is counted so the review line can show
    the split.
    """
    entry = entries.get(merchant)
    if entry is None:
        entry = MapEntry(merchant=merchant, category=category, seen=1, ambiguous=False)
        entries[merchant] = entry
        return entry

    entry.seen += 1
    if category == entry.category:
        entry.streak += 1
    else:
        entry.alternates[category] = entry.alternates.get(category, 0) + 1
        entry.ambiguous = True
        entry.streak = 0
    return entry
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd budget-bot && python3 -m unittest discover -s tests
```

Expected: `Ran 36 tests` / `OK`

- [ ] **Step 5: Commit**

```bash
git add budget-bot/scripts/merchant_map.py budget-bot/tests/test_merchant_map.py
git commit -m "Add learned merchant category map"
```

---

### Task 5: Dedupe against previously exported rows

**Spec refinement — `emitted.csv` stores the hash components, not just the hash.** The ±4 day near-match rule needs to compare dates and amounts against past rows, which a bare hash cannot support. The file keeps one row per emitted transaction with the fields the comparison needs. It lives in `~/.claude/budget-bot/`, outside the repo.

**Files:**
- Create: `budget-bot/scripts/dedupe.py`
- Create: `budget-bot/tests/test_dedupe.py`

**Interfaces:**
- Consumes: `ledger.Row`, `normalize.normalize_merchant`
- Produces:
  - `NEAR_MATCH_DAYS = 4`
  - `EMITTED_COLUMNS = ["hash", "account", "timestamp", "amount", "merchant", "category"]`
  - `Emitted` dataclass: `hash: str`, `account: str`, `timestamp: str`, `amount: Decimal`, `merchant: str`, `category: str = ""`
  - The `category` column exists so Task 9 can compare this month against the prior month. Dedupe itself ignores it.
  - `row_hash(row) -> str`
  - `load_emitted(path) -> list[Emitted]`
  - `append_emitted(path, rows) -> None`
  - `DedupeResult` dataclass: `new: list[Row]`, `exact_duplicates: list[Row]`, `near_matches: list[tuple[Row, Emitted]]`
  - `partition(rows, emitted) -> DedupeResult`

- [ ] **Step 1: Write the failing test**

Create `budget-bot/tests/test_dedupe.py`:

```python
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd budget-bot && python3 -m unittest discover -s tests
```

Expected: `ModuleNotFoundError: No module named 'dedupe'`

- [ ] **Step 3: Write the implementation**

Create `budget-bot/scripts/dedupe.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd budget-bot && python3 -m unittest discover -s tests
```

Expected: `Ran 45 tests` / `OK`

- [ ] **Step 5: Commit**

```bash
git add budget-bot/scripts/dedupe.py budget-bot/tests/test_dedupe.py
git commit -m "Add dedupe against previously exported rows"
```

---
### Task 6: The three categorization tiers

**Files:**
- Create: `budget-bot/scripts/categorize.py`
- Create: `budget-bot/tests/test_categorize.py`

**Interfaces:**
- Consumes: `ledger.Row`, `normalize.normalize_merchant`, `merchant_map.MapEntry`
- Produces:
  - `TIER_EXACT = 1`, `TIER_FUZZY = 2`, `TIER_UNKNOWN = 3`, `MIN_FUZZY_KEY_LENGTH = 4`, `MIN_SHARED_PREFIX = 8`
  - `Decision` dataclass: `row: Row`, `merchant: str`, `proposed: str`, `tier: int`, `display: str`, `needs_review: bool`
  - `fuzzy_match(key: str, entries: dict) -> str | None`
  - `categorize(rows: list[Row], entries: dict) -> list[Decision]`

- [ ] **Step 1: Write the failing test**

Create `budget-bot/tests/test_categorize.py`:

```python
import sys
import pathlib
import unittest
from decimal import Decimal

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from categorize import (
    TIER_EXACT, TIER_FUZZY, TIER_UNKNOWN, categorize, fuzzy_match,
)
from ledger import Row
from merchant_map import MapEntry


def make_row(transaction, amount="6.50"):
    return Row(timestamp="2026-08-04", transaction=transaction,
               amount=Decimal(amount), account="Test Visa")


ENTRIES = {
    "BLUE BOTTLE": MapEntry("BLUE BOTTLE", "Dining", 14, False),
    "COSTCO": MapEntry("COSTCO", "Grocery", 31, True, 0, {"Household": 13}),
    "WHOLE FOODS MARKET": MapEntry("WHOLE FOODS MARKET", "Grocery", 9, False),
}


class TestCategorize(unittest.TestCase):
    def test_known_unambiguous_merchant_is_assigned_silently(self):
        decision = categorize([make_row("SQ *BLUE BOTTLE 1123")], dict(ENTRIES))[0]
        self.assertEqual(decision.proposed, "Dining")
        self.assertEqual(decision.tier, TIER_EXACT)
        self.assertFalse(decision.needs_review)

    def test_ambiguous_merchant_always_surfaces_with_the_split(self):
        decision = categorize([make_row("COSTCO WHSE 0455")], dict(ENTRIES))[0]
        self.assertEqual(decision.tier, TIER_EXACT)
        self.assertTrue(decision.needs_review)
        self.assertEqual(decision.proposed, "Grocery")
        self.assertIn("13 of 31 were Household", decision.display)

    def test_fuzzy_match_is_assigned_but_shown(self):
        decision = categorize([make_row("WHOLE FOODS MKT")], dict(ENTRIES))[0]
        self.assertEqual(decision.tier, TIER_FUZZY)
        self.assertTrue(decision.needs_review)

    def test_unknown_merchant_has_no_proposal_for_claude_to_fill(self):
        decision = categorize([make_row("SOME NEW PLACE")], dict(ENTRIES))[0]
        self.assertEqual(decision.tier, TIER_UNKNOWN)
        self.assertEqual(decision.proposed, "")
        self.assertTrue(decision.needs_review)

    def test_descriptor_is_never_replaced_by_the_normalized_key(self):
        decision = categorize([make_row("SQ *BLUE BOTTLE 1123")], dict(ENTRIES))[0]
        self.assertEqual(decision.row.transaction, "SQ *BLUE BOTTLE 1123")
        self.assertEqual(decision.merchant, "BLUE BOTTLE")


class TestFuzzyMatch(unittest.TestCase):
    def test_prefix_of_a_known_merchant_matches(self):
        self.assertEqual(fuzzy_match("WHOLE FOODS", ENTRIES), "WHOLE FOODS MARKET")

    def test_short_keys_do_not_fuzzy_match(self):
        # A two-character key would prefix-match half the map.
        self.assertIsNone(fuzzy_match("BL", ENTRIES))

    def test_unrelated_key_does_not_match(self):
        self.assertIsNone(fuzzy_match("SOME NEW PLACE", ENTRIES))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd budget-bot && python3 -m unittest discover -s tests
```

Expected: `ModuleNotFoundError: No module named 'categorize'`

- [ ] **Step 3: Write the implementation**

Create `budget-bot/scripts/categorize.py`:

```python
"""Apply the merchant map to a month of transactions.

Three tiers, in descending confidence. Only the first is silent; the other
two are handed to the user in a single review table. A flagged merchant is
never silent regardless of how well it matches, because merchants like
Costco genuinely split by trip and one careless inherited answer would
quietly poison a year of data.
"""

from dataclasses import dataclass

from normalize import normalize_merchant

TIER_EXACT = 1
TIER_FUZZY = 2
TIER_UNKNOWN = 3

# Below this length a key prefix-matches far too much of the map.
MIN_FUZZY_KEY_LENGTH = 4

# Shared leading characters before two keys are considered the same
# merchant. Abbreviations diverge mid-string ("WHOLE FOODS MKT" vs
# "WHOLE FOODS MARKET"), so neither is a prefix of the other and a
# startswith test would miss them.
MIN_SHARED_PREFIX = 8


@dataclass
class Decision:
    row: object
    merchant: str
    proposed: str
    tier: int
    display: str
    needs_review: bool


def _shared_prefix_length(a, b):
    count = 0
    for left, right in zip(a, b):
        if left != right:
            break
        count += 1
    return count


def fuzzy_match(key, entries):
    """Known merchant sharing the longest leading run with this key."""
    if len(key) < MIN_FUZZY_KEY_LENGTH:
        return None

    best = None
    best_shared = 0
    for known in entries:
        if known == key or len(known) < MIN_FUZZY_KEY_LENGTH:
            continue
        shared = _shared_prefix_length(key, known)
        if shared >= MIN_SHARED_PREFIX and shared > best_shared:
            best, best_shared = known, shared
    return best


def categorize(rows, entries):
    """Assign a category to each row, marking what needs human review."""
    decisions = []

    for row in rows:
        key = normalize_merchant(row.transaction)
        entry = entries.get(key)

        if entry is not None:
            decisions.append(Decision(
                row=row, merchant=key, proposed=entry.category, tier=TIER_EXACT,
                display=entry.describe(), needs_review=entry.ambiguous,
            ))
            continue

        matched = fuzzy_match(key, entries)
        if matched is not None:
            entry = entries[matched]
            decisions.append(Decision(
                row=row, merchant=key, proposed=entry.category, tier=TIER_FUZZY,
                display=f"{entry.category} (matched {matched})", needs_review=True,
            ))
            continue

        decisions.append(Decision(
            row=row, merchant=key, proposed="", tier=TIER_UNKNOWN,
            display="unknown merchant", needs_review=True,
        ))

    return decisions
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd budget-bot && python3 -m unittest discover -s tests
```

Expected: `Ran 53 tests` / `OK`

- [ ] **Step 5: Commit**

```bash
git add budget-bot/scripts/categorize.py budget-bot/tests/test_categorize.py
git commit -m "Add three-tier transaction categorization"
```

---

### Task 7: Bootstrap the map from categorized history

**Files:**
- Create: `budget-bot/scripts/bootstrap.py`
- Create: `budget-bot/tests/test_bootstrap.py`

**Interfaces:**
- Consumes: `ledger.Row`/`read_ledger`, `normalize.normalize_merchant`, `merchant_map.MapEntry`/`save_map`
- Produces:
  - `AMBIGUITY_MINORITY_SHARE = 0.20`, `AMBIGUITY_MINORITY_COUNT = 3`, `RECENCY_WINDOW_DAYS = 183`, `RECENCY_MIN_OCCURRENCES = 2`
  - `BootstrapReport` dataclass: `rows_used: int`, `merchants: int`, `flagged: list[str]`, `borderline: list[str]`, `recency_overrides: list[str]`, `unknown_categories: dict[str, int]`, `blank_category_rows: int`
  - `build_map(rows, known_categories: set | None = None) -> tuple[dict[str, MapEntry], BootstrapReport]`

- [ ] **Step 1: Write the failing test**

Create `budget-bot/tests/test_bootstrap.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd budget-bot && python3 -m unittest discover -s tests
```

Expected: `ModuleNotFoundError: No module named 'bootstrap'`

- [ ] **Step 3: Write the implementation**

Create `budget-bot/scripts/bootstrap.py`:

```python
"""Build the merchant map from a year of the user's own categorizations.

This is what makes the first live run useful instead of a cold start: the
decisions are already made, sitting in the exported sheet, and only need
to be indexed. Everything here reads; the only write is merchant-map.csv.
"""

import argparse
import pathlib
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from ledger import read_ledger
from merchant_map import MapEntry, load_map, save_map
from normalize import normalize_merchant

# A runner-up category must clear BOTH bars to count as a genuine split
# rather than a stale mis-tag. This is the knob controlling how big the
# monthly review table is; tune after a real run.
AMBIGUITY_MINORITY_SHARE = 0.20
AMBIGUITY_MINORITY_COUNT = 3

# A merchant categorized consistently in recent history was deliberately
# re-categorized; the all-time majority is a decision already overturned.
RECENCY_WINDOW_DAYS = 183
RECENCY_MIN_OCCURRENCES = 2


@dataclass
class BootstrapReport:
    rows_used: int = 0
    merchants: int = 0
    flagged: list = field(default_factory=list)
    borderline: list = field(default_factory=list)
    recency_overrides: list = field(default_factory=list)
    unknown_categories: dict = field(default_factory=dict)
    blank_category_rows: int = 0


def build_map(rows, known_categories=None):
    """Index categorized history into a merchant map plus a data-quality report."""
    report = BootstrapReport()
    by_merchant = defaultdict(list)
    unknown = Counter()

    for row in rows:
        if not row.category:
            report.blank_category_rows += 1
            continue
        if known_categories is not None and row.category not in known_categories:
            unknown[row.category] += 1
            continue
        by_merchant[normalize_merchant(row.transaction)].append(row)

    report.unknown_categories = dict(unknown)

    latest = max((r.timestamp for group in by_merchant.values() for r in group),
                 default=None)
    cutoff = (date.fromisoformat(latest) - timedelta(days=RECENCY_WINDOW_DAYS)
              if latest else None)

    entries = {}
    for merchant, group in by_merchant.items():
        counts = Counter(r.category for r in group)
        total = sum(counts.values())
        (majority, majority_count), *rest = counts.most_common()

        chosen = majority
        recent = [r.category for r in group
                  if cutoff and date.fromisoformat(r.timestamp) >= cutoff]
        if (len(recent) >= RECENCY_MIN_OCCURRENCES
                and len(set(recent)) == 1
                and recent[0] != majority):
            chosen = recent[0]
            report.recency_overrides.append(merchant)

        alternates = {c: n for c, n in counts.items() if c != chosen}
        ambiguous = False
        if rest:
            runner_up_count = max(alternates.values(), default=0)
            share = runner_up_count / total if total else 0
            if (share >= AMBIGUITY_MINORITY_SHARE
                    and runner_up_count >= AMBIGUITY_MINORITY_COUNT):
                ambiguous = True
                report.flagged.append(merchant)
            elif runner_up_count >= 2:
                report.borderline.append(merchant)

        entries[merchant] = MapEntry(
            merchant=merchant, category=chosen, seen=total,
            ambiguous=ambiguous, streak=0,
            alternates=alternates if ambiguous else {},
        )

    report.rows_used = sum(len(g) for g in by_merchant.values())
    report.merchants = len(entries)
    return entries, report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build merchant-map.csv from history")
    parser.add_argument("history", nargs="+", type=pathlib.Path,
                        help="one or more sheet exports in the ledger schema")
    parser.add_argument("--out", type=pathlib.Path,
                        default=pathlib.Path.home() / ".claude/budget-bot/merchant-map.csv")
    parser.add_argument("--force", action="store_true",
                        help="overwrite an existing map instead of writing alongside it")
    args = parser.parse_args(argv)

    rows = [row for path in args.history for row in read_ledger(path)]
    entries, report = build_map(rows)

    destination = args.out
    if destination.exists() and not args.force:
        # The existing map holds corrections history does not contain.
        destination = destination.with_suffix(".new.csv")
        print(f"{args.out} exists; writing {destination} instead.")
        print(f"Compare with: diff {args.out} {destination}")
        print("Re-run with --force to replace it.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    save_map(destination, entries)

    print(f"\n{report.merchants} merchants from {report.rows_used} categorized rows")
    print(f"  flagged ambiguous: {len(report.flagged)}")
    if report.borderline:
        print(f"  borderline, confirm these: {', '.join(sorted(report.borderline))}")
    if report.recency_overrides:
        print(f"  recency overrides: {', '.join(sorted(report.recency_overrides))}")
    if report.blank_category_rows:
        print(f"  skipped {report.blank_category_rows} rows with no category")
    for category, count in sorted(report.unknown_categories.items()):
        print(f"  unrecognized category {category!r}: {count} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd budget-bot && python3 -m unittest discover -s tests
```

Expected: `Ran 59 tests` / `OK`

- [ ] **Step 5: Commit**

```bash
git add budget-bot/scripts/bootstrap.py budget-bot/tests/test_bootstrap.py
git commit -m "Add merchant map bootstrap from categorized history"
```

---

### Task 8: Holdout validation

The spec's gate before first live use. Build the map from everything before a chosen month, then measure what fraction of that month it would have auto-categorized correctly. A result near 85% confirms the design; near 60% means normalization needs work, and that should be known now rather than after a disappointing first run.

**Files:**
- Modify: `budget-bot/scripts/bootstrap.py` (add `holdout_coverage` and a `--holdout` flag)
- Modify: `budget-bot/tests/test_bootstrap.py` (add `TestHoldout`)

**Interfaces:**
- Consumes: `build_map`, `categorize.categorize`
- Produces: `holdout_coverage(rows, month: str) -> dict` with keys `train_rows`, `test_rows`, `auto_assigned`, `correct`, `coverage`, `misses` (list of `(merchant, expected, got)`)

- [ ] **Step 1: Write the failing test**

Append to `budget-bot/tests/test_bootstrap.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd budget-bot && python3 -m unittest discover -s tests
```

Expected: `ImportError: cannot import name 'holdout_coverage'`

- [ ] **Step 3: Add the implementation to `bootstrap.py`**

Add the import at the top, next to the others:

```python
from categorize import TIER_EXACT, categorize
```

Add the function after `build_map`:

```python
def holdout_coverage(rows, month):
    """Measure the map against a month it was not built from.

    Only silent assignments count as coverage. A row the pipeline would
    have put in front of the user is not automation, even when the
    proposal turns out right.
    """
    train = [r for r in rows if not r.timestamp.startswith(month)]
    test = [r for r in rows if r.timestamp.startswith(month)]

    entries, _ = build_map(train)
    correct = 0
    auto_assigned = 0
    misses = []

    for decision in categorize(test, entries):
        expected = decision.row.category
        if decision.tier == TIER_EXACT and not decision.needs_review:
            auto_assigned += 1
            if decision.proposed == expected:
                correct += 1
            else:
                misses.append((decision.merchant, expected, decision.proposed))
        else:
            misses.append((decision.merchant, expected, decision.proposed or "(no match)"))

    return {
        "train_rows": len(train),
        "test_rows": len(test),
        "auto_assigned": auto_assigned,
        "correct": correct,
        "coverage": correct / len(test) if test else 0.0,
        "misses": misses,
    }
```

Add the flag in `main`, after the `--force` argument:

```python
    parser.add_argument("--holdout", metavar="YYYY-MM",
                        help="validate against this month instead of writing a map")
```

And immediately after `rows = [...]` in `main`, before `build_map` is called:

```python
    if args.holdout:
        result = holdout_coverage(rows, args.holdout)
        print(f"Holdout {args.holdout}: {result['test_rows']} rows, "
              f"{result['train_rows']} used for training")
        print(f"  auto-categorized correctly: {result['correct']} "
              f"({result['coverage']:.0%})")
        print(f"  needed review: {result['test_rows'] - result['auto_assigned']}")
        for merchant, expected, got in result["misses"][:20]:
            print(f"    {merchant}: expected {expected}, got {got}")
        return 0
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd budget-bot && python3 -m unittest discover -s tests
```

Expected: `Ran 63 tests` / `OK`

- [ ] **Step 5: Commit**

```bash
git add budget-bot/scripts/bootstrap.py budget-bot/tests/test_bootstrap.py
git commit -m "Add holdout validation for merchant map coverage"
```

---
### Task 9: Two-phase orchestration and sanity checks

The run is split into `review` and `commit` because a human decision happens in the middle. `review` produces a work file and a table; Claude collects corrections; `commit` applies them and writes. Splitting it this way also gets the spec's write ordering right — output CSV first, then map, then emitted ledger — so a crash never records rows the user does not have.

**Files:**
- Create: `budget-bot/scripts/run.py`
- Create: `budget-bot/tests/test_run.py`

**Interfaces:**
- Consumes: everything above
- Produces:
  - `DOUBLING_FACTOR = 2.0`
  - `summarize(rows, emitted, missing_accounts, today=None) -> dict` with keys `by_account`, `by_category`, `prior_by_category`, `doubled`, `missing_accounts`, `total`. `today` is injectable so the prior-month comparison is testable rather than dependent on the wall clock.
  - `review(drop, state, work, since=None) -> int`
  - `--since YYYY-MM-DD` overrides the emitted ledger: only rows on or after that date are considered, and dedupe is skipped for them. This is the escape hatch for when `emitted.csv` has drifted from the real sheet.
  - `commit(work, state, out, overrides: dict[int, str]) -> int`
  - `main(argv=None) -> int`

- [ ] **Step 1: Write the failing test**

Create `budget-bot/tests/test_run.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd budget-bot && python3 -m unittest discover -s tests
```

Expected: `ModuleNotFoundError: No module named 'run'`

- [ ] **Step 3: Write the implementation**

Create `budget-bot/scripts/run.py`:

```python
"""The monthly run, in two phases with the human decision in between.

`review` parses, dedupes, categorizes, and writes a work file plus a table
of everything needing a person. `commit` takes that work file plus the
user's corrections and writes the output.

The write order in commit is deliberate: the output CSV first, then the
merchant map, then the emitted ledger. A crash partway through leaves state
that under-claims what was exported, which produces a duplicate the user can
see, rather than over-claiming and silently losing a transaction.
"""

import argparse
import json
import pathlib
import sys
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from categorize import categorize
from dedupe import append_emitted, load_emitted, partition
from ledger import Row, write_ledger
from merchant_map import load_map, record_decision, save_map
from parse import ParseError, load_accounts, parse_folder

DOUBLING_FACTOR = 2.0


def _prior_month(today=None):
    first = (today or date.today()).replace(day=1)
    return (first - timedelta(days=1)).strftime("%Y-%m")


def summarize(rows, emitted, missing_accounts, today=None):
    """Sanity checks run before the user pastes anything into the sheet."""
    by_account = defaultdict(lambda: Decimal("0"))
    by_category = defaultdict(lambda: Decimal("0"))
    for row in rows:
        by_account[row.account] += row.amount
        by_category[row.category or "(uncategorized)"] += row.amount

    prior_key = _prior_month(today)
    prior = defaultdict(lambda: Decimal("0"))
    for record in emitted:
        if record.timestamp.startswith(prior_key) and record.category:
            prior[record.category] += record.amount

    doubled = [
        category for category, total in by_category.items()
        if prior.get(category) and total > prior[category] * Decimal(str(DOUBLING_FACTOR))
    ]

    return {
        "by_account": dict(by_account),
        "by_category": dict(by_category),
        "prior_by_category": dict(prior),
        "doubled": sorted(doubled),
        "missing_accounts": missing_accounts,
        "total": sum((r.amount for r in rows), Decimal("0")),
    }


def review(drop, state, work, since=None):
    """Phase one: parse, dedupe, categorize, write the work file."""
    state = pathlib.Path(state)
    accounts = load_accounts(state / "accounts.toml")
    entries = load_map(state / "merchant-map.csv")
    emitted = load_emitted(state / "emitted.csv")

    parsed, missing = parse_folder(drop, accounts)

    if since:
        # The ledger is the normal source of truth, but it can drift from
        # the real sheet. --since replaces it with an explicit date.
        parsed = [row for row in parsed if row.timestamp >= since]
        result = partition(parsed, [])
    else:
        result = partition(parsed, emitted)

    decisions = categorize(result.new, entries)

    near_by_id = {id(row): previous for row, previous in result.near_matches}
    rows = []
    for number, decision in enumerate(decisions, start=1):
        near = near_by_id.get(id(decision.row))
        rows.append({
            "n": number,
            "timestamp": decision.row.timestamp,
            "transaction": decision.row.transaction,
            "amount": f"{decision.row.amount:.2f}",
            "account": decision.row.account,
            "merchant": decision.merchant,
            "proposed": decision.proposed,
            "tier": decision.tier,
            "display": decision.display,
            "needs_review": decision.needs_review or near is not None,
            "possible_duplicate_of": near.timestamp if near else None,
        })

    stamps = [row["timestamp"] for row in rows]
    payload = {
        "drop_folder": str(drop),
        "rows": rows,
        "missing_accounts": missing,
        "exact_duplicates": len(result.exact_duplicates),
        "date_range": [min(stamps), max(stamps)] if stamps else None,
    }
    work = pathlib.Path(work)
    work.parent.mkdir(parents=True, exist_ok=True)
    work.write_text(json.dumps(payload, indent=2))

    _print_review(payload, emitted)
    return 0


def _print_review(payload, emitted):
    rows = payload["rows"]
    print(f"{len(rows)} new transactions "
          f"({payload['exact_duplicates']} already exported)")

    # Printed every run so a drifted emitted.csv is visible rather than
    # silently swallowing a month.
    if payload["date_range"]:
        print(f"covering {payload['date_range'][0]} to {payload['date_range'][1]}")

    if payload["missing_accounts"]:
        print(f"\nWARNING: no file matched these accounts: "
              f"{', '.join(payload['missing_accounts'])}")

    needing = [r for r in rows if r["needs_review"]]
    if not needing:
        print("\nNothing needs review.")
        return

    print(f"\n{len(needing)} need review:\n")
    for row in needing:
        flag = ""
        if row["possible_duplicate_of"]:
            flag = f"  [possible duplicate of {row['possible_duplicate_of']}]"
        proposal = row["display"] if row["proposed"] else "NEEDS A CATEGORY"
        print(f"  {row['n']:>3}. {row['timestamp']}  {row['amount']:>10}  "
              f"{row['transaction'][:40]:<40}  {proposal}{flag}")


def commit(work, state, out, overrides):
    """Phase two: apply corrections, write output, then update state."""
    state = pathlib.Path(state)
    payload = json.loads(pathlib.Path(work).read_text())

    rows = []
    uncategorized = []
    for record in payload["rows"]:
        category = overrides.get(record["n"], record["proposed"])
        if not category:
            uncategorized.append(record)
            continue
        rows.append((record, Row(
            timestamp=record["timestamp"],
            transaction=record["transaction"],
            amount=Decimal(record["amount"]),
            account=record["account"],
            category=category,
        )))

    if uncategorized:
        print("Refusing to write: these rows have no category.", file=sys.stderr)
        for record in uncategorized:
            print(f"  {record['n']}. {record['transaction']}", file=sys.stderr)
        raise SystemExit(1)

    ledger_rows = [row for _, row in rows]

    # Output first. State that under-claims produces a visible duplicate;
    # state that over-claims silently loses a transaction.
    write_ledger(out, ledger_rows)

    entries = load_map(state / "merchant-map.csv")
    for record, row in rows:
        record_decision(entries, record["merchant"], row.category)
    save_map(state / "merchant-map.csv", entries)

    append_emitted(state / "emitted.csv", ledger_rows)

    summary = summarize(ledger_rows, load_emitted(state / "emitted.csv"),
                        payload["missing_accounts"])
    print(f"\nWrote {len(ledger_rows)} rows to {out}")
    if summary["missing_accounts"]:
        print(f"  WARNING no file matched: {', '.join(summary['missing_accounts'])}")
    for account, total in sorted(summary["by_account"].items()):
        print(f"  {account}: {total:.2f}")
    for category in summary["doubled"]:
        print(f"  WARNING {category} more than doubled vs last month")
    return 0


def _parse_overrides(pairs):
    overrides = {}
    for pair in pairs or []:
        number, _, category = pair.partition("=")
        overrides[int(number)] = category
    return overrides


def main(argv=None):
    parser = argparse.ArgumentParser(description="budget-bot monthly run")
    default_state = pathlib.Path.home() / ".claude/budget-bot"
    sub = parser.add_subparsers(dest="command", required=True)

    review_cmd = sub.add_parser("review")
    review_cmd.add_argument("--drop", type=pathlib.Path,
                            default=pathlib.Path.home() / "Documents/budget-bot")
    review_cmd.add_argument("--state", type=pathlib.Path, default=default_state)
    review_cmd.add_argument("--work", type=pathlib.Path,
                            default=default_state / "work.json")
    review_cmd.add_argument("--since", metavar="YYYY-MM-DD",
                            help="ignore emitted.csv; take everything from this date")

    commit_cmd = sub.add_parser("commit")
    commit_cmd.add_argument("--work", type=pathlib.Path,
                            default=default_state / "work.json")
    commit_cmd.add_argument("--state", type=pathlib.Path, default=default_state)
    commit_cmd.add_argument("--out", type=pathlib.Path, required=True)
    commit_cmd.add_argument("--set", action="append", metavar="N=Category",
                            help="override row N's category; repeatable")

    args = parser.parse_args(argv)

    try:
        if args.command == "review":
            return review(args.drop, args.state, args.work, args.since)
        return commit(args.work, args.state, args.out, _parse_overrides(args.set))
    except ParseError as exc:
        print(f"\nSTOPPED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd budget-bot && python3 -m unittest discover -s tests
```

Expected: `Ran 74 tests` / `OK`

- [ ] **Step 5: Commit**

```bash
git add budget-bot/scripts/run.py budget-bot/tests/test_run.py
git commit -m "Add two-phase monthly run orchestration"
```

---

### Task 10: SKILL.md, references, and installation

**Files:**
- Create: `budget-bot/SKILL.md`
- Create: `budget-bot/README.md`
- Create: `budget-bot/references/categories.md`
- Create: `budget-bot/references/accounts.template.toml`
- Create: `budget-bot/references/adding-an-account.md`
- Create: `budget-bot/install_to_claude.sh`
- Modify: `README.md` (add budget-bot to "The Skills")

- [ ] **Step 1: Write `SKILL.md`**

```markdown
---
name: budget-bot
description: Use when doing the monthly budget — turns per-account CSV exports from banks and credit cards into one normalized, categorized ledger ready to paste into a budget spreadsheet. Replays past categorization decisions so only new or ambiguous merchants need a human.
---

# Monthly Budget Ledger

Eight accounts, one ledger. The scripts do all parsing, arithmetic, and
deduplication; your only job is adjudicating merchants the map has not
seen and confirming the ones that genuinely split by trip.

Never categorize a transaction the scripts already resolved, never edit
amounts or descriptors by hand, and never write the output CSV yourself.

## Setup

Paths below are relative to this skill's own base directory (Claude is
told that directory when the skill loads — substitute it in). They are
never relative to the current working directory.

State lives in `~/.claude/budget-bot/`:

| File | Purpose |
|---|---|
| `accounts.toml` | Per-account column mappings. Required. |
| `merchant-map.csv` | Learned merchant → category map |
| `emitted.csv` | Rows already exported, for dedupe |

If `accounts.toml` is missing, walk the user through creating it from
`references/accounts.template.toml` and stop — nothing works without it.

If `merchant-map.csv` is missing, this is a first run. Offer the bootstrap
below before anything else; a cold map means the user categorizes every
row by hand this month.

## First run only: bootstrap from history

Ask the user to export their existing budget sheet as CSV. Then validate
before trusting it:

```bash
python3 <skill-dir>/scripts/bootstrap.py history.csv --holdout 2026-07
```

Report the coverage figure plainly. Near 85% means the map works. Near
60% means normalization is mis-collapsing merchants — show the misses and
say so rather than proceeding as though the run succeeded.

Then build the map:

```bash
python3 <skill-dir>/scripts/bootstrap.py history.csv
```

Report flagged, borderline, and recency-override merchants, and resolve
any unrecognized category values with the user. Write the confirmed
category list into `references/categories.md`.

## The monthly run

### 1. Review

```bash
python3 <skill-dir>/scripts/run.py review
```

Reads `~/Documents/budget-bot/`. Pass `--drop <path>` for a different
folder.

If it stops with `STOPPED:`, do not work around it. An unrecognized header
means a new account or a changed export format — walk through
`references/adding-an-account.md`. An ambiguous match means two accounts
share a format and need `filename_hint` values. A malformed row means the
export is damaged.

**If it warns that no file matched an account, say so first and loudly.**
A forgotten download is the most likely error in this whole process, and a
month missing an entire card looks perfectly normal in the output.

### 2. Present the review table

Show every row the script flagged, in one numbered table. Do not ask
about them one at a time.

For each, give the proposal and enough to decide on: the descriptor, the
amount, and for flagged merchants the historical split the script
supplies. Use `references/categories.md` for merchants with no proposal —
propose the best category rather than leaving it blank.

Tell the user to reply with corrections only, and that silence accepts the
rest.

### 3. Commit

Pass one `--set` per correction. Rows you proposed a category for need no
flag; rows the script could not resolve need one, and commit refuses to
write while any row lacks a category.

```bash
python3 <skill-dir>/scripts/run.py commit --out ~/Documents/budget-bot/2026-08.csv --set 4=Gifts --set 11=Household
```

### 4. Report

Give the user the output path, the row count, per-account totals, and any
warning the script printed. If a category more than doubled against last
month, name it — it is usually either a real anomaly worth knowing about
or a miscategorization worth fixing before it enters the sheet.

Then tell them the file is ready to paste. Do not attempt to write to
Google Sheets; this skill deliberately stops at the CSV.

## Ambiguity flags

A flag is normally set by the scripts — inferred at bootstrap, or learned
the first time the user overrides a silent assignment. The user can also
set one directly ("always ask me about Amazon"): find the merchant's row
in `merchant-map.csv` and set its `ambiguous` column to `true`. If the
merchant is not in the map yet, say so rather than inventing a row; it
will be added the first time it appears.

When a flagged merchant has been confirmed the same way five runs in a
row, `merchant-map.csv` marks it clearable. Offer to clear it. Never
clear one without asking — a flag disappearing unprompted is exactly the
change a user would not notice.
```

- [ ] **Step 2: Write `references/accounts.template.toml`**

```toml
# Copy to ~/.claude/budget-bot/accounts.toml and fill in one block per account.
#
# signature     the export's header row. Routing is by header, not filename,
#               so files can keep whatever name the bank gave them.
# filename_hint only needed when two accounts export identical headers (two
#               cards at the same bank). A substring of the downloaded name.
# amount_sign   "negative_is_charge" if the export shows a purchase as -42.10,
#               "positive_is_charge" if it shows 42.10. Output is always
#               charges-positive; this is where that is reconciled.

[[account]]
name          = "Example Card"
signature     = ["Transaction Date", "Post Date", "Description", "Amount"]
date_column   = "Transaction Date"
date_format   = "%m/%d/%Y"
description   = "Description"
amount_column = "Amount"
amount_sign   = "negative_is_charge"
filename_hint = ""
```

- [ ] **Step 3: Write `references/adding-an-account.md`**

```markdown
# Adding an account

The parser stops rather than guessing when a file's header matches no
account. That happens on a genuinely new account, and on a bank that
changed its export format — the second is more common and looks identical.

1. Read the header the error printed.
2. If an existing account's format merely changed, update that block's
   `signature` and any renamed columns. Do not add a second account.
3. Otherwise add a block to `~/.claude/budget-bot/accounts.toml` using
   `accounts.template.toml` as the shape.
4. Determine `amount_sign` by looking at a known purchase in the file. If
   it is negative, use `negative_is_charge`.
5. If the header is identical to an existing account, give both blocks a
   `filename_hint`.
6. Anonymize a few rows into `tests/fixtures/` and add a parse test, so a
   future format change is caught by the suite rather than in production.

Never edit a source CSV to make it parse. The config describes the bank's
format; changing the data hides the problem and breaks the next export.
```

- [ ] **Step 4: Write `references/categories.md`**

Write the file with this structure. The category list itself is filled in during the first bootstrap from the distinct values found in the user's real history — the canonical set comes from the sheet, not from invention.

```markdown
# Categories

The authoritative list. A category not here is not valid output; if a
transaction seems to need one, ask rather than inventing it.

<!-- Filled in at first bootstrap from the user's history. One line each:
     `- **Name** — what belongs here.` -->

## Boundary rules

Recurring judgment calls, settled once so they are settled the same way
every month.

<!-- Added as they come up in review. Each rule names the two categories
     it separates and the test that decides between them. Example:
     "Prepared food from a grocery store counter is Grocery, not Dining;
     Dining requires table service or a restaurant." -->
```

- [ ] **Step 5: Write `install_to_claude.sh`**

Model it on `lodging-scout/install_to_claude.sh`: same zsh preamble (`emulate -L zsh`, `setopt err_exit no_unset pipe_fail`), same `runtime=(SKILL.md references scripts)` copy, same `__pycache__` cleanup. Differences: no MCP servers to add, and it creates `~/.claude/budget-bot/` and `~/Documents/budget-bot/` if absent, never overwriting an existing `accounts.toml` or `merchant-map.csv`.

```bash
#!/usr/bin/env zsh
#
# Install budget-bot into ~/.claude/skills and create its directories.
#
# Safe to re-run: the installed skill directory is replaced wholesale, and
# existing state -- accounts.toml, the merchant map, the emitted ledger --
# is never touched.

emulate -L zsh
setopt err_exit no_unset pipe_fail

src=${0:A:h}
cd $src

claude_dir=${CLAUDE_CONFIG_DIR:-$HOME/.claude}
skill_dir=$claude_dir/skills/budget-bot
state_dir=$claude_dir/budget-bot
drop_dir=$HOME/Documents/budget-bot

runtime=(SKILL.md references scripts)

if ! whence -p python3 >/dev/null; then
    print -u2 "error: python3 not found on PATH"
    exit 1
fi

for item in $runtime; do
    if [[ ! -e $item ]]; then
        print -u2 "error: missing '$item' -- run this from a full checkout"
        exit 1
    fi
done

print "Installing skill to $skill_dir"
rm -rf $skill_dir
mkdir -p $skill_dir
cp -R $runtime $skill_dir/
find $skill_dir \( -name '__pycache__' -o -name '*.pyc' \) -prune -exec rm -rf {} +
print "  $(find $skill_dir -type f | wc -l | tr -d ' ') files"

for dir in $state_dir $drop_dir; do
    if [[ -d $dir ]]; then
        print "  $dir exists -- left untouched"
    else
        mkdir -p $dir
        print "  $dir created"
    fi
done

if [[ -f $state_dir/accounts.toml ]]; then
    print "  accounts.toml exists -- left untouched"
else
    print "\n  accounts.toml is MISSING. The skill needs it. Start with:"
    print "    cp $src/references/accounts.template.toml $state_dir/accounts.toml"
    print "    \$EDITOR $state_dir/accounts.toml"
fi

print "\nDone. Restart Claude Code to pick up the new skill."
```

- [ ] **Step 6: Write `budget-bot/README.md`**

Cover: what the skill does, the one-time bootstrap with the holdout check, the monthly run, where state lives and why it is outside the repo, and how to add an account. Keep the tone of `lodging-scout/README.md`.

- [ ] **Step 7: Add the skill to the repo README**

In the root `README.md`, under `## The Skills`, after the `lodging-scout` section:

```markdown
### budget-bot

Turns per-account CSV exports from banks and credit cards into one
normalized, categorized ledger ready to paste into a budget spreadsheet.
Bootstraps a merchant→category map from your existing sheet, so only new
or genuinely ambiguous merchants need a decision.

Needs Python 3.11+ and nothing else. Setup is in the
[skill's README](budget-bot/README.md).
```

- [ ] **Step 8: Verify the whole suite and the install script**

```bash
cd budget-bot && python3 -m unittest discover -s tests
zsh -n budget-bot/install_to_claude.sh
```

Expected: `OK`, and no syntax errors from `zsh -n`.

- [ ] **Step 9: Commit**

```bash
chmod +x budget-bot/install_to_claude.sh
git add budget-bot/SKILL.md budget-bot/README.md budget-bot/references \
        budget-bot/install_to_claude.sh README.md
git commit -m "Add budget-bot skill instructions and installer"
```

---

## Verification

After Task 10, before first live use:

1. `cd budget-bot && python3 -m unittest discover -s tests` — full suite green.
2. `./package-skill.sh budget-bot` from the repo root — confirms the skill packages cleanly for upload.
3. **The holdout gate.** Run `bootstrap.py --holdout` against the real history export. This is the go/no-go on the central premise. Report the number honestly — if coverage is far below 85%, the normalization rules need work before the tool is trusted with a real month, and that is a finding, not a failure to hide.
