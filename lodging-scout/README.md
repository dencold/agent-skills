# lodging-scout

Searches hotels, Airbnb, and VRBO for a party, dates, and location, then
returns ranked stays with true total-stay cost and honest pros and cons.
Accounts for hotel loyalty status, brand preferences, and family amenities.

Note that this skill is best used in Claude Code — it requires local MCP 
servers and the profile in `~/.claude/`. See the note in [the top-level README](../README.md#lodging-scout) 
for more details.

## Requires

- Python 3 for `scripts/lodging_calc.py` (standard library only)
- Node / `npx` for the Airbnb MCP server
- The Claude in Chrome extension, with permission for `vrbo.com`
- Three search channels: `airbnb_search` and `hotel_search` over MCP, plus
  VRBO read from the browser. The skill probes for all three at runtime and
  refuses to quietly return a single-channel search.

## Setup

1. Run the installer. It copies the skill to `~/.claude/skills/`, adds the
   MCP servers at user scope, and tells you if the profile is missing:

   ```bash
   ./install_to_claude.sh
   ```

   It never touches an existing profile, and re-running it is safe.

2. Create the profile, which lives outside this repo on purpose (it holds
   loyalty standing and kids' ages):

   ```bash
   cp references/profile.template.md ~/.claude/lodging-profile.md
   $EDITOR ~/.claude/lodging-profile.md
   ```

3. Nothing to create for output: trip files are written to
   `~/.claude/lodging-trips/` on the first run, and later sessions paginate
   from them.

Why VRBO is different: every VRBO MCP provider is currently blocked by
Akamai, so the skill drives `vrbo.com` in your logged-in browser instead.
That is slower than an MCP call, which is why step 3 of the skill starts it
in the same tool block as the two fast ones. Details and the provider table
are in [`references/mcp-setup.md`](references/mcp-setup.md).

## Verify

```bash
python3 -m unittest discover -s tests   # 46 tests
```

Then walk the six scenarios in
[`references/verification.md`](references/verification.md) — they check the
parts the unit tests cannot, like whether the two-room-versus-rental tradeoff
actually gets stated out loud.

## Not built yet: direct-booking lookup

`install_to_claude.sh` installs `@mvanhorn/printing-press-library`, but the
skill only uses it as a search channel today. The package also ships
per-listing commands that the skill ignores, and one of them is a real gap:

- **`cheapest`** — given an Airbnb or VRBO listing URL, finds the host's own
  direct-booking site and compares it against the OTA price.
- **`match`** — finds the same property on the other platform via geocode,
  amenities, and photo signal.
- **`host portfolio`** — every listing under one host or management company.

`cheapest` fits this skill's whole thesis. Airbnb's guest service fee runs
roughly 14–16%, so a host's own site can undercut the OTA total outright —
the same category of finding as the cleaning-fee inversion in step 4 of
`SKILL.md`, and the skill currently misses it. `match` would strengthen
deduplication, which today keys on address and name; photo-signal matching
is a good deal better at catching the same condo listed twice.

These are not search — they take a listing URL as input, one CLI invocation
each, so they cannot go into step 3 alongside `airbnb_search`. The shape
that likely works is a pass between ranking (step 5) and presentation
(step 6), run against the top 3–5 only, adding a direct-booking line to
those entries when it beats the OTA total.

Open questions to settle before building it:

- Does a cheaper direct price **re-rank** the list, or only annotate the
  entry? Re-ranking is more useful and makes the totals harder to trust,
  since they now come from two different pricing methodologies.
- How should the output handle the trust difference? Booking on Airbnb
  carries platform protections that a host's own site does not, so a pure
  dollar comparison overstates the case. This is a `cons` line, not a
  footnote.
- Does the saved trip file record the direct-booking price, and does
  pagination re-check it on later pages, or is it top-N only forever?

Prerequisite: the `airbnb-pp-cli` binary must be on `PATH`. It installs to
`~/.local/bin`, which is not there by default — see the PATH note in
[`references/mcp-setup.md`](references/mcp-setup.md).
