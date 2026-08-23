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
