# Budget Bot Skill — Design

**Date:** 2026-08-30
**Status:** Approved for planning

## Goal

A Claude skill that turns eight per-account CSV exports into one normalized,
categorized ledger in the schema `Timestamp, Transaction, Notes, Amount,
Category, Account`, ready to paste into an existing Google Sheet.

The value is not CSV munging. It is replaying a year of the user's own
categorization decisions so that a monthly task currently done by hand,
account by account, becomes a single review pass over the handful of
transactions that are genuinely new or genuinely ambiguous.

The design splits strictly along one line: **deterministic work is code,
judgment work is Claude.** Parsing eight dialects, normalizing signs, summing
amounts, and detecting duplicates are tested Python with no model in the
loop. Deciding whether a new merchant is Dining or Grocery is Claude's, and
every such decision is persisted so it is never asked twice.

## Non-Goals

Deliberately cut, and not to be added without a new design discussion:

- **Writing to Google Sheets via API.** No OAuth, no Google Cloud project.
  The skill emits a CSV; the human pastes it. Revisit only if the paste step
  becomes the bottleneck, which it is not today.
- **Downloading from banks.** No scraping, no Plaid, no credentials. The
  human downloads the eight CSVs as they do now.
- **Budgeting, forecasting, or spend analysis.** This produces a clean
  ledger. What the user does with it in the sheet is out of scope.
- **Splitting one transaction across multiple categories.**
- **Amount-based category inference** (a $12 Costco charge vs. a $240 one).
  A plausible signal, but a guess dressed as a rule; the ambiguity review
  already makes these cases cheap.
- **Populating `Notes`.** That column is the user's own annotation field.
  The pipeline always leaves it empty.

## Architecture

Four scripts, one skill file, and state that lives outside this repository.

| Component | Kind | Responsibility |
|---|---|---|
| `SKILL.md` | Instructions | Orchestrates the monthly run; owns the review conversation |
| `scripts/parse.py` | Deterministic | Route CSVs to accounts by header signature; map columns to the schema, descriptors verbatim |
| `scripts/normalize.py` | Deterministic | Merchant string → internal lookup key. Imported by categorize, bootstrap, and dedupe. **Never writes to output** |
| `scripts/categorize.py` | Deterministic | Apply merchant map in three tiers; emit the unresolved tail |
| `scripts/bootstrap.py` | Deterministic | One-time: build the merchant map from categorized history |
| `references/categories.md` | Instructions | The 15 categories and their boundary rules |

Claude's only irreducible role is adjudicating the tail that
`categorize.py` cannot resolve, and conducting the review conversation. It
does not parse, sum, or dedupe.

### State lives outside the repository

`agent-skills` is a public repository. The merchant map is a multi-year
record of where the user shops and `accounts.toml` names their financial
accounts. Both live in `~/.claude/budget-bot/`, following the same
convention as `lodging-scout`'s `~/.claude/lodging-profile.md`.

| Path | Contents |
|---|---|
| `~/.claude/budget-bot/accounts.toml` | Per-account column mappings |
| `~/.claude/budget-bot/merchant-map.csv` | Learned merchant → category map |
| `~/.claude/budget-bot/emitted.csv` | Hashes of rows already exported |

The repository holds only code, tests, and category definitions.

`~/Documents/budget-bot/` is separate and is **input, not state** — it is
the drop folder holding that month's raw bank exports. Nothing in the
pipeline writes to it, and its contents are disposable once a run
completes.

## Account Configuration

Files are routed to accounts by **header signature**, not filename. The user
drops all eight exports into a folder under whatever names the banks gave
them (`Chase_Activity20260830.CSV`, `export (3).csv`) and the parser matches
each file's header row against the configured signatures.

```toml
[[account]]
name          = "Chase Sapphire"
signature     = ["Transaction Date", "Post Date", "Description", "Amount"]
date_column   = "Transaction Date"
date_format   = "%m/%d/%Y"
description   = "Description"
amount_column = "Amount"
amount_sign   = "negative_is_charge"   # or "positive_is_charge"
```

`amount_sign` is per-account because institutions disagree: some export a
charge as `-42.10`, others as `+42.10`, and refunds invert either way. The
convention is normalized exactly once, here, so no downstream code and no
model reasoning ever revisits it.

Output convention, global: **charges positive, income negative, dates
`YYYY-MM-DD`.**

### Unknown files stop the run

The drop folder is dedicated to this process, so every file in it was put
there deliberately. An unmatched file is therefore a real event — a new
account, or a bank that changed its export — and never incidental clutter
to skip over. A CSV whose header matches no configured signature is never
heuristically mapped. The parser reports the filename and its header row; Claude then
walks the user through the new entry, appends it to `accounts.toml`, and
adds a fixture to `tests/`. This is also the recovery path for a bank
silently changing its export format, which happens.

### A malformed row halts the run

No skipping, no partial output. A transaction quietly dropped from a budget
is worse than a crash, because the user would reconcile against a total that
looks plausible.

## Merchant Normalization

The map is keyed on a normalized merchant, not the raw descriptor. Raw
strings carry per-transaction noise that would make every purchase look
novel:

```
SQ *BLUE BOTTLE 1123        → BLUE BOTTLE
TST* CHIPOTLE 0455          → CHIPOTLE
AMZN Mktp US*2H4KL9DJ3      → AMZN MKTP
```

Normalization uppercases, strips known payment-processor prefixes (`SQ *`,
`TST*`, `PY *`, `SP `), and trims trailing store and order identifiers.

### The normalized string never reaches the output

It is an internal lookup key and nothing else. The `Transaction` column of
the emitted CSV always carries the **exact descriptor from the account
export, byte for byte** — `SQ *BLUE BOTTLE 1123`, not `BLUE BOTTLE`. The
normalized form exists only in memory, as the key used to hit
`merchant-map.csv` and to build dedupe hashes, and as the merchant column
of the map itself.

This is a hard requirement, not a default. The sheet is a financial record
that must reconcile against statements, and a descriptor the bank never
issued cannot be looked up when a charge is disputed a year later. It is
also what keeps `bootstrap.py` correct on a re-run: it re-reads the sheet
and normalizes at read time, which only works if the sheet still holds raw
descriptors. A test asserts output descriptors are byte-identical to the
source rows.

**This function is the single point of failure for the entire design.** If
`bootstrap.py` normalizes a string differently than `categorize.py` does,
every key misses and the map is worthless. It therefore lives in one module,
`normalize.py`, imported by both, and carries the heaviest test coverage in
the project.

## Merchant Map and Ambiguity

```csv
merchant,category,seen,ambiguous
BLUE BOTTLE,Dining,14,false
COSTCO,Grocery,31,true
```

Flat, sorted, one merchant per line — hand-editable and diffable.

### Three matching tiers

1. **Exact key match, not ambiguous** → assigned silently. Expected to cover
   the large majority of rows.
2. **Fuzzy or prefix match** to a known merchant → assigned, but listed in
   the review table so a wrong collapse is visible.
3. **No match** → the tail. Claude proposes a category using
   `references/categories.md` and the merchant name.

### How a merchant becomes ambiguous

Some merchants legitimately split by trip — Costco, Amazon, Target. A
flagged merchant is **never** silently assigned; it appears in the review
table with its most common category pre-filled and the split shown
(`Costco → Grocery (usually; 13 of 31 were Household)`), so confirming costs
a glance and a silent wrong answer is impossible.

The flag is set three ways:

- **Inferred at bootstrap.** Flag when the runner-up category holds **≥20%
  of that merchant's rows and appears ≥3 times**. Below that threshold the
  minority is treated as a stale mis-tag and the majority wins. Borderline
  cases are listed for confirmation in one pass.
- **Learned from a correction.** If the map silently assigns a category and
  the user overrides it, `ambiguous` is set to true. One disagreement is
  sufficient evidence of a genuine split.
- **Declared.** The user says "always ask me about Amazon", or edits the CSV.

The 20%/3 threshold is the knob controlling monthly review size. Too loose
and half the merchants ask every month, defeating the purpose. Start there
and tune after the first live run; it is a constant, not a redesign.

### Clearing the flag

After five consecutive runs accepting the same category, Claude **offers**
to clear the flag. It never clears silently — a flag disappearing without
being asked for is precisely the kind of change the user would not notice.

## History Bootstrap

The user's existing sheet is already in the target schema, so `bootstrap.py`
reads it directly and does not involve the account parsers. It accepts one
export or several.

It groups categorized rows by normalized merchant, computes each merchant's
category distribution, applies the ambiguity threshold, and writes
`merchant-map.csv`.

**Recency beats volume on disagreement.** If a merchant was Dining for two
years and has been Grocery in every occurrence of the last six months, the
user re-categorized it deliberately. Take the recent answer and report the
override. Without this rule the map confidently replays a decision already
overturned.

**Dirty history is reported, not imported.** Blank categories, `Groceries`
vs. `Grocery`, and category values absent from the current 15 are listed for
resolution rather than silently becoming map entries. A year of manual work
contains typos.

`references/categories.md` is authored during implementation from the
distinct category values this pass finds in the user's real history, plus
the boundary rules needed to settle recurring judgment calls (for instance,
whether a prepared-food counter inside a grocery store is Grocery or
Dining). The canonical 15 come from the sheet, not from invention.

**Re-running does not clobber the map.** An existing `merchant-map.csv`
holds accumulated corrections that history does not contain. Bootstrap
writes to a new file and shows a diff; overwriting requires `--force`.

## Dedupe

No stable transaction ID exists across bank CSVs, so identity is the
composite `account | date | amount | normalized_merchant`. Two $4.50 coffees
at the same shop on the same day are two real transactions, so the check
counts occurrences rather than testing presence.

Dedupe is against **what has already been exported**, not within the file.
`emitted.csv` holds a hash per previously written row.

- **Exact match** → dropped, with a count reported.
- **Near match** — same account, amount, and merchant within **±4 days** →
  surfaced as a possible duplicate for the user to rule on. A pending charge
  downloaded on 8/30 can post as 9/2, changing the key. Never auto-dropped:
  silently deleting a real transaction is the worst outcome this pipeline
  can produce, worse than a duplicate, which a category total would reveal.

`emitted.csv` is the one piece of state that can drift from the actual
sheet. Every run prints the date range it emitted so drift is visible, and
`--since DATE` overrides the ledger.

## Workflow

1. User drops the eight CSVs into `~/Documents/budget-bot/` under whatever
   names the banks gave them. The path is overridable per run.
2. `parse.py` routes by signature and maps columns to the schema —
   descriptors copied verbatim, only dates and amount signs converted.
   Unknown header or
   malformed row halts the run.
3. Dedupe against `emitted.csv` → new rows plus possible-duplicate flags.
4. `categorize.py` applies the three tiers.
5. Claude presents **one** review table carrying everything needing a human:
   tier 2 and 3 categorizations, ambiguous merchants, possible duplicates,
   and parse warnings. Rows are numbered with proposals pre-filled.
6. User replies with corrections only — "4 is Gifts, 11 is Household".
   Silence accepts the remainder.
7. Write the output CSV, **then** write back to `merchant-map.csv` and
   append to `emitted.csv`, in that order, so a crash never records rows the
   user does not have.
8. Print the summary and sanity checks.

### Sanity checks

Run before the user pastes anything:

- **Account coverage.** Eight accounts configured, seven files matched. The
  likeliest error in the whole process and the cheapest to catch.
- **Per-category totals vs. the prior month**, flagging anything that
  doubles.
- **Row count and dollar total per account**, for reconciling against
  statements.

## Output

A single CSV at a user-specified path, columns exactly
`Timestamp, Transaction, Notes, Amount, Category, Account`, sorted by
timestamp then account, with `Notes` empty throughout and `Transaction`
holding the verbatim source descriptor. Ready to select and
paste as a block into the existing sheet.

## File Layout

```
budget-bot/
  SKILL.md
  README.md
  install_to_claude.sh
  scripts/
    parse.py
    normalize.py
    categorize.py
    bootstrap.py
  references/
    categories.md
    accounts.template.toml
    adding-an-account.md
  tests/
    test_normalize.py
    test_parse.py
    test_categorize.py
    test_dedupe.py
    fixtures/            one anonymized CSV per account dialect
```

## Risks

| Risk | Mitigation |
|---|---|
| Normalization diverges between bootstrap and monthly run | Single shared `normalize.py`; heaviest test coverage in the project |
| Over-aggressive normalization collapses distinct merchants | Tier 2 fuzzy matches always appear in the review table, never silent |
| `emitted.csv` drifts from the real sheet | Date range printed every run; `--since` override |
| A bank changes its export format | Signature mismatch halts loudly and starts the add-an-account flow |
| Bootstrap coverage is poor, making every month a slog | Measured up front by holdout validation before any live run |
| Private financial data committed to a public repo | All state in `~/.claude/budget-bot/`; test fixtures anonymized; the repo holds no ledger data by construction |

## Verification

**Holdout validation is the gate before first live use.** Hold out the most
recent month of history, build the map from everything prior, and measure
what fraction of that month it auto-categorizes correctly. This tests the
central premise — that past decisions predict future ones — before the user
trusts the pipeline. A result near 85% confirms the design; a result near
60% means normalization needs work, and that is known immediately rather
than after a disappointing first run.

Unit tests cover:

- `normalize.py` — processor prefixes, trailing IDs, casing, idempotence
- `parse.py` — one fixture per account dialect, both sign conventions,
  malformed row halts, unknown header halts, **emitted descriptors are
  byte-identical to the source CSV**
- `categorize.py` — all three tiers, ambiguity flag suppresses silent
  assignment, write-back correctness
- dedupe — exact drop, near-match surfaced not dropped, same-day
  same-amount distinct transactions preserved
