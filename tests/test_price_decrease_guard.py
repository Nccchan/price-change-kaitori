import unittest
from types import SimpleNamespace

from src.price_decrease_guard import evaluate_result, should_hold


class PriceDecreaseGuardTests(unittest.TestCase):
    def test_box_within_threshold_is_auto(self):
        self.assertFalse(should_hold(10000, 9000, "BOX")[0])

    def test_box_over_ten_percent_is_held(self):
        self.assertTrue(should_hold(10000, 8999, "BOX")[0])

    def test_box_over_3000_yen_is_held(self):
        self.assertTrue(should_hold(50000, 46999, "BOX")[0])

    def test_exact_3000_and_exact_ten_percent_are_not_over(self):
        self.assertFalse(should_hold(30000, 27000, "BOX")[0])

    def test_carton_uses_20000_yen_limit(self):
        self.assertFalse(should_hold(300000, 280000, "CARTON")[0])
        self.assertTrue(should_hold(300000, 279999, "CARTON")[0])

    def test_pokemon_price2_is_ns(self):
        r = SimpleNamespace(name="x", code="M5", current_price_1=10000,
            recommended_price_1=10000, current_price_2=10000, recommended_price_2=8000)
        holds = evaluate_result("pokemon", r)
        self.assertEqual([h.unit for h in holds], ["NS"])


if __name__ == "__main__":
    unittest.main()
