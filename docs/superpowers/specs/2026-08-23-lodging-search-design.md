# Lodging Search Skill — Design

**Date:** 2026-08-23
**Status:** Approved for planning

## Goal

A Claude skill that takes a party (adults + kids with ages), dates, and a
location, then returns a ranked shortlist of places to stay — hotels
alongside Airbnb and VRBO rentals — with the true total cost of the whole
stay and an honest set of pros and cons for each.

The skill's value is judgment, not scraping. Anyone can pull a list of
listings. The hard parts are normalizing wildly different fee structures
into comparable totals, recognizing when a party size forces a two-room or
rental decision, and weighing loyalty perks against a cheaper competitor.

## Non-Goals

Deliberately cut, and not to be added without a new design discussion:

- Points-versus-cash award redemption modeling
- Flights, ground transport, or itinerary building
- Automated booking. The skill recommends; the human books.
- Long-term rental or relocation search
- Group/event block booking

## Inputs

Required, gathered conversationally — the skill asks only for what it does
not already have:

| Input | Notes |
|---|---|
| Adults | Count |
| Kids | Count **and ages**. Ages drive occupancy rules, crib needs, and pool suitability. |
| Check-in / check-out | Absolute dates. Resolve relative phrasing ("spring break") before searching. |
| Location | City, neighborhood, or an anchor ("near Legoland") |
| Budget ceiling | Total for the stay, not nightly. Optional but strongly prompted for. |
| Trip anchor | Optional. The thing the trip is organized around — a beach, a venue, grandma's house. Drives the location score. |

## Architecture: Capability Contract

The skill does **not** hard-code specific MCP servers. Every provider in
this space is a scraper; they break when Airbnb or Booking.com changes
their DOM, and they get abandoned and replaced. Hard-coding one means a
skill rewrite each time.

Instead the skill declares three capabilities and probes at runtime for
whichever server satisfies each:

| Capability | Purpose | Known providers (2026-08) |
|---|---|---|
| `airbnb_search` | Airbnb listings + details | `@openbnb/mcp-server-airbnb` (no API key) |
| `vrbo_search` | VRBO listings + details | `@mvanhorn/printing-press-library` `airbnb` tool — searches Airbnb **and** VRBO in parallel and surfaces host direct-booking sites |
| `hotel_search` | Hotel rates | `hotel-goat` (Google Hotels + Trivago, OTA-aggregated); `hotels-skill` (Booking.com via Playwright); HotelZero |

`references/mcp-setup.md` holds install and config snippets for each, plus
a short note on how to verify a provider is alive.

Note the overlap: the printing-press tool covers both Airbnb and VRBO, so
it can satisfy `airbnb_search` and `vrbo_search` together. When both it and
the openbnb server are installed, prefer openbnb for Airbnb (more detailed
listing data) and printing-press for VRBO. Deduplicate by property address
and name — the same house is often listed on both platforms at different
prices, and when that happens the price gap is itself a finding worth
reporting.

### Noisy Failure

If a capability is unavailable — not installed, erroring, or returning
zero results when results are expected — the skill states plainly which
channel is missing, shows the install command, and asks whether to proceed
single-channel or stop. It never returns a quiet half-search presented as a
complete one. A shortlist that silently omits every hotel is worse than no
shortlist, because it looks finished.

Distinguish two zero-result cases and report them differently:

- **Blocked or broken** — provider errored, timed out, or returned
  malformed data. Report as a tooling failure.
- **Genuinely empty** — provider responded correctly with no availability.
  Report as a trip-planning fact, and suggest loosening dates or radius.

## Profile

The real profile lives at `~/.claude/lodging-profile.md`, **outside this
repository**. The repo is public; loyalty account standing and children's
ages do not belong in it. The repo ships `references/profile.template.md`.
On first run, if no profile exists, the skill walks the user through
creating one, then reads it on every subsequent run.

Profile contents:

```markdown
## Household
- Adults: 2
- Kids: ages 6, 9 (update annually)
- Notes: early bedtimes; need room-darkening / separate sleeping space

## Hotel loyalty
- Marriott Bonvoy — Titanium — perks: lounge access, suite upgrades, late checkout
- Hilton Honors — Gold — perks: free breakfast, room upgrade
- (brand, status tier, and the perks that tier actually delivers)

## Brand preferences
- Prefer: <brands>
- Avoid: <brands, and why>

## Amenity priorities
- Must have: <hard requirements>
- Strongly prefer: heated pool, in-unit laundry, full kitchen
- Nice to have: <...>

## Defaults
- Typical budget range per night
- Driving vs. flying (drives the parking-fee calculation)
- Cancellation flexibility tolerance
```

The perks list matters: the skill converts status into dollars, so it needs
to know what each tier actually delivers, not just the tier name.

## Workflow

### 1. Resolve inputs

Fill gaps from the profile, ask for what remains. Convert relative dates to
absolute. Confirm the resolved parameters back to the user in one line
before searching — a wrong date range wastes the entire search.

### 2. Occupancy check — before any search

This runs first because it reshapes the whole comparison, and it is the
step naive searches skip.

- Standard hotel rooms cap at **4 occupants**; many cap at 3, and the cap is
  legally enforced, not a suggestion.
- Children under 2–3 often do not count toward occupancy, but this varies by
  brand and country. Treat as a flag to verify, never as a silent assumption.
- `rooms_needed = ceil(party_size / max_occupancy)`

A party of 5 cannot stay in one standard room. That party's real choice is
two rooms, a suite, or a rental — and once the hotel side is priced at two
rooms, rentals frequently win outright. Surface this conclusion explicitly
rather than burying it in the numbers.

Where a suite or connecting rooms would beat two separate rooms, search for
those as a distinct option.

### 3. Search channels in parallel

Query `airbnb_search`, `vrbo_search`, and `hotel_search` concurrently with
the resolved party size and dates. Hotels are searched at `rooms_needed`.

### 4. Normalize to true total cost

The heart of the skill. Nightly rates are not comparable across channels.

**Rentals:**
```
nightly_rate × nights
+ cleaning fee            (one-time — dominates short stays)
+ service fee             (~14% on Airbnb, varies)
+ occupancy/lodging taxes (~10–15%, jurisdiction-dependent)
+ pet fee                 (if applicable)
− long-stay discount      (weekly/monthly thresholds)
```

**Hotels:**
```
(nightly_rate × nights × rooms_needed)
+ resort/destination fee × nights × rooms
+ parking × nights        (only if driving)
+ taxes                   (~12–18%)
```

Two rules govern this calculation:

**Prefer the provider's total.** When a provider returns an all-in total,
use it and label it as authoritative. Compose from parts only when no total
is available, and mark composed figures as estimates with `~`.

**Never blend perk value into price.** Report `total_cost` and
`effective_cost_after_perks` as two separate numbers. Free breakfast for
four is roughly $60/day of real money and belongs in the decision — but
silently subtracting it from the price makes the skill's output impossible
to reconcile against a booking page, which destroys trust in every other
number it prints.

The inversion this step exists to catch: a $180/night rental with a $200
cleaning fee loses to a $210/night hotel over three nights. That reversal
is invisible until fees are normalized, and it is the single most common
way families overpay.

### 5. Score against profile

**Hard gates — eliminate, do not down-rank:**
- Cannot sleep the entire party
- Exceeds the stated budget ceiling
- Unavailable for the requested dates
- Rating below the floor, after normalizing to a 5-point scale

Ratings are not comparable as returned. Airbnb reports out of 5,
Booking.com out of 10, Google out of 5 with different distribution.
Normalize every rating to a 5-point scale before applying any threshold,
and set the floor per channel rather than globally — a 4.0 hotel is
unremarkable, while a 4.0 Airbnb is a warning.

Thin review counts are a **flag, not a gate**. A listing with six reviews
may be excellent and newly listed; eliminating it silently discards good
options. Surface it, rank it slightly lower, and name the thin review
history as one of its cons so the user decides.

**Weighted scoring for everything that survives:**
- True total cost against budget — heaviest weight
- Kid amenities: **heated** pool, not merely "pool"; kiddie/shallow area;
  kitchen; in-unit laundry; crib availability
- Loyalty: rank boost for status brands, plus quantified perk value
- Location: distance to the trip anchor, walkability, area at night
- Review quality: rating weighted by review count
- Cancellation flexibility

**Seasonal amenity check.** An outdoor pool in a cold-climate city in
October is closed. Verify pool and amenity availability against the travel
dates rather than trusting the listing's amenity list, which describes the
property year-round.

### 6. Present

Top 5, ranked, each with true total cost, 2–3 pros, 1–2 **genuine** cons,
and a booking link.

Every option gets at least one real con. If no downside can be named, the
listing has not been examined closely enough — a con-free recommendation is
a research failure, not an endorsement. Where two options are genuinely
close, say so rather than manufacturing a ranking gap.

Include the price-check timestamp and state that prices are point-in-time
and must be verified at booking.

## Output

1. **Terminal** — ranked comparison table plus pros/cons. Always.
2. **Saved markdown** — `trips/YYYY-MM-DD-<location>.md`, every run. Builds
   a diffable record across searches for the same trip, which is how price
   movement becomes visible.
3. **Artifact** — a shareable comparison page, on request only. Note that
   artifact CSP blocks remote images, so these pages carry text, prices,
   and links, never property photos.

## File Layout

```
lodging-search/
  SKILL.md                    # workflow, gates, presentation rules
  references/
    profile.template.md       # copied to ~/.claude/lodging-profile.md
    ranking.md                # scoring rubric, perk dollar values, gates
    mcp-setup.md              # per-provider install + health check
  trips/                      # gitignored
```

Add `lodging-search/trips/` to `.gitignore`.

## Risks

**Provider fragility.** Every data source is a scraper subject to blocking
and DOM changes. Mitigated by the capability contract, noisy failure, and
per-provider health checks — not eliminated. Expect to swap providers.

**Price staleness.** Prices are point-in-time and change between search and
booking. Every result carries a timestamp and a link; the skill never
implies a quoted price is held.

**Fee estimation error.** Composed totals are estimates. Marked as such,
and the provider's own total always wins when available.

**Profile drift.** Kids' ages and loyalty status change. The template notes
that ages need an annual update.

## Verification

Prose, not code — verification means running the skill end to end and
reconciling its output against reality. Three scenarios:

1. **Party of 5** — forces the two-room-versus-rental decision. Confirm the
   occupancy check fires before the search and that hotels are priced at
   two rooms.
2. **Bonvoy-heavy downtown** — confirm loyalty boost and perk quantification
   appear, and that `total_cost` and `effective_cost_after_perks` are both
   reported and reconcilable.
3. **Beach town, short stay** — confirm the cleaning-fee inversion is caught
   and that a hotel can beat a lower-nightly-rate rental.

For each: open the actual booking pages and check the skill's totals
reconcile. Also test the degraded path by disabling one provider and
confirming the failure is loud.
