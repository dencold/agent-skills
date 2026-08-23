import sys
import pathlib
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from lodging_calc import rooms_needed, rental_total, hotel_total, effective_cost


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


if __name__ == "__main__":
    unittest.main()
