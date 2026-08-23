# Lodging Search Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Claude skill that searches hotels, Airbnb, and VRBO for a given party, dates, and location, then returns the top 10 ranked stays with true total-stay cost and honest pros/cons.

**Architecture:** A prose skill (`SKILL.md` + `references/`) carries the workflow and judgment. A small stdlib-only Python module (`scripts/lodging_calc.py`) carries the deterministic arithmetic — occupancy math, fee normalization, rating normalization, and hard gates — because those must reconcile exactly against real booking pages. Data comes from swappable MCP providers discovered at runtime through a capability contract.

**Tech Stack:** Markdown skill files; Python 3 standard library only (`unittest`, no third-party packages, no pip install); MCP servers for data (`@openbnb/mcp-server-airbnb`, `@mvanhorn/printing-press-library`, `hotel-goat`/`hotels-skill`).

**Spec:** `docs/superpowers/specs/2026-08-23-lodging-search-design.md`

## Departure From The Spec

The spec's Verification section states "prose, not code." This plan adds
`scripts/lodging_calc.py`. The reasoning: spec §4 defines exact arithmetic,
and the skill's whole value claim is that its totals reconcile against
booking pages. Freehand LLM arithmetic over a six-term fee stack × rooms ×
nights × 10 candidates will drift. The module is stdlib-only, so it adds no
install friction.

If this is unwanted, drop Tasks 1–3 and fold their formulas into
`references/cost-model.md` as prose the skill follows by hand. Every later
task still works; the totals just become less reliable.

## Global Constraints

Copied verbatim from the spec. Every task's requirements implicitly include this section.

- **Python is stdlib-only.** No pip installs, no third-party imports. Tests use `unittest`.
- **The real profile lives at `~/.claude/lodging-profile.md`, outside this repository.** The repo is public. Never commit a populated profile, loyalty account standing, or children's ages.
- **Trip files live at `~/.claude/lodging-trips/`, outside the repo.** Filenames use the search date, not check-in date.
- **Noisy failure.** A missing or broken capability is stated plainly with its install command. Never return a quiet half-search presented as complete.
- **Never blend perk value into price.** Report `total_cost` and `effective_cost_after_perks` as two separate numbers.
- **Prefer the provider's total.** Use a provider's all-in total when present and label it authoritative. Compose from parts only when absent, and mark composed figures as estimates with `~`.
- **Ratings normalize to a 5-point scale** before any threshold. Floors are per-channel, not global.
- **Thin review counts are a flag, not a gate.**
- **Top 10 results by default**, configurable by profile default and per-request override.
- **Pagination never re-searches.** It serves from the retained pool.
- **Every presented option carries at least one genuine con.**
- All money rounds to cents with `round(x, 2)` at the final step only.

---

### Task 1: Scaffolding and occupancy math

The occupancy calculation runs before any search because it reshapes the
entire comparison — a party of 5 cannot use one standard hotel room, and
once hotels are priced at two rooms, rentals often win outright.

**Files:**
- Create: `lodging-search/scripts/lodging_calc.py`
- Create: `lodging-search/tests/test_lodging_calc.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: nothing
- Produces: `rooms_needed(adults: int, kids_ages: list, max_occupancy: int = 4, infant_age_cutoff: int = 2) -> dict` returning keys `rooms` (int), `counted_occupants` (int), `uncounted_infants` (int), `verify_infant_policy` (bool), `forces_multi_room` (bool)

- [ ] **Step 1: Create directories and gitignore entry**

```bash
mkdir -p lodging-search/scripts lodging-search/tests
```

- [ ] **Step 2: Write the failing test**

Create `lodging-search/tests/test_lodging_calc.py`:

```python
import sys
import pathlib
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from lodging_calc import rooms_needed


class TestRoomsNeeded(unittest.TestCase):
    def test_family_of_four_fits_one_room(self):
        result = rooms_needed(adults=2, kids_ages=[6, 9])
        self.assertEqual(result["rooms"], 1)
        self.assertEqual(result["counted_occupants"], 4)
        self.assertFalse(result["forces_multi_room"])

    def test_family_of_five_needs_two_rooms(self):
        result = rooms_needed(adults=2, kids_ages=[4, 6, 9])
        self.assertEqual(result["rooms"], 2)
        self.assertEqual(result["counted_occupants"], 5)
        self.assertTrue(result["forces_multi_room"])

    def test_infant_not_counted_but_flagged_for_verification(self):
        result = rooms_needed(adults=2, kids_ages=[1, 6, 9])
        self.assertEqual(result["counted_occupants"], 4)
        self.assertEqual(result["uncounted_infants"], 1)
        self.assertEqual(result["rooms"], 1)
        self.assertTrue(result["verify_infant_policy"])

    def test_no_infants_means_no_verification_flag(self):
        result = rooms_needed(adults=2, kids_ages=[6, 9])
        self.assertEqual(result["uncounted_infants"], 0)
        self.assertFalse(result["verify_infant_policy"])

    def test_three_person_occupancy_cap_forces_second_room(self):
        result = rooms_needed(adults=2, kids_ages=[6, 9], max_occupancy=3)
        self.assertEqual(result["rooms"], 2)

    def test_large_group_rounds_up(self):
        result = rooms_needed(adults=4, kids_ages=[5, 7, 9, 11, 13])
        self.assertEqual(result["counted_occupants"], 9)
        self.assertEqual(result["rooms"], 3)

    def test_rejects_zero_adults(self):
        with self.assertRaises(ValueError):
            rooms_needed(adults=0, kids_ages=[6])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python3 -m unittest discover -s lodging-search/tests -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'lodging_calc'`

- [ ] **Step 4: Write minimal implementation**

Create `lodging-search/scripts/lodging_calc.py`:

```python
"""Deterministic arithmetic for the lodging-search skill.

Occupancy, fee normalization, rating normalization, and hard gates live
here rather than being done freehand, because the skill's totals must
reconcile against real booking pages.
"""

import math

MAX_OCCUPANCY_DEFAULT = 4
INFANT_AGE_CUTOFF_DEFAULT = 2


def rooms_needed(adults, kids_ages, max_occupancy=MAX_OCCUPANCY_DEFAULT,
                 infant_age_cutoff=INFANT_AGE_CUTOFF_DEFAULT):
    """How many hotel rooms this party requires.

    Children under the infant cutoff typically do not count toward room
    occupancy, but the rule varies by brand and country. They are excluded
    from the count and the caller is told to verify, never silently assumed.
    """
    if adults < 1:
        raise ValueError("a party needs at least one adult")
    if max_occupancy < 1:
        raise ValueError("max_occupancy must be at least 1")

    infants = [age for age in kids_ages if age < infant_age_cutoff]
    counted = adults + len(kids_ages) - len(infants)
    rooms = math.ceil(counted / max_occupancy)

    return {
        "rooms": rooms,
        "counted_occupants": counted,
        "uncounted_infants": len(infants),
        "verify_infant_policy": len(infants) > 0,
        "forces_multi_room": rooms > 1,
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m unittest discover -s lodging-search/tests -v`
Expected: PASS, 7 tests

- [ ] **Step 6: Commit**

```bash
git add .gitignore lodging-search/
git commit -m "Add occupancy calculation for lodging search"
```

---

### Task 2: True total cost normalization

Nightly rates are not comparable across channels. This task exists to catch
one specific inversion: a $180/night rental with a $200 cleaning fee loses
to a $210/night hotel over three nights. That reversal is invisible until
fees are normalized, and it is the most common way families overpay.

**Files:**
- Modify: `lodging-search/scripts/lodging_calc.py`
- Modify: `lodging-search/tests/test_lodging_calc.py`

**Interfaces:**
- Consumes: nothing from Task 1
- Produces:
  - `rental_total(nightly_rate, nights, cleaning_fee=0.0, service_fee_rate=0.14, tax_rate=0.0, pet_fee=0.0, discount=0.0, provider_total=None) -> dict`
  - `hotel_total(nightly_rate, nights, rooms=1, resort_fee_per_night=0.0, parking_per_night=0.0, tax_rate=0.0, provider_total=None) -> dict`
  - `effective_cost(total, perk_value) -> float`
  - All three return dicts with keys `total` (float), `is_estimate` (bool), `breakdown` (dict)

**Tax assumptions**, documented because jurisdictions vary: for rentals,
tax applies to the nightly subtotal plus cleaning plus service fee; pet
fees are untaxed. For hotels, tax applies to the room subtotal plus resort
fees; parking is charged per stay rather than per room, on the assumption
of one family vehicle.

- [ ] **Step 1: Write the failing tests**

Append to `lodging-search/tests/test_lodging_calc.py`, above the
`if __name__` block, and add the imports to the existing import line so it
reads `from lodging_calc import rooms_needed, rental_total, hotel_total, effective_cost`:

```python
class TestRentalTotal(unittest.TestCase):
    def test_composes_from_parts_and_marks_estimate(self):
        result = rental_total(nightly_rate=180.0, nights=3, cleaning_fee=200.0,
                              service_fee_rate=0.14, tax_rate=0.10)
        # subtotal 540, service 75.60, taxable 815.60, tax 81.56
        self.assertAlmostEqual(result["total"], 897.16, places=2)
        self.assertTrue(result["is_estimate"])
        self.assertAlmostEqual(result["breakdown"]["service_fee"], 75.60, places=2)

    def test_provider_total_wins_and_is_authoritative(self):
        result = rental_total(nightly_rate=180.0, nights=3, cleaning_fee=200.0,
                              tax_rate=0.10, provider_total=850.00)
        self.assertAlmostEqual(result["total"], 850.00, places=2)
        self.assertFalse(result["is_estimate"])

    def test_discount_reduces_subtotal_before_fees(self):
        result = rental_total(nightly_rate=100.0, nights=7, discount=70.0,
                              service_fee_rate=0.10, tax_rate=0.0)
        # subtotal 630, service 63, total 693
        self.assertAlmostEqual(result["total"], 693.00, places=2)

    def test_pet_fee_added_untaxed(self):
        no_pet = rental_total(nightly_rate=100.0, nights=2, tax_rate=0.20,
                              service_fee_rate=0.0)
        with_pet = rental_total(nightly_rate=100.0, nights=2, tax_rate=0.20,
                                service_fee_rate=0.0, pet_fee=50.0)
        self.assertAlmostEqual(with_pet["total"] - no_pet["total"], 50.00, places=2)

    def test_rejects_zero_nights(self):
        with self.assertRaises(ValueError):
            rental_total(nightly_rate=100.0, nights=0)


class TestHotelTotal(unittest.TestCase):
    def test_multiplies_by_rooms(self):
        one = hotel_total(nightly_rate=210.0, nights=3, rooms=1)
        two = hotel_total(nightly_rate=210.0, nights=3, rooms=2)
        self.assertAlmostEqual(two["total"], one["total"] * 2, places=2)

    def test_resort_fee_is_per_room_per_night_and_taxed(self):
        result = hotel_total(nightly_rate=200.0, nights=2, rooms=2,
                             resort_fee_per_night=25.0, tax_rate=0.15)
        # rooms subtotal 800, resort 100, taxable 900, tax 135
        self.assertAlmostEqual(result["total"], 1035.00, places=2)
        self.assertAlmostEqual(result["breakdown"]["resort_fees"], 100.00, places=2)

    def test_parking_is_per_stay_not_per_room(self):
        result = hotel_total(nightly_rate=200.0, nights=2, rooms=2,
                             parking_per_night=30.0, tax_rate=0.0)
        self.assertAlmostEqual(result["breakdown"]["parking"], 60.00, places=2)
        self.assertAlmostEqual(result["total"], 860.00, places=2)

    def test_provider_total_wins_and_is_authoritative(self):
        result = hotel_total(nightly_rate=210.0, nights=3, rooms=2,
                             tax_rate=0.15, provider_total=1400.00)
        self.assertAlmostEqual(result["total"], 1400.00, places=2)
        self.assertFalse(result["is_estimate"])


class TestCleaningFeeInversion(unittest.TestCase):
    def test_cheaper_nightly_rental_loses_on_short_stay(self):
        rental = rental_total(nightly_rate=180.0, nights=3, cleaning_fee=200.0,
                              service_fee_rate=0.14, tax_rate=0.10)
        hotel = hotel_total(nightly_rate=210.0, nights=3, rooms=1, tax_rate=0.10)
        self.assertGreater(rental["total"], hotel["total"])


class TestEffectiveCost(unittest.TestCase):
    def test_perk_value_subtracts_without_touching_total(self):
        total = 1000.00
        self.assertAlmostEqual(effective_cost(total, 180.0), 820.00, places=2)
        self.assertAlmostEqual(total, 1000.00, places=2)

    def test_perk_value_cannot_push_below_zero(self):
        self.assertAlmostEqual(effective_cost(100.0, 500.0), 0.00, places=2)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest discover -s lodging-search/tests -v`
Expected: FAIL with `ImportError: cannot import name 'rental_total'`

- [ ] **Step 3: Write minimal implementation**

Append to `lodging-search/scripts/lodging_calc.py`:

```python
DEFAULT_SERVICE_FEE_RATE = 0.14


def rental_total(nightly_rate, nights, cleaning_fee=0.0,
                 service_fee_rate=DEFAULT_SERVICE_FEE_RATE, tax_rate=0.0,
                 pet_fee=0.0, discount=0.0, provider_total=None):
    """All-in cost of a rental stay.

    Tax applies to the nightly subtotal plus cleaning and service fees.
    Pet fees are treated as untaxed. Jurisdictions vary; these are the
    documented assumptions, and provider_total always wins when present.
    """
    if nights < 1:
        raise ValueError("nights must be at least 1")

    subtotal = nightly_rate * nights - discount
    service_fee = subtotal * service_fee_rate
    taxable = subtotal + cleaning_fee + service_fee
    taxes = taxable * tax_rate
    composed = taxable + taxes + pet_fee

    breakdown = {
        "nightly_subtotal": round(subtotal, 2),
        "cleaning_fee": round(cleaning_fee, 2),
        "service_fee": round(service_fee, 2),
        "taxes": round(taxes, 2),
        "pet_fee": round(pet_fee, 2),
        "discount": round(discount, 2),
    }

    if provider_total is not None:
        return {"total": round(provider_total, 2), "is_estimate": False,
                "breakdown": breakdown}
    return {"total": round(composed, 2), "is_estimate": True,
            "breakdown": breakdown}


def hotel_total(nightly_rate, nights, rooms=1, resort_fee_per_night=0.0,
                parking_per_night=0.0, tax_rate=0.0, provider_total=None):
    """All-in cost of a hotel stay across the rooms the party requires.

    Resort fees are charged per room per night and are taxed. Parking is
    charged per stay rather than per room, assuming one family vehicle.
    """
    if nights < 1:
        raise ValueError("nights must be at least 1")
    if rooms < 1:
        raise ValueError("rooms must be at least 1")

    room_subtotal = nightly_rate * nights * rooms
    resort_fees = resort_fee_per_night * nights * rooms
    taxable = room_subtotal + resort_fees
    taxes = taxable * tax_rate
    parking = parking_per_night * nights
    composed = taxable + taxes + parking

    breakdown = {
        "room_subtotal": round(room_subtotal, 2),
        "resort_fees": round(resort_fees, 2),
        "taxes": round(taxes, 2),
        "parking": round(parking, 2),
        "rooms": rooms,
    }

    if provider_total is not None:
        return {"total": round(provider_total, 2), "is_estimate": False,
                "breakdown": breakdown}
    return {"total": round(composed, 2), "is_estimate": True,
            "breakdown": breakdown}


def effective_cost(total, perk_value):
    """Cost after loyalty perks, reported alongside total — never instead of it.

    Blending perk value into the price makes output impossible to reconcile
    against a booking page, which destroys trust in every other number.
    """
    return round(max(0.0, total - perk_value), 2)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest discover -s lodging-search/tests -v`
Expected: PASS, 19 tests

- [ ] **Step 5: Commit**

```bash
git add lodging-search/
git commit -m "Add true total cost normalization for rentals and hotels"
```

---

### Task 3: Rating normalization and hard gates

Ratings are not comparable as returned: Airbnb reports out of 5,
Booking.com out of 10. A global threshold applied to raw values would
silently eliminate every Booking.com result or none of them.

**Files:**
- Modify: `lodging-search/scripts/lodging_calc.py`
- Modify: `lodging-search/tests/test_lodging_calc.py`

**Interfaces:**
- Consumes: nothing from Tasks 1–2
- Produces:
  - `normalize_rating(rating, scale) -> float` — returns a 0–5 value
  - `CHANNEL_RATING_FLOORS` — dict mapping `"airbnb"`, `"vrbo"`, `"hotel"` to floats
  - `THIN_REVIEW_THRESHOLD` — int
  - `apply_gates(candidate: dict, budget_ceiling=None) -> dict` returning keys `passes` (bool), `eliminated_by` (list of str), `flags` (list of str)
  - Candidate dict shape: `{"name": str, "channel": str, "total": float, "rating": float, "rating_scale": int, "review_count": int, "sleeps": int, "party_size": int, "available": bool}`

- [ ] **Step 1: Write the failing tests**

Append to `lodging-search/tests/test_lodging_calc.py` and extend the import
line to include `normalize_rating, apply_gates, CHANNEL_RATING_FLOORS, THIN_REVIEW_THRESHOLD`:

```python
class TestNormalizeRating(unittest.TestCase):
    def test_five_point_scale_passes_through(self):
        self.assertAlmostEqual(normalize_rating(4.8, 5), 4.8, places=2)

    def test_ten_point_scale_halves(self):
        self.assertAlmostEqual(normalize_rating(9.0, 10), 4.5, places=2)
        self.assertAlmostEqual(normalize_rating(8.0, 10), 4.0, places=2)

    def test_rejects_unsupported_scale(self):
        with self.assertRaises(ValueError):
            normalize_rating(90, 100)

    def test_rejects_out_of_range_rating(self):
        with self.assertRaises(ValueError):
            normalize_rating(6.0, 5)


def _candidate(**overrides):
    base = {
        "name": "Test Property",
        "channel": "airbnb",
        "total": 1000.00,
        "rating": 4.8,
        "rating_scale": 5,
        "review_count": 120,
        "sleeps": 6,
        "party_size": 5,
        "available": True,
    }
    base.update(overrides)
    return base


class TestApplyGates(unittest.TestCase):
    def test_good_candidate_passes_clean(self):
        result = apply_gates(_candidate(), budget_ceiling=1500.00)
        self.assertTrue(result["passes"])
        self.assertEqual(result["eliminated_by"], [])
        self.assertEqual(result["flags"], [])

    def test_cannot_sleep_party_is_eliminated(self):
        result = apply_gates(_candidate(sleeps=4, party_size=5))
        self.assertFalse(result["passes"])
        self.assertIn("cannot_sleep_party", result["eliminated_by"])

    def test_over_budget_is_eliminated(self):
        result = apply_gates(_candidate(total=2000.00), budget_ceiling=1500.00)
        self.assertFalse(result["passes"])
        self.assertIn("over_budget", result["eliminated_by"])

    def test_no_budget_ceiling_means_no_budget_gate(self):
        result = apply_gates(_candidate(total=99999.00), budget_ceiling=None)
        self.assertTrue(result["passes"])

    def test_unavailable_is_eliminated(self):
        result = apply_gates(_candidate(available=False))
        self.assertFalse(result["passes"])
        self.assertIn("unavailable", result["eliminated_by"])

    def test_low_rating_eliminated_per_channel_floor(self):
        result = apply_gates(_candidate(channel="airbnb", rating=4.1))
        self.assertFalse(result["passes"])
        self.assertIn("below_rating_floor", result["eliminated_by"])

    def test_same_normalized_rating_survives_on_hotel_channel(self):
        # 4.1 of 5 clears the hotel floor but not the stricter rental floor
        result = apply_gates(_candidate(channel="hotel", rating=8.2,
                                        rating_scale=10))
        self.assertTrue(result["passes"])

    def test_thin_reviews_flag_but_do_not_eliminate(self):
        result = apply_gates(_candidate(review_count=6))
        self.assertTrue(result["passes"])
        self.assertIn("thin_review_history", result["flags"])

    def test_estimate_and_multiple_eliminations_accumulate(self):
        result = apply_gates(_candidate(sleeps=2, party_size=5, available=False))
        self.assertFalse(result["passes"])
        self.assertEqual(len(result["eliminated_by"]), 2)


class TestConstantsMatchRankingDoc(unittest.TestCase):
    """references/ranking.md documents these same values in prose.

    Pin them here so the code and the doc cannot drift apart silently.
    """

    def test_rental_floors_are_stricter_than_hotel_floor(self):
        self.assertEqual(CHANNEL_RATING_FLOORS["airbnb"], 4.5)
        self.assertEqual(CHANNEL_RATING_FLOORS["vrbo"], 4.5)
        self.assertEqual(CHANNEL_RATING_FLOORS["hotel"], 4.0)
        self.assertGreater(CHANNEL_RATING_FLOORS["airbnb"],
                           CHANNEL_RATING_FLOORS["hotel"])

    def test_thin_review_threshold_matches_doc(self):
        self.assertEqual(THIN_REVIEW_THRESHOLD, 10)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest discover -s lodging-search/tests -v`
Expected: FAIL with `ImportError: cannot import name 'normalize_rating'`

- [ ] **Step 3: Write minimal implementation**

Append to `lodging-search/scripts/lodging_calc.py`:

```python
# A 4.0 hotel is unremarkable; a 4.0 Airbnb is a warning. Floors differ.
CHANNEL_RATING_FLOORS = {
    "airbnb": 4.5,
    "vrbo": 4.5,
    "hotel": 4.0,
}

THIN_REVIEW_THRESHOLD = 10


def normalize_rating(rating, scale):
    """Convert a provider rating to a 0-5 scale before any threshold."""
    if scale not in (5, 10):
        raise ValueError(f"unsupported rating scale: {scale}")
    if not 0 <= rating <= scale:
        raise ValueError(f"rating {rating} out of range for scale {scale}")
    return round(rating * (5.0 / scale), 2)


def apply_gates(candidate, budget_ceiling=None):
    """Eliminate disqualified stays; flag the merely uncertain ones.

    Thin review counts are a flag rather than a gate: a listing with six
    reviews may be excellent and newly listed, and eliminating it silently
    discards good options.
    """
    eliminated_by = []
    flags = []

    if not candidate.get("available", True):
        eliminated_by.append("unavailable")

    if candidate["sleeps"] < candidate["party_size"]:
        eliminated_by.append("cannot_sleep_party")

    if budget_ceiling is not None and candidate["total"] > budget_ceiling:
        eliminated_by.append("over_budget")

    floor = CHANNEL_RATING_FLOORS.get(candidate["channel"], 4.0)
    normalized = normalize_rating(candidate["rating"],
                                  candidate.get("rating_scale", 5))
    if normalized < floor:
        eliminated_by.append("below_rating_floor")

    if candidate.get("review_count", 0) < THIN_REVIEW_THRESHOLD:
        flags.append("thin_review_history")

    return {
        "passes": len(eliminated_by) == 0,
        "eliminated_by": eliminated_by,
        "flags": flags,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest discover -s lodging-search/tests -v`
Expected: PASS, 34 tests

- [ ] **Step 5: Commit**

```bash
git add lodging-search/
git commit -m "Add rating normalization and hard gates"
```

---

### Task 4: Provider setup reference

Every provider here is a scraper subject to blocking and DOM changes. This
file is what makes swapping a dead provider a config edit rather than a
skill rewrite.

**Files:**
- Create: `lodging-search/references/mcp-setup.md`

**Interfaces:**
- Consumes: nothing
- Produces: capability names `airbnb_search`, `vrbo_search`, `hotel_search`, referenced by `SKILL.md` in Task 6

- [ ] **Step 1: Write the reference file**

Create `lodging-search/references/mcp-setup.md`:

````markdown
# Provider Setup

The skill needs three capabilities. Each is satisfied by whichever MCP
server is installed — the skill probes at runtime and does not depend on
any specific one.

| Capability | Purpose |
|---|---|
| `airbnb_search` | Airbnb listings and details |
| `vrbo_search` | VRBO listings and details |
| `hotel_search` | Hotel rates |

## Known providers, as of 2026-08

### Airbnb — `@openbnb/mcp-server-airbnb`

No API key. Exposes `airbnb_search` and `airbnb_listing_details`.

```json
{
  "mcpServers": {
    "airbnb": {
      "command": "npx",
      "args": ["-y", "@openbnb/mcp-server-airbnb"]
    }
  }
}
```

### Airbnb + VRBO — `@mvanhorn/printing-press-library`

Covers both platforms and surfaces host direct-booking sites, which
sometimes beat the OTA price.

```bash
npx -y @mvanhorn/printing-press-library install airbnb
```

Public search needs no authentication. Its web-search backend is
configurable; DuckDuckGo is the free option.

### Hotels — pick one

- `hotel-goat` — Google Hotels plus Trivago, OTA-aggregated rates, no API key
- `hotels-skill` — Booking.com via Playwright, no API key
- HotelZero — Booking.com via Playwright, 80+ filters

## Preference order

When both Airbnb providers are installed, use openbnb for Airbnb (richer
listing detail) and printing-press for VRBO.

## Deduplication

The same house is frequently listed on both Airbnb and VRBO at different
prices. Deduplicate by property address and name. When a duplicate is found
and the prices differ, **report the gap** — it is a finding, not noise, and
it tells the user where to book.

## Health check

Before trusting a provider, run a throwaway search against a busy market on
near dates. Distinguish two failures and report them differently:

- **Blocked or broken** — errored, timed out, or returned malformed data.
  This is a tooling failure. Say so and give the install command.
- **Genuinely empty** — responded correctly with no availability. This is a
  trip-planning fact. Suggest loosening dates or radius.

Never let the second explanation cover for the first.
````

- [ ] **Step 2: Verify all three capabilities are named in the file**

```bash
for cap in airbnb_search vrbo_search hotel_search; do
  grep -q "$cap" lodging-search/references/mcp-setup.md \
    || { echo "MISSING: $cap"; exit 1; }
done
echo "all three capabilities documented"
```
Expected: `all three capabilities documented`

- [ ] **Step 3: Commit**

```bash
git add lodging-search/references/mcp-setup.md
git commit -m "Add provider setup reference"
```

---

### Task 5: Profile template and ranking rubric

**Files:**
- Create: `lodging-search/references/profile.template.md`
- Create: `lodging-search/references/ranking.md`

**Interfaces:**
- Consumes: `CHANNEL_RATING_FLOORS`, `THIN_REVIEW_THRESHOLD` from Task 3
- Produces: the profile field names `Results per page`, `Household`, `Hotel loyalty`, `Brand preferences`, `Amenity priorities`, `Defaults`, read by `SKILL.md` in Task 6

- [ ] **Step 1: Write the profile template**

Create `lodging-search/references/profile.template.md`:

```markdown
<!--
Copy this to ~/.claude/lodging-profile.md and fill it in.

Keep it OUTSIDE this repository. The repo is public; loyalty account
standing and children's ages do not belong in it.
-->

## Household
- Adults: 2
- Kids: ages 6, 9   <!-- update annually -->
- Notes: early bedtimes; need room-darkening or a separate sleeping space

## Hotel loyalty
<!-- The perks matter more than the tier name: the skill converts status
     into dollars, so list what the tier actually delivers. -->
- Marriott Bonvoy — Titanium — lounge access, suite upgrades, late checkout, free breakfast for 2
- Hilton Honors — Gold — free breakfast, room upgrade

## Brand preferences
- Prefer:
- Avoid:            <!-- and why, so the skill can weigh exceptions -->

## Amenity priorities
- Must have:        <!-- hard requirements; these become gates -->
- Strongly prefer: heated pool, in-unit laundry, full kitchen
- Nice to have:

## Defaults
- Typical nightly budget range:
- Driving or flying:   <!-- driving turns on the parking calculation -->
- Cancellation flexibility tolerance:
- Results per page: 10
```

- [ ] **Step 2: Write the ranking rubric**

Create `lodging-search/references/ranking.md`:

```markdown
# Ranking

## Hard gates — eliminate, never down-rank

Computed by `scripts/lodging_calc.py::apply_gates`:

- Cannot sleep the entire party
- Exceeds the stated budget ceiling
- Unavailable for the requested dates
- Rating below the per-channel floor, after normalizing to a 5-point scale
- Any profile "Must have" amenity is absent

Rating floors: Airbnb and VRBO 4.5, hotels 4.0. A 4.0 hotel is
unremarkable; a 4.0 Airbnb is a warning.

Thin review history (under 10 reviews) is a **flag, not a gate**. Rank it
slightly lower and name it as one of the property's cons.

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
```

- [ ] **Step 3: Verify both files exist and the template carries its warning**

```bash
test -f lodging-search/references/profile.template.md && \
  grep -q "OUTSIDE this repository" lodging-search/references/profile.template.md && \
  test -f lodging-search/references/ranking.md && echo OK
```
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add lodging-search/references/
git commit -m "Add profile template and ranking rubric"
```

---

### Task 6: SKILL.md — workflow, output, and pagination

The skill file itself. It ties the calculator and references together into
the six-step workflow plus pagination.

**Files:**
- Create: `lodging-search/SKILL.md`

**Interfaces:**
- Consumes: `rooms_needed`, `rental_total`, `hotel_total`, `effective_cost`, `apply_gates` from Tasks 1–3; capability names from Task 4; profile field names and rubric from Task 5
- Produces: the trip file format consumed by pagination

- [ ] **Step 1: Write SKILL.md**

Create `lodging-search/SKILL.md`:

````markdown
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

Needed: adults, kids **with ages**, check-in and check-out, location,
budget ceiling for the stay, and the trip anchor (the thing the trip is
organized around — a beach, a venue, grandma's house).

Convert relative dates ("spring break") to absolute ones. Confirm the
resolved parameters back in one line before searching; a wrong date range
wastes the whole search.

## 2. Occupancy check — before any search

```bash
python3 -c "
import sys; sys.path.insert(0, 'lodging-search/scripts')
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

## 4. Normalize to true total cost

Never compare nightly rates. Use `scripts/lodging_calc.py`:
`rental_total()` and `hotel_total()`.

Pass `provider_total` whenever the provider returned an all-in figure — it
wins and is authoritative. Only compose from parts when it is absent, and
mark composed figures with `~` in output, since `is_estimate` will be true.

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
   every run, where `YYYY-MM-DD` is the search date. Holds the **complete
   ranked pool**, not just the shown page, plus resolved parameters and
   the price-check timestamp. This is what makes pagination work in a
   later session.
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
````

- [ ] **Step 2: Verify frontmatter parses and required sections exist**

```bash
python3 - <<'PY'
import pathlib, re
p = pathlib.Path("lodging-search/SKILL.md")
text = p.read_text()
assert text.startswith("---\n"), "missing frontmatter opening"
fm = text.split("---", 2)[1]
assert re.search(r"^name: lodging-search$", fm, re.M), "bad name"
assert re.search(r"^description: Use when", fm, re.M), "description must start with 'Use when'"
for section in ["## Setup", "## 1. Resolve inputs", "## 2. Occupancy check",
                "## 3. Search", "## 4. Normalize", "## 5. Score",
                "## 6. Present", "## 7. Paginate", "## Output", "## Never"]:
    assert section in text, f"missing {section}"
print("SKILL.md OK")
PY
```
Expected: `SKILL.md OK`

- [ ] **Step 3: Run the full test suite to confirm nothing regressed**

Run: `python3 -m unittest discover -s lodging-search/tests -v`
Expected: PASS, 34 tests

- [ ] **Step 4: Commit**

```bash
git add lodging-search/SKILL.md
git commit -m "Add lodging-search skill workflow"
```

---

### Task 7: End-to-end scenario verification

The calculator is unit-tested; the judgment is not. These scenarios test
the parts no unit test reaches, by running the skill for real and
reconciling against actual booking pages.

**Files:**
- Create: `lodging-search/references/verification.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: everything from Tasks 1–6
- Produces: nothing consumed downstream

- [ ] **Step 1: Write the verification checklist**

Create `lodging-search/references/verification.md`:

```markdown
# Verification

Run these by hand after any change to the workflow, rubric, or providers.
Each ends by opening the real booking pages and confirming the skill's
totals reconcile.

## 1. Party of five

"2 adults and 3 kids, ages 4, 6, and 9, San Diego, first week of October,
under $2000."

- [ ] Occupancy check fires **before** the search
- [ ] The two-room-versus-rental tradeoff is stated explicitly, not buried
- [ ] Hotels are priced at 2 rooms
- [ ] Suites or connecting rooms appear as a distinct option

## 2. Loyalty-heavy downtown

A city dense with your status brands, 2 adults, 3 nights.

- [ ] Status brands are boosted, and the boost is visible in the reasoning
- [ ] Perk value is quantified in dollars
- [ ] `total_cost` and `effective_cost_after_perks` both appear, unblended
- [ ] Both reconcile against the booking page

## 3. Beach town, short stay

Somewhere rentals dominate, 2 nights.

- [ ] The cleaning-fee inversion is caught
- [ ] A hotel can beat a lower-nightly-rate rental, and the totals show why
- [ ] Cross-listed Airbnb/VRBO duplicates are merged and the price gap reported

## 4. Pagination, same session

After any search, ask for the next 10.

- [ ] Serves ranks 11–20 with no re-search
- [ ] Repeats nothing from page 1
- [ ] States its position in the pool

## 5. Pagination, later session

Fresh session, ask for more results on a saved trip.

- [ ] Reads the saved pool rather than re-searching
- [ ] Exhausting the pool offers concrete widenings, not a silent stop

## 6. Degraded path

Disable one provider.

- [ ] Failure is loud and names the missing capability
- [ ] Install command is shown
- [ ] Blocked-versus-genuinely-empty are reported differently
- [ ] The user is asked whether to proceed single-channel
```

- [ ] **Step 2: Document the skill in the README**

```bash
cat >> README.md <<'EOF'

## Skills

### lodging-search

Searches hotels, Airbnb, and VRBO for a party, dates, and location, then
returns ranked stays with true total-stay cost and honest pros and cons.
Accounts for hotel loyalty status, brand preferences, and family amenities.

Setup: copy `lodging-search/references/profile.template.md` to
`~/.claude/lodging-profile.md` and fill it in, then install the MCP
providers listed in `lodging-search/references/mcp-setup.md`.
EOF
```

- [ ] **Step 3: Confirm the profile is not committed anywhere**

```bash
git ls-files | grep -i "lodging-profile" && echo "FAIL: profile is tracked" || echo "OK: profile not tracked"
```
Expected: `OK: profile not tracked`

- [ ] **Step 4: Run the full suite one final time**

Run: `python3 -m unittest discover -s lodging-search/tests -v`
Expected: PASS, 34 tests

- [ ] **Step 5: Commit**

```bash
git add README.md lodging-search/references/verification.md
git commit -m "Add verification checklist and document skill in README"
```

---

## Post-Implementation

Tasks 1–7 produce a complete, installed skill. The scenarios in
`references/verification.md` require live MCP providers and real booking
pages, so they are run by the user, not by an implementing agent. Report
the skill as built-and-unit-tested, with end-to-end verification pending —
not as verified.
