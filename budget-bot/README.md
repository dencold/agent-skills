# budget-bot

Turns per-account CSV exports from banks and credit cards into one
normalized, categorized, deduplicated ledger ready to paste into a budget
spreadsheet. A merchant map learned from your own past categorizations
does most of the work; you only adjudicate what the map hasn't seen
before or what genuinely splits by category from trip to trip.

## Requires

- Python 3.11+, standard library only. No `pip install`, no MCP servers.

## Setup

1. Run the installer. It copies the skill to `~/.claude/skills/` and
   creates `~/.claude/budget-bot/` (state) and `~/Documents/budget-bot/`
   (the monthly drop folder) if they don't already exist:

   ```bash
   ./install_to_claude.sh
   ```

   It never touches an existing `accounts.toml` or `merchant-map.csv`,
   and re-running it is safe.

2. Create `accounts.toml`, which describes each bank/card export's column
   layout. It lives outside this repo on purpose — see below.

   ```bash
   cp references/accounts.template.toml ~/.claude/budget-bot/accounts.toml
   $EDITOR ~/.claude/budget-bot/accounts.toml
   ```

   One `[[account]]` block per account. See
   [`references/accounts.template.toml`](references/accounts.template.toml)
   for the field meanings, and
   [`references/adding-an-account.md`](references/adding-an-account.md)
   for what to do when a bank changes its export format or a new account
   shows up.

3. Bootstrap the merchant map from your existing budget sheet, so the
   first live month isn't a cold start. Export the sheet as CSV in the
   ledger schema (`Timestamp, Transaction, Notes, Amount, Category,
   Account`), then validate before trusting it:

   ```bash
   python3 scripts/bootstrap.py history.csv --holdout 2026-07
   ```

   This is the go/no-go gate: it trains on everything before the holdout
   month and scores the map against that month alone. A result near 85%
   means the merchant-normalization rules are working; a result far below
   that means normalization is mis-collapsing merchants and needs work
   before the tool is trusted with a real month — report the number
   honestly either way.

   Once the holdout number looks right, build the map for real:

   ```bash
   python3 scripts/bootstrap.py history.csv
   ```

   Resolve any category values the report calls out as odd or
   inconsistent, then write the confirmed category list into
   [`references/categories.md`](references/categories.md) — it starts as
   an empty skeleton; the canonical category list comes from your sheet,
   not from invention. From then on, pass `--categories` (a plain-text
   file, one category name per line) on any later re-bootstrap so a
   typo'd or retired category in the history gets reported instead of
   silently indexed as real.

## The monthly run

Drop the month's exports into `~/Documents/budget-bot/` under whatever
name the bank gave them — routing is by header, not filename — then ask
Claude to run the budget. It walks `SKILL.md`: `scripts/run.py review`
parses, dedupes, and categorizes everything it can, `scripts/run.py
commit` applies your corrections and writes the output CSV, and the
merchant map and dedupe ledger update themselves as a side effect of
`commit`. The output CSV is the deliverable — this skill deliberately
stops there rather than writing to Google Sheets itself.

## Where state lives, and why

Everything in `~/.claude/budget-bot/` — `accounts.toml`,
`merchant-map.csv`, `emitted.csv` — stays out of this repo. The merchant
map is a multi-year record of where its owner shops; `accounts.toml`
names their financial accounts. Neither belongs in a git history, personal
or shared.

`~/Documents/budget-bot/` is different: it's input, not state. It's just
where that month's raw bank exports land before a run.

## Verify

```bash
python3 -m unittest discover -s tests   # 101 tests
```

The suite covers parsing, normalization, dedupe, categorization, and the
review/commit flow. It cannot check whether your holdout coverage is
actually good — that's what step 3 of setup is for, against your real
history, not the test fixtures.
