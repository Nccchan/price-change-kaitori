import unittest

from src.models import CardItem, CompetitorData, CompetitorType, GameType
from src.runto_overlay import apply_runto_max


class RuntoOverlayTests(unittest.TestCase):
    def data(self, game, item):
        return CompetitorData(game=game, competitor=CompetitorType.HOMURA, date="7/23", items=[item])

    def test_dragonball_uses_runto_before_gate(self):
        item = CardItem(name="MANGA BOOSTER 01", code="SB-01", price_1=100000, price_2=None)
        data = self.data(GameType.DRAGONBALL, item)
        selected, matched = apply_runto_max("dragonball", data, 200, 1000,
            [("MANGA BOOSTER 01【SB-01】", 150000, 150000)])
        self.assertEqual(matched, 1)
        self.assertEqual(item.price_1, 150000)
        self.assertEqual(selected[0].final, 150200)

    def test_homura_wins_max(self):
        item = CardItem(name="ロストアビス", code="S11", price_1=44000, price_2=40000)
        data = self.data(GameType.POKEMON, item)
        selected, _ = apply_runto_max("pokemon", data, 200, 200,
            [("拡張パック ロストアビス BOX", 39000, 39000)])
        self.assertEqual(selected, [])
        self.assertEqual(item.price_1, 44000)

    def test_pokemon_narrow_range_uses_upper_as_box(self):
        item = CardItem(name="25thアニバーサリー", code="S8a", price_1=70000, price_2=None)
        data = self.data(GameType.POKEMON, item)
        selected, _ = apply_runto_max("pokemon", data, 200, 200,
            [("25thアニバーサリー BOX", 75000, 80000)])
        self.assertEqual(item.price_1, 80000)
        self.assertEqual(selected[0].final, 80200)

    def test_zero_matches_is_failure(self):
        item = CardItem(name="x", code="X", price_1=1)
        data = self.data(GameType.POKEMON, item)
        with self.assertRaises(RuntimeError):
            apply_runto_max("pokemon", data, 200, 200, [("unrelated", 1, 1)])


if __name__ == "__main__":
    unittest.main()
