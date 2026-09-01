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
from ledger import Row, parse_money

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


class DuplicateExportError(ParseError):
    def __init__(self, duplicates):
        self.duplicates = duplicates
        lines = "\n".join(
            f"  {name}: {', '.join(files)}" for name, files in sorted(duplicates.items())
        )
        super().__init__(
            "two or more files in the drop folder belong to the same account.\n"
            f"{lines}\n"
            "  Every row in them would be counted twice, and in-batch rows are\n"
            "  never deduplicated. Delete the redundant download -- typically\n"
            "  the 'name (1).csv' copy -- and run again."
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
                amount = parse_money(record.get(account.amount_column))
            except ValueError as exc:
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
    matched = {}

    # One case-insensitive pass. Globbing "*.csv" and "*.CSV" separately
    # returns each file twice on macOS, doubling every transaction.
    exports = sorted(p for p in folder.iterdir()
                     if p.is_file() and p.suffix.lower() == ".csv")
    for path in exports:
        with open(path, newline="", encoding="utf-8-sig") as handle:
            header = next(csv.reader(handle), [])
        account = match_account(header, path.name, accounts)
        matched.setdefault(account.name, []).append(path.name)
        rows.extend(parse_file(path, account))

    # The mirror image of a missing account, and far quieter: a second
    # download of one account doubles it, and most of those rows are tier-1
    # silent so they never reach the review table to be noticed.
    duplicates = {name: files for name, files in matched.items() if len(files) > 1}
    if duplicates:
        raise DuplicateExportError(duplicates)

    missing = [a.name for a in accounts if a.name not in matched]
    return rows, missing
