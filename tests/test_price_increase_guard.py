import unittest

from src.price_increase_guard import should_hold


class PriceIncreaseGuardTests(unittest.TestCase):
    def test_over_five_percent_is_held(self):
        self.assertTrue(should_hold(10_000, 10_501)[0])

    def test_exact_five_percent_is_not_over(self):
        self.assertFalse(should_hold(10_000, 10_500)[0])

    def test_decrease_is_not_increase_hold(self):
        self.assertFalse(should_hold(10_000, 9_000)[0])


if __name__ == "__main__":
    unittest.main()
