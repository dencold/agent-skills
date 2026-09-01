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

   **If the number is short, fix `scripts/normalize.py` before building
   the map, never after.** Normalization is the key for both
   `merchant-map.csv` and the dedupe hashes in `emitted.csv`; changing it
   later invalidates every entry in both, so the map stops matching and
   dedupe stops recognizing rows that were already exported and pasted.
   The first thing to try is stripping *interior* store numbers — today
   only a trailing run of noise tokens is removed, so `COSTCO WHSE #0455
   SEATTLE WA` keeps its store number and every location becomes its own
   key. It is deliberately left untuned until it is measured against real
   descriptors: collapsing two genuinely different merchants is a silent
   miscategorization, while fragmenting one merchant only costs a review
   row.

   Once the holdout number looks right, build the map for real:

   ```bash
   python3 scripts/bootstrap.py history.csv --categories references/categories.md
   ```

   `references/categories.md` is the single source of truth for category
   names — bootstrap reads it directly, so there's no separate list to
   keep in sync. It starts as an empty skeleton (the canonical category
   list comes from your sheet, not from invention), so this first run has
   nothing to validate against yet and indexes the history's category
   values as-is. Resolve any values the report calls out as odd or
   inconsistent, then write the confirmed list into
   [`references/categories.md`](references/categories.md) as bullets, one
   per category. From then on, the same `--categories` flag on any later
   re-bootstrap validates for real: a typo'd or retired category in the
   history gets reported instead of silently indexed as real.

## The monthly run

Drop the month's exports into `~/Documents/budget-bot/` under whatever
name the bank gave them — routing is by header, not filename — then ask
Claude to run the budget. It walks `SKILL.md`: `scripts/run.py review`
parses, dedupes, and categorizes everything it can, `scripts/run.py
commit` applies your corrections and writes the output CSV, and the
merchant map and dedupe ledger update themselves as a side effect of
`commit`. The output CSV is the deliverable — this skill deliberately
stops there rather than writing to Google Sheets itself.

`commit` consumes its work file — on success `work.json` becomes
`work.committed.json` and a second `commit` on it is refused, so spotting
a wrong category afterwards and re-running can't export the whole month
twice. Re-run `review` for a fresh work file instead; the rows already
exported are deduped away.

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
python3 -m unittest discover -s tests   # 137 tests
```

The suite covers parsing, normalization, dedupe, categorization, and the
review/commit flow. It cannot check whether your holdout coverage is
actually good — that's what step 3 of setup is for, against your real
history, not the test fixtures.
