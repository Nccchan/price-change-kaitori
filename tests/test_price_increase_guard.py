import unittest

from src.price_increase_guard import should_hold


class PriceIncreaseGuardTests(unittest.TestCase):
    """2026-08-14 なつき決定で上限を 5% → 20% に引き上げた（T-131）。

    5%だと競合追随の通常幅（当日の保留19件は最大14%）を毎日ブロックし、
    承認導線が無いため永久に解消しなかった。20%は桁違いの取り違えだけを止める天井。
    """

    def test_normal_follow_up_passes(self):
        # 14%（当日のムニキスゼロ相当）は通る
        self.assertFalse(should_hold(7_600, 8_700)[0])

    def test_twenty_percent_exactly_is_not_over(self):
        self.assertFalse(should_hold(10_000, 12_000)[0])

    def test_over_twenty_percent_is_held(self):
        self.assertTrue(should_hold(10_000, 12_001)[0])

    def test_digit_slip_is_held(self):
        # 桁違い（競合データの取り違え）は必ず止める
        self.assertTrue(should_hold(10_000, 100_000)[0])

    def test_decrease_is_not_increase_hold(self):
        self.assertFalse(should_hold(10_000, 9_000)[0])


if __name__ == "__main__":
    unittest.main()
