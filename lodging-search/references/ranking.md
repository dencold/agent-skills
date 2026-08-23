# Ranking

## Hard gates — eliminate, never down-rank

Computed by `scripts/lodging_calc.py::apply_gates`:

- Cannot sleep the entire party
- Exceeds the stated budget ceiling
- Unavailable for the requested dates
- Rating below the per-channel floor, after normalizing to a 5-point scale
  (unrated listings — no rating available — skip this gate and are flagged
  `unrated` instead of eliminated)

Applied by hand from the profile, not by `apply_gates` — the candidate
dict carries no amenity data:

- Any profile "Must have" amenity is absent

Rating floors: Airbnb and VRBO 4.5, hotels 4.0. A 4.0 hotel is
unremarkable; a 4.0 Airbnb is a warning.

Thin review history (under 10 reviews) is a **flag, not a gate**. Rank it
slightly lower and name it as one of the property's cons.

### Candidate dict shape

`apply_gates(candidate, budget_ceiling=None)` expects `candidate` to carry:

| Key | Type | Notes |
|---|---|---|
| `channel` | str | One of `"airbnb"`, `"vrbo"`, `"hotel"` (case-insensitive; normalized internally). Any other value raises `ValueError` — a typo or an unmapped provider label must be surfaced, not silently given the most permissive floor. |
| `total` | float | True total cost, from `rental_total()`/`hotel_total()` |
| `rating` | float or `None` | Provider rating; `None` or absent means unrated, not zero |
| `rating_scale` | int | `5` or `10`; defaults to `5` if omitted |
| `review_count` | int | Defaults to `0` if omitted |
| `sleeps` | int | Max occupancy of the listing |
| `party_size` | int | Total party size for this search |
| `available` | bool | Defaults to `True` if omitted |

## Weighted scoring for survivors

| Factor | Weight | Notes |
|---|---|---|
| True total cost against budget | Heaviest | Use `total`, not nightly rate |
| Kid amenities | High | See below |
| Loyalty | Medium-high | Rank boost plus quantified perk value |
| Location | Medium | Distance to trip anchor, walkability, area at night |
| Review quality | Medium | Rating weighted by review count |
| Cancellation flexibility | Low-medium | Weight up for far-out dates |

## Kid amenities

Match on specifics, not on the word:

- **Heated** pool, not merely "pool" — an unheated outdoor pool in April is
  decorative
- Kiddie or shallow area, and whether the pool has a lifeguard
- Full kitchen — matters enormously with small children
- In-unit laundry
- Crib or pack-n-play availability
- Separate sleeping space, so adults are not sitting in the dark at 7pm

### Seasonal amenity check

A listing's amenity list describes the property year-round. An outdoor pool
in a cold-climate city in October is closed. Verify pool and amenity
availability **against the travel dates** rather than trusting the list.

## Loyalty valuation

Convert status into dollars, then report it separately from price.

- Free breakfast: roughly $15–20 per person per day
- Waived resort fee: the actual fee, per night
- Free parking: the actual rate, per night
- Suite upgrade: the fare difference, discounted by the odds of clearing
- Late checkout / lounge access: real but hard to price — describe rather
  than quantify

Report `total_cost` and `effective_cost_after_perks` as two numbers.
Never blend perk value into the price: silently subtracting it makes the
output impossible to reconcile against a booking page, which destroys
trust in every other number the skill prints.

A $40-pricier stay at a status brand can legitimately win. Show the work.

## Pros and cons

Two to three pros, one to two cons, per option.

**Every option gets at least one genuine con.** If no downside can be
named, the listing has not been examined closely enough — a con-free
recommendation is a research failure, not an endorsement. Mine the reviews
for the specific complaint that recurs.

Where two options are genuinely close, say so rather than manufacturing a
ranking gap.
