---
name: lodging-search
description: Use when planning where to stay on a trip — searches hotels, Airbnb, and VRBO for a given party, dates, and location, then returns ranked stays with true total-stay cost and honest pros and cons. Accounts for hotel loyalty status, brand preferences, and family amenities like pools.
---

# Lodging Search

Anyone can pull a list of listings. The value here is judgment: normalizing
wildly different fee structures into comparable totals, recognizing when a
party size forces a two-room or rental decision, and weighing loyalty perks
against a cheaper competitor.

## Setup

Paths below are relative to this skill's own base directory (Claude is
told that directory when the skill loads — substitute it in). Never treat
them as relative to the current working directory: the skill runs with cwd
set to the user's project, not to wherever the skill is installed.

Read `~/.claude/lodging-profile.md`. If it does not exist, walk the user
through creating one from `references/profile.template.md`, then continue.
The profile lives outside this repo on purpose — see the template.

Check the three capabilities in `references/mcp-setup.md`. If any is
missing or broken, **say so plainly**, show the install command, and ask
whether to proceed single-channel or stop. Never return a quiet
half-search presented as complete — a shortlist that silently omits every
hotel is worse than no shortlist, because it looks finished.

## 1. Resolve inputs

Fill gaps from the profile; ask only for what remains.

Needed: adults, kids **with ages**, check-in and check-out, location, and
the trip anchor (the thing the trip is organized around — a beach, a
venue, grandma's house). Budget ceiling for the stay is optional but
worth prompting for — don't block the search on a user without a number
in mind.

Convert relative dates ("spring break") to absolute ones. Confirm the
resolved parameters back in one line before searching; a wrong date range
wastes the whole search.

## 2. Occupancy check — before any search

`scripts/lodging_calc.py` is skill-relative (see Setup) — resolve it
against this skill's own base directory, not the cwd:

```bash
python3 -c "
import sys; sys.path.insert(0, '<skill-dir>/scripts')
from lodging_calc import rooms_needed
print(rooms_needed(adults=2, kids_ages=[4,6,9]))
"
```

If `forces_multi_room` is true, **say so explicitly** before showing
results. A party of 5 cannot use one standard room; its real choice is two
rooms, a suite, or a rental. Once hotels are priced at two rooms, rentals
frequently win outright, and burying that in the numbers hides the actual
decision.

If `verify_infant_policy` is true, flag that the infant exclusion needs
confirming with the specific brand.

Where a suite or connecting rooms would beat two separate rooms, search
those as a distinct option.

## 3. Search all three channels in parallel

Query `airbnb_search`, `vrbo_search`, and `hotel_search` concurrently with
the resolved party size and dates. Hotels are searched at `rooms` from
step 2. Deduplicate cross-listed properties per `references/mcp-setup.md`.

A channel that returns zero results is not automatically a healthy
"nothing available" — see the blocked-vs-genuinely-empty distinction in
`references/mcp-setup.md` and report the two differently.

## 4. Normalize to true total cost

Never compare nightly rates. Use `scripts/lodging_calc.py`:
`rental_total()` and `hotel_total()` for the all-in total, then
`effective_cost()` to apply loyalty perk value on top of it.

Pass `provider_total` whenever the provider returned an all-in figure — it
wins and is authoritative. Only compose from parts when it is absent, and
mark composed figures with `~` in output, since `is_estimate` will be true.

Only include a parking cost for hotels when the party is driving —
`hotel_total`'s `parking_per_night` should be left at its default
otherwise. Check the profile's `Driving or flying` field before pricing
parking in.

This step exists to catch one inversion: a $180/night rental with a $200
cleaning fee loses to a $210/night hotel over three nights.

## 5. Score against the profile

Apply `apply_gates()` first, then the weighted rubric in
`references/ranking.md`. Run the seasonal amenity check — a listing's
amenity list describes the property year-round, and an outdoor pool in
October is closed.

## 6. Present

**Top 10 by default.** The count is configurable two ways: the profile's
`Results per page`, and a per-request override in plain language ("top 5",
"give me 20"). The per-request value applies to that search only and does
not overwrite the profile default.

Each entry carries: rank, name, channel, true total cost, effective cost
after perks where loyalty applies, 2–3 pros, 1–2 **genuine** cons, and a
booking link.

Report `total_cost` and `effective_cost_after_perks` as separate numbers,
never blended.

Close with the price-check timestamp and state that prices are
point-in-time and must be verified at booking.

## 7. Paginate on request

A follow-up — "show me the next 10", "what else is there" — serves the next
page **from the saved trip file without re-searching**.

In a fresh session there is no page 1 on screen to continue from, so
locate the trip file first: glob `~/.claude/lodging-trips/*<location>*.md`,
take the most recently modified match, and state plainly which trip was
resumed (location and dates) before serving the next page.

Re-searching is wrong here, not merely wasteful: prices move and providers
return non-deterministic result sets, so a fresh query produces a page 2
inconsistent with the page 1 already on screen, possibly duplicating it.
Ranking a stay against its alternatives only works if everything was priced
at the same moment.

- **Never repeat a shown property.** Track presented ranks.
- **State the position** — "11–20 of 34 candidates".
- **Say when the pool runs dry.** Show what remains, then offer the
  concrete widenings: raise the budget ceiling, extend the radius, relax a
  gate, shift dates.
- **Re-search only when parameters change.** Different dates, party size,
  or budget make a new search — say so rather than paginating into a stale
  pool.
- **Quality declines down the list.** When remaining candidates are
  materially weaker, say it plainly instead of presenting rank 27 with the
  confidence of rank 2.

## Output

1. **Terminal** — the ranked page plus pros/cons. Always.
2. **Saved markdown** — `~/.claude/lodging-trips/YYYY-MM-DD-<location>.md`,
   every run, where `YYYY-MM-DD` is the date the search was run, not the
   check-in date. Holds the **complete ranked pool**, not just the shown
   page, plus resolved parameters and the price-check timestamp. This is
   what makes pagination work in a later session, and it builds a
   diffable record of price movement across repeated searches for one
   trip.
3. **Artifact** — a shareable comparison page, on request only. Artifact
   CSP blocks remote images, so these carry text, prices, and links, never
   property photos.

### Trip file format

```markdown
# <Location> — <check-in> to <check-out>

**Searched:** <ISO timestamp>
**Party:** N adults, M kids (ages ...) — rooms needed: R
**Budget ceiling:** $X
**Anchor:** <trip anchor>
**Providers used:** airbnb_search (openbnb), vrbo_search (printing-press), hotel_search (hotel-goat)
**Shown through rank:** 10

## Ranked pool

### 1. <Name> — hotel — $1,240.00 total / $1,060.00 after perks
- Link:
- Pros:
- Cons:
- Flags:

### 2. ...
```

`Shown through rank` is what pagination reads to find where to resume.
Update it each time a page is served.

## Never

- Book anything. Recommend; the human books.
- Present a con-free option.
- Blend perk value into the price.
- Report a composed estimate as if it were a provider total.
- Model points-versus-cash award redemptions. Perk value in dollars is in
  scope; deciding whether to burn points is not.
- Plan flights, ground transport, or itineraries. This skill finds a place
  to sleep.
