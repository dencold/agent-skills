"""Deterministic arithmetic for the lodging-scout skill.

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


DEFAULT_SERVICE_FEE_RATE = 0.14


def _finalize_total(composed, breakdown, provider_total):
    """Shared by rental_total and hotel_total.

    When a provider total is supplied it wins and is authoritative, but the
    composed figure is not discarded — it is attached to the breakdown as
    `composed_estimate` so a caller printing both a total and a breakdown
    never shows two numbers that silently disagree.
    """
    if provider_total is not None:
        breakdown = dict(breakdown, composed_estimate=round(composed, 2))
        return {"total": round(provider_total, 2), "is_estimate": False,
                "breakdown": breakdown}
    return {"total": round(composed, 2), "is_estimate": True,
            "breakdown": breakdown}


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
    if discount > nightly_rate * nights:
        raise ValueError("discount cannot exceed the pre-fee nightly total")

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

    return _finalize_total(composed, breakdown, provider_total)


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

    return _finalize_total(composed, breakdown, provider_total)


def effective_cost(total, perk_value):
    """Cost after loyalty perks, reported alongside total — never instead of it.

    Blending perk value into the price makes output impossible to reconcile
    against a booking page, which destroys trust in every other number.
    """
    return round(max(0.0, total - perk_value), 2)


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
    discards good options. A missing or None rating means unrated, not
    zero — a brand-new listing with no reviews yet is flagged, not
    eliminated, and not allowed to crash the whole gating pass.
    """
    eliminated_by = []
    flags = []

    if not candidate.get("available", True):
        eliminated_by.append("unavailable")

    if candidate["sleeps"] < candidate["party_size"]:
        eliminated_by.append("cannot_sleep_party")

    if budget_ceiling is not None and candidate["total"] > budget_ceiling:
        eliminated_by.append("over_budget")

    channel = candidate["channel"].strip().lower()
    if channel not in CHANNEL_RATING_FLOORS:
        raise ValueError(f"unrecognized channel: {candidate['channel']!r}")

    rating = candidate.get("rating")
    if rating is None:
        flags.append("unrated")
    else:
        floor = CHANNEL_RATING_FLOORS[channel]
        normalized = normalize_rating(rating, candidate.get("rating_scale", 5))
        if normalized < floor:
            eliminated_by.append("below_rating_floor")

    if candidate.get("review_count", 0) < THIN_REVIEW_THRESHOLD:
        flags.append("thin_review_history")

    return {
        "passes": len(eliminated_by) == 0,
        "eliminated_by": eliminated_by,
        "flags": flags,
    }
