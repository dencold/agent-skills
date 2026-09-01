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
from merchant_map import CLEAR_FLAG_STREAK, load_map, record_decision, save_map
from parse import ParseError, load_accounts, parse_folder

DOUBLING_FACTOR = 2.0


class UsageError(ValueError):
    """Bad command-line input, reported before any work or any state write."""


class OverrideError(UsageError):
    """A malformed --set argument on the command line."""


def _month_before(month_key):
    first = date.fromisoformat(f"{month_key}-01")
    return (first - timedelta(days=1)).strftime("%Y-%m")


def summarize(rows, emitted, missing_accounts):
    """Sanity checks run before the user pastes anything into the sheet.

    The comparison month is derived from the batch, not from today's date:
    the run happens in September for August's transactions, so "the month
    before today" is August -- the batch's own month -- and every category
    would be compared against itself.

    Magnitudes are compared because income is negative by convention. A raw
    `total > prior * 2` is backwards for every income category: pay tripling
    goes unflagged while pay collapsing is reported as a doubling.
    """
    by_account = defaultdict(lambda: Decimal("0"))
    counts_by_account = defaultdict(int)
    by_category = defaultdict(lambda: Decimal("0"))
    for row in rows:
        by_account[row.account] += row.amount
        counts_by_account[row.account] += 1
        by_category[row.category or "(uncategorized)"] += row.amount

    months = sorted(row.timestamp[:7] for row in rows if row.timestamp)
    prior_key = _month_before(months[0]) if months else None

    prior = defaultdict(lambda: Decimal("0"))
    if prior_key:
        for record in emitted:
            if record.timestamp.startswith(prior_key) and record.category:
                prior[record.category] += record.amount

    factor = Decimal(str(DOUBLING_FACTOR))
    doubled = [
        category for category, total in by_category.items()
        if prior.get(category) and abs(total) > abs(prior[category]) * factor
    ]

    stamps = [row.timestamp for row in rows if row.timestamp]
    return {
        "by_account": dict(by_account),
        "counts_by_account": dict(counts_by_account),
        "by_category": dict(by_category),
        "prior_month": prior_key,
        "prior_by_category": dict(prior),
        "doubled": sorted(doubled),
        "missing_accounts": missing_accounts,
        "date_range": [min(stamps), max(stamps)] if stamps else None,
        "total": sum((r.amount for r in rows), Decimal("0")),
    }


def review(drop, state, work, since=None):
    """Phase one: parse, dedupe, categorize, write the work file."""
    state = pathlib.Path(state)
    drop = pathlib.Path(drop)

    if since is not None:
        # --since is compared lexicographically against ISO timestamps, so
        # an unpadded or misspelled date silently matches nothing and the
        # run reports "0 new transactions" -- which reads as "nothing to do".
        try:
            date.fromisoformat(since)
        except ValueError:
            raise UsageError(
                f"--since {since!r} is not a date; expected YYYY-MM-DD "
                f"(zero-padded, e.g. 2026-08-01)") from None

    if not drop.is_dir():
        raise UsageError(
            f"drop folder {drop} does not exist. Create it and put this "
            f"month's exports in it, or pass --drop with the right path.")

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
        "since": since,
    }
    work = pathlib.Path(work)
    work.parent.mkdir(parents=True, exist_ok=True)
    work.write_text(json.dumps(payload, indent=2))

    _print_review(payload)
    return 0


def _print_review(payload):
    rows = payload["rows"]
    if payload.get("since"):
        # Under --since the partition ran against an empty ledger, so the
        # duplicate count is structurally zero. Printing it would read as
        # "dedupe checked and found nothing", the opposite of the truth.
        print(f"{len(rows)} transactions from {payload['since']} forward")
        print(f"  --since bypassed emitted.csv entirely: no duplicate check "
              f"ran, and rows exported by an earlier run CAN be re-emitted.")
    else:
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


def _consumed_path(work):
    """Where a work file is parked once its rows have been committed."""
    return work.with_suffix(".committed.json")


def commit(work, state, out, overrides, recommit=False):
    """Phase two: apply corrections, write output, then update state."""
    state = pathlib.Path(state)
    out = pathlib.Path(out)
    work = pathlib.Path(work)

    # Re-running commit on the same work file -- plausible when the user
    # spots a wrong category and asks for another --set -- would append
    # every row to emitted.csv a second time and re-fold every decision
    # into the map. The work file is therefore consumed on success.
    consumed = _consumed_path(work)
    if not work.exists() and consumed.exists():
        if not recommit:
            raise UsageError(
                f"{work} was already committed (moved to {consumed}). "
                f"Re-run `review` for a fresh work file, or pass --recommit "
                f"to write it again -- which will duplicate the rows it "
                f"already exported.")
        work = consumed

    if not work.exists():
        raise UsageError(f"no work file at {work}; run `review` first.")

    if not out.parent.is_dir():
        raise UsageError(
            f"cannot write {out}: the directory {out.parent} does not exist.")

    payload = json.loads(work.read_text())

    # A stale or mistyped --set row number must never be silently discarded
    # in favor of the proposed category: that is a deliberate correction
    # the user made, reversed without any complaint.
    known_numbers = {record["n"] for record in payload["rows"]}
    unknown_overrides = sorted(n for n in overrides if n not in known_numbers)
    if unknown_overrides:
        print("Refusing to write: these --set row numbers do not exist "
              "in the work file.", file=sys.stderr)
        for n in unknown_overrides:
            print(f"  {n}", file=sys.stderr)
        raise SystemExit(1)

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

    # Read before appending: the prior-month comparison must not see this
    # batch, and append_emitted is about to put it there.
    emitted_before = load_emitted(state / "emitted.csv")

    # Output first. State that under-claims produces a visible duplicate;
    # state that over-claims silently loses a transaction.
    write_ledger(out, ledger_rows)

    entries = load_map(state / "merchant-map.csv")
    # The streak is "consecutive runs the user accepted this category", not
    # consecutive transactions: five Costco trips in one month are one
    # human confirmation, not five.
    bumped = set()
    for record, row in rows:
        merchant = record["merchant"]
        record_decision(entries, merchant, row.category,
                        count_streak=merchant not in bumped)
        bumped.add(merchant)
    clearable = sorted(m for m in bumped
                       if m in entries and entries[m].clearable())
    save_map(state / "merchant-map.csv", entries)

    append_emitted(state / "emitted.csv", ledger_rows)

    # Only now is the work file spent. Doing this last keeps the refusal
    # paths above re-runnable.
    if work != consumed:
        work.replace(consumed)

    summary = summarize(ledger_rows, emitted_before, payload["missing_accounts"])
    print(f"\nWrote {len(ledger_rows)} rows to {out}")
    if summary["date_range"]:
        print(f"  covering {summary['date_range'][0]} to {summary['date_range'][1]}")
    if summary["missing_accounts"]:
        print(f"  WARNING no file matched: {', '.join(summary['missing_accounts'])}")
    for account, total in sorted(summary["by_account"].items()):
        print(f"  {account}: {summary['counts_by_account'][account]} rows, {total:.2f}")
    for category in summary["doubled"]:
        print(f"  WARNING {category} more than doubled vs "
              f"{summary['prior_month']}")
    if clearable:
        print(f"  confirmed {CLEAR_FLAG_STREAK} runs running, offer to clear "
              f"the ambiguity flag: {', '.join(clearable)}")
    return 0


def _parse_overrides(pairs):
    """Turn --set N=Category strings into {int: str}, or refuse cleanly."""
    overrides = {}
    for pair in pairs or []:
        number, sep, category = pair.partition("=")
        if not sep:
            raise OverrideError(
                f"--set {pair!r} is missing '='; expected N=Category")
        try:
            n = int(number)
        except ValueError:
            raise OverrideError(
                f"--set {pair!r}: {number!r} is not a row number") from None
        overrides[n] = category
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
    commit_cmd.add_argument("--recommit", action="store_true",
                            help="commit a work file that was already "
                                 "committed, duplicating its rows")

    args = parser.parse_args(argv)

    try:
        if args.command == "review":
            return review(args.drop, args.state, args.work, args.since)
        overrides = _parse_overrides(args.set)
        return commit(args.work, args.state, args.out, overrides,
                      recommit=args.recommit)
    except (ParseError, UsageError) as exc:
        print(f"\nSTOPPED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
