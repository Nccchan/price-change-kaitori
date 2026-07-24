import unittest

from src.models import CardItem, CompetitorData, CompetitorType, GameType
from src.runto_overlay import apply_runto_max, _parse_onepiece_variations


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

    def test_special_set_does_not_overwrite_regular_box(self):
        item = CardItem(name="イーブイヒーローズ", code="S6a", price_1=140000)
        data = self.data(GameType.POKEMON, item)
        selected, matched = apply_runto_max("pokemon", data, 200, 200, [
            ("強化拡張パック イーブイヒーローズ", 138000, 138000),
            ("強化拡張パック イーブイヒーローズ イーブイズセット", 300000, 300000),
        ])
        self.assertEqual(matched, 1)
        self.assertEqual(selected, [])
        self.assertEqual(item.price_1, 140000)

    def test_onepiece_variations_select_shrink_and_carton(self):
        variation_json = """[
          {"attributes":{"attribute_pa_shrink":"5"},"display_price":11000,"variation_id":1,"variation_is_active":true,"variation_is_visible":true,"is_in_stock":true},
          {"attributes":{"attribute_pa_shrink":"ari"},"display_price":13000,"variation_id":2,"variation_is_active":true,"variation_is_visible":true,"is_in_stock":true},
          {"attributes":{"attribute_pa_shrink":"case"},"display_price":180000,"variation_id":3,"variation_is_active":true,"variation_is_visible":true,"is_in_stock":true}
        ]""".replace('"', '&quot;')
        page = f'''<form class="variations_form" data-product_variations="{variation_json}">
          <select id="pa_shrink" name="attribute_pa_shrink">
            <option value="5">テープカット</option><option value="ari">シュリンク有</option><option value="case">カートン</option>
          </select></form>'''
        product = _parse_onepiece_variations("決戦の刻【OP-16】", "https://example.test/op16", page)
        self.assertEqual((product.box, product.carton), (13000, 180000))
        self.assertEqual((product.box_variation_id, product.carton_variation_id), (2, 3))

    def test_duplicate_box_variation_fails_closed(self):
        variation_json = """[
          {"attributes":{"attribute_pa_shrink":"ari"},"display_price":13000,"variation_id":2,"variation_is_active":true,"variation_is_visible":true,"is_in_stock":true},
          {"attributes":{"attribute_pa_shrink":"ari"},"display_price":14000,"variation_id":4,"variation_is_active":true,"variation_is_visible":true,"is_in_stock":true},
          {"attributes":{"attribute_pa_shrink":"case"},"display_price":180000,"variation_id":3,"variation_is_active":true,"variation_is_visible":true,"is_in_stock":true}
        ]""".replace('"', '&quot;')
        page = f'''<form data-product_variations="{variation_json}"><select id="pa_shrink">
          <option value="ari">シュリンク有</option><option value="case">カートン</option></select></form>'''
        with self.assertRaisesRegex(ValueError, "有効variationが2件"):
            _parse_onepiece_variations("決戦の刻【OP-16】", "x", page)


if __name__ == "__main__":
    unittest.main()
