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
