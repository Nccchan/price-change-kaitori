import unittest

from src.price_increase_guard import should_hold


class PriceIncreaseGuardTests(unittest.TestCase):
    """2026-09-17 なつき決定（T-131 → A）: 値上げは幅を問わず自動反映する。

    保留するのは値下げガードと対称の「前回比+50%以上＝取得ミス疑い」だけ。
    旧ルール（5%→20%は2026-08-14 T-131 A案の中間段階）は撤廃した。
    """

    def test_normal_follow_up_passes(self):
        # 14%（通常の競合追随幅）は通る
        self.assertFalse(should_hold(7_600, 8_700)[0])

    def test_twenty_percent_no_longer_held(self):
        # 旧上限だった20%超も、50%未満なら自動反映
        self.assertFalse(should_hold(10_000, 12_001)[0])

    def test_fifty_percent_exactly_is_not_over(self):
        self.assertFalse(should_hold(10_000, 15_000)[0])

    def test_over_fifty_percent_is_held(self):
        self.assertTrue(should_hold(10_000, 15_001)[0])

    def test_digit_slip_is_held(self):
        # 桁違い（競合データの取り違え）は必ず止める
        self.assertTrue(should_hold(10_000, 100_000)[0])

    def test_decrease_is_not_increase_hold(self):
        self.assertFalse(should_hold(10_000, 9_000)[0])


if __name__ == "__main__":
    unittest.main()
