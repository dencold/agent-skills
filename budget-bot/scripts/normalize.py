"""Descriptor to merchant lookup key for the budget-bot skill.

Bank descriptors carry per-transaction noise -- store numbers, order ids,
payment-processor prefixes -- that makes every purchase look novel. This
module collapses that noise so a merchant seen twenty times is one key.

The result NEVER reaches the output CSV. It is an in-memory lookup key
for merchant-map.csv and dedupe hashes only; the ledger keeps the bank's
exact descriptor so the sheet reconciles against statements.

Both bootstrap.py and categorize.py import this. If they ever normalized
differently, every map key would miss -- which is why there is one
implementation and it is tested harder than anything else here.
"""

import re

PROCESSOR_PREFIXES = ("SQ *", "TST*", "PY *", "SP ", "PAYPAL *", "IN *")

_SPLIT = re.compile(r"[\s*]+")
_HAS_DIGIT = re.compile(r"\d")


def _is_noise(token):
    """Trailing tokens that identify a transaction rather than a merchant.

    Store numbers and order ids contain digits. Two-character trailing
    tokens are state or country codes ("AMZN MKTP US"). Both vary between
    visits to the same merchant.
    """
    return bool(_HAS_DIGIT.search(token)) or len(token) <= 2


def normalize_merchant(descriptor):
    """Collapse a bank descriptor to a stable merchant key."""
    if not descriptor or not descriptor.strip():
        raise ValueError("descriptor is required")

    key = descriptor.strip().upper()

    for prefix in PROCESSOR_PREFIXES:
        if key.startswith(prefix):
            key = key[len(prefix):].strip()
            break

    tokens = [t for t in _SPLIT.split(key) if t]
    trimmed = list(tokens)
    while len(trimmed) > 1 and _is_noise(trimmed[-1]):
        trimmed.pop()

    # Every token was noise ("7-ELEVEN", "76"). Stripping to empty would
    # collapse unrelated merchants into a single key.
    if not trimmed or all(_is_noise(t) for t in trimmed):
        return " ".join(tokens)

    return " ".join(trimmed)
