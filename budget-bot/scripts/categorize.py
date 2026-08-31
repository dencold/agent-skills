"""Apply the merchant map to a month of transactions.

Three tiers, in descending confidence. Only the first is silent; the other
two are handed to the user in a single review table. A flagged merchant is
never silent regardless of how well it matches, because merchants like
Costco genuinely split by trip and one careless inherited answer would
quietly poison a year of data.
"""

from dataclasses import dataclass

from normalize import normalize_merchant

TIER_EXACT = 1
TIER_FUZZY = 2
TIER_UNKNOWN = 3

# Below this length a key prefix-matches far too much of the map.
MIN_FUZZY_KEY_LENGTH = 4

# Shared leading characters before two keys are considered the same
# merchant. Abbreviations diverge mid-string ("WHOLE FOODS MKT" vs
# "WHOLE FOODS MARKET"), so neither is a prefix of the other and a
# startswith test would miss them.
MIN_SHARED_PREFIX = 8


@dataclass
class Decision:
    row: object
    merchant: str
    proposed: str
    tier: int
    display: str
    needs_review: bool


def _shared_prefix_length(a, b):
    count = 0
    for left, right in zip(a, b):
        if left != right:
            break
        count += 1
    return count


def fuzzy_match(key, entries):
    """Known merchant sharing the longest leading run with this key."""
    if len(key) < MIN_FUZZY_KEY_LENGTH:
        return None

    best = None
    best_shared = 0
    for known in entries:
        if known == key or len(known) < MIN_FUZZY_KEY_LENGTH:
            continue
        shared = _shared_prefix_length(key, known)
        if shared >= MIN_SHARED_PREFIX and shared > best_shared:
            best, best_shared = known, shared
    return best


def categorize(rows, entries):
    """Assign a category to each row, marking what needs human review."""
    decisions = []

    for row in rows:
        key = normalize_merchant(row.transaction)
        entry = entries.get(key)

        if entry is not None:
            decisions.append(Decision(
                row=row, merchant=key, proposed=entry.category, tier=TIER_EXACT,
                display=entry.describe(), needs_review=entry.ambiguous,
            ))
            continue

        matched = fuzzy_match(key, entries)
        if matched is not None:
            entry = entries[matched]
            decisions.append(Decision(
                row=row, merchant=key, proposed=entry.category, tier=TIER_FUZZY,
                display=f"{entry.category} (matched {matched})", needs_review=True,
            ))
            continue

        decisions.append(Decision(
            row=row, merchant=key, proposed="", tier=TIER_UNKNOWN,
            display="unknown merchant", needs_review=True,
        ))

    return decisions
