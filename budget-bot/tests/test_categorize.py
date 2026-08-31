import sys
import pathlib
import unittest
from decimal import Decimal

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from categorize import (
    TIER_EXACT, TIER_FUZZY, TIER_UNKNOWN, categorize, fuzzy_match,
)
from ledger import Row
from merchant_map import MapEntry


def make_row(transaction, amount="6.50"):
    return Row(timestamp="2026-08-04", transaction=transaction,
               amount=Decimal(amount), account="Test Visa")


ENTRIES = {
    "BLUE BOTTLE": MapEntry("BLUE BOTTLE", "Dining", 14, False),
    "COSTCO WHSE": MapEntry("COSTCO WHSE", "Grocery", 31, True, 0, {"Household": 13}),
    "WHOLE FOODS MARKET": MapEntry("WHOLE FOODS MARKET", "Grocery", 9, False),
}


class TestCategorize(unittest.TestCase):
    def test_known_unambiguous_merchant_is_assigned_silently(self):
        decision = categorize([make_row("SQ *BLUE BOTTLE 1123")], dict(ENTRIES))[0]
        self.assertEqual(decision.proposed, "Dining")
        self.assertEqual(decision.tier, TIER_EXACT)
        self.assertFalse(decision.needs_review)

    def test_ambiguous_merchant_always_surfaces_with_the_split(self):
        decision = categorize([make_row("COSTCO WHSE 0455")], dict(ENTRIES))[0]
        self.assertEqual(decision.tier, TIER_EXACT)
        self.assertTrue(decision.needs_review)
        self.assertEqual(decision.proposed, "Grocery")
        self.assertIn("13 of 31 were Household", decision.display)

    def test_fuzzy_match_is_assigned_but_shown(self):
        decision = categorize([make_row("WHOLE FOODS MKT")], dict(ENTRIES))[0]
        self.assertEqual(decision.tier, TIER_FUZZY)
        self.assertTrue(decision.needs_review)

    def test_unknown_merchant_has_no_proposal_for_claude_to_fill(self):
        decision = categorize([make_row("SOME NEW PLACE")], dict(ENTRIES))[0]
        self.assertEqual(decision.tier, TIER_UNKNOWN)
        self.assertEqual(decision.proposed, "")
        self.assertTrue(decision.needs_review)

    def test_descriptor_is_never_replaced_by_the_normalized_key(self):
        decision = categorize([make_row("SQ *BLUE BOTTLE 1123")], dict(ENTRIES))[0]
        self.assertEqual(decision.row.transaction, "SQ *BLUE BOTTLE 1123")
        self.assertEqual(decision.merchant, "BLUE BOTTLE")


class TestFuzzyMatch(unittest.TestCase):
    def test_prefix_of_a_known_merchant_matches(self):
        self.assertEqual(fuzzy_match("WHOLE FOODS", ENTRIES), "WHOLE FOODS MARKET")

    def test_short_keys_do_not_fuzzy_match(self):
        # A two-character key would prefix-match half the map.
        self.assertIsNone(fuzzy_match("BL", ENTRIES))

    def test_unrelated_key_does_not_match(self):
        self.assertIsNone(fuzzy_match("SOME NEW PLACE", ENTRIES))


if __name__ == "__main__":
    unittest.main()
