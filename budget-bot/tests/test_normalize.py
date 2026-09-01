import sys
import pathlib
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from normalize import normalize_merchant


class TestNormalizeMerchant(unittest.TestCase):
    def test_strips_square_prefix_and_trailing_store_id(self):
        self.assertEqual(normalize_merchant("SQ *BLUE BOTTLE 1123"), "BLUE BOTTLE")

    def test_strips_toast_prefix(self):
        self.assertEqual(normalize_merchant("TST* CHIPOTLE 0455"), "CHIPOTLE")

    def test_strips_amazon_order_id_and_state_token(self):
        self.assertEqual(normalize_merchant("AMZN Mktp US*2H4KL9DJ3"), "AMZN MKTP")

    def test_uppercases_and_collapses_whitespace(self):
        self.assertEqual(normalize_merchant("  Blue   Bottle  "), "BLUE BOTTLE")

    def test_is_idempotent(self):
        once = normalize_merchant("SQ *BLUE BOTTLE 1123")
        self.assertEqual(normalize_merchant(once), once)

    def test_all_noise_descriptor_is_preserved_not_emptied(self):
        # 7-ELEVEN is entirely "noise" by the token rule. Returning "" would
        # collapse every such merchant into one key.
        self.assertEqual(normalize_merchant("7-ELEVEN"), "7-ELEVEN")

    def test_distinct_merchants_do_not_collide(self):
        self.assertNotEqual(
            normalize_merchant("SAFEWAY 1842"),
            normalize_merchant("SAFECO INSURANCE"),
        )

    def test_strips_py_prefix(self):
        self.assertEqual(normalize_merchant("PY *STARBUCKS 0123"), "STARBUCKS")

    def test_strips_sp_prefix(self):
        self.assertEqual(normalize_merchant("SP WHOLE FOODS 456"), "WHOLE FOODS")

    def test_strips_paypal_prefix(self):
        self.assertEqual(normalize_merchant("PAYPAL *EBAY 789XYZ"), "EBAY")

    def test_strips_in_prefix(self):
        self.assertEqual(normalize_merchant("IN *CONSULTING 0987"), "CONSULTING")

    def test_empty_descriptor_raises(self):
        with self.assertRaises(ValueError):
            normalize_merchant("")


if __name__ == "__main__":
    unittest.main()
