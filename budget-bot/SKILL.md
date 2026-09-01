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
python3 <skill-dir>/scripts/bootstrap.py history.csv --categories <skill-dir>/references/categories.md
```

`references/categories.md` is the single source of truth for category
names, and bootstrap reads it directly — no separate list to maintain.
Its bullet lines (`- **Name** — ...`) are what get parsed; headings, HTML
comments, and the boundary-rules prose are ignored automatically. On this
very first run the skeleton has no bullets yet, so there is nothing to
validate against — bootstrap says so and indexes whatever category values
appear in the history as-is. Report flagged, borderline, and
recency-override merchants, and resolve any odd-looking category values
with the user by eye. Write the confirmed category list into
`references/categories.md` as bullets, one per category, following the
skeleton's own comment.

By default bootstrap never overwrites an existing map: if
`~/.claude/budget-bot/merchant-map.csv` already exists, it writes
`merchant-map.new.csv` alongside it instead and tells you to diff the two.
Only pass `--force` once you've confirmed the new build should replace the
old one outright.

From then on, `references/categories.md` has real bullets, so the same
`--categories` flag on any later re-bootstrap (a fresh year of history, a
corrected export) validates for real: bootstrap reports every category
value in the history that doesn't match a bullet in the file, which is
what catches a typo or a retired category name before it gets silently
indexed as if it were real.

## The monthly run

### 1. Review

```bash
python3 <skill-dir>/scripts/run.py review
```

Reads `~/Documents/budget-bot/`. Pass `--drop <path>` for a different
folder. `--since YYYY-MM-DD` ignores `emitted.csv` entirely and takes
everything in the drop folder from that date forward — use it only when
asked to, such as backfilling after fixing a config, since it bypasses the
normal duplicate check. `--state` and `--work` override where state and
the intermediate work file live; leave them at their defaults for a normal
run.

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

Pass one `--set N=Category` per correction. Rows you proposed a category
for need no flag; rows the script could not resolve need one, and commit
refuses to write while any row lacks a category.

```bash
python3 <skill-dir>/scripts/run.py commit --out ~/Documents/budget-bot/2026-08.csv --set 4=Gifts --set 11=Household
```

Commit also refuses to write if a `--set` names a row number that doesn't
appear in the review table. That is deliberate: a mistyped row number
fails loudly instead of silently falling back to the tool's own guess for
that row.

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
