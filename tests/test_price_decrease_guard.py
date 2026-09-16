import unittest
from types import SimpleNamespace

from src.price_decrease_guard import evaluate_result, should_hold


class PriceDecreaseGuardTests(unittest.TestCase):
    """2026-09-16 なつき決定「値下げは全承認」: 50%以上の急落だけ保留（取得ミス疑い）。"""

    def test_small_decrease_is_auto(self):
        self.assertFalse(should_hold(10000, 9000, "BOX")[0])

    def test_large_but_under_50_percent_is_auto(self):
        # 実例: 30th CELEBRATION FUTURISTIC BOX 50,000→35,000 (-30%) は自動反映対象
        self.assertFalse(should_hold(50000, 35000, "BOX")[0])

    def test_49_percent_is_auto(self):
        self.assertFalse(should_hold(10000, 5100, "BOX")[0])

    def test_50_percent_is_held(self):
        self.assertTrue(should_hold(10000, 5000, "BOX")[0])

    def test_over_50_percent_is_held(self):
        self.assertTrue(should_hold(10000, 4999, "BOX")[0])

    def test_carton_uses_same_50_percent_rule(self):
        self.assertFalse(should_hold(300000, 150001, "CARTON")[0])
        self.assertTrue(should_hold(300000, 150000, "CARTON")[0])

    def test_pokemon_price2_is_ns(self):
        r = SimpleNamespace(name="x", code="M5", current_price_1=10000,
            recommended_price_1=10000, current_price_2=10000, recommended_price_2=4000)
        holds = evaluate_result("pokemon", r)
        self.assertEqual([h.unit for h in holds], ["NS"])


if __name__ == "__main__":
    unittest.main()
