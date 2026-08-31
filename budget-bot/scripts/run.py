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
