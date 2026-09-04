import unittest
from unittest.mock import MagicMock

from src.homura_fetcher import HomuraFetcher
from src.models import GameType


class HomuraFetcherTests(unittest.TestCase):
    def setUp(self):
        self.fetcher = HomuraFetcher()

    def test_category_registry_covers_public_tcg_categories(self):
        self.assertEqual(
            set(self.fetcher.CATEGORY_REGISTRY),
            {128, 129, 130, 131, 183, 132, 133, 160, 159, 172, 157, 171},
        )

    def test_pokemon_ns_category_enabled(self):
        self.assertEqual(self.fetcher.CATEGORY_IDS[GameType.POKEMON]["price_2"], 129)
        self.assertNotIn("price_1_extra", self.fetcher.CATEGORY_IDS[GameType.POKEMON])

    def test_condition_prefixes_normalize_to_same_name(self):
        self.assertEqual(self.fetcher._clean_name("〖BOX〗アビスアイ"), "アビスアイ")
        self.assertEqual(self.fetcher._clean_name("〖シュリンク無しBOX〗アビスアイ"), "アビスアイ")

    def test_box_and_ns_merge(self):
        box = [{"name": "アビスアイ", "raw_code": "", "price": 11200}]
        ns = [{"name": "アビスアイ", "raw_code": "", "price": 9000}]
        result = self.fetcher._merge(box, ns, GameType.POKEMON)
        self.assertEqual(len(result), 1)
        self.assertEqual((result[0].price_1, result[0].price_2), (11200, 9000))


class HomuraOtherCategoryTests(unittest.TestCase):
    """T-348: ホムラ「その他」カテゴリ（弾コード無し商品）の取得・突合。"""

    def setUp(self):
        self.fetcher = HomuraFetcher()

    def test_other_category_ids_known_games(self):
        self.assertEqual(self.fetcher.OTHER_CATEGORY_IDS[GameType.POKEMON], [130])
        self.assertEqual(self.fetcher.OTHER_CATEGORY_IDS[GameType.ONEPIECE], [160])

    def test_other_category_ids_unconfirmed_games_empty(self):
        # dragonball/yugioh は2026-09-05時点でサイト上に専用「その他」カテゴリを確認できていない。
        # 空リストのままにして、確認できるまで推測でIDを埋めない。
        self.assertEqual(self.fetcher.OTHER_CATEGORY_IDS[GameType.DRAGONBALL], [])
        self.assertEqual(self.fetcher.OTHER_CATEGORY_IDS[GameType.YUGIOH], [])

    def test_fetch_other_raw_dedups_responsive_double_markup(self):
        # ホムラのページは同一商品の h5 がレスポンシブ用に2重マークアップされている。
        duplicated = [
            {"name": "熊本スペシャル", "raw_code": "", "price": 5000},
            {"name": "熊本スペシャル", "raw_code": "", "price": 5000},
        ]
        self.fetcher._fetch_category = MagicMock(return_value=duplicated)
        result = self.fetcher.fetch_other_raw(GameType.POKEMON)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "熊本スペシャル")

    def test_fetch_other_raw_no_category_returns_empty(self):
        self.fetcher._fetch_category = MagicMock(side_effect=AssertionError("呼ばれてはいけない"))
        result = self.fetcher.fetch_other_raw(GameType.DRAGONBALL)
        self.assertEqual(result, [])

    def test_resolve_other_item_matches_via_inline_code(self):
        resolver = MagicMock()
        resolver.resolve.return_value = [{"sku": "OPE-01-BOX"}]
        resolver.resolve_by_display_name.return_value = []
        item = {"name": "OP-01 ロマンスドーン", "raw_code": "", "price": 1000}

        result = self.fetcher.resolve_other_item(item, resolver)

        self.assertTrue(result["matched"])
        self.assertEqual(result["method"], "code")
        resolver.resolve.assert_called_once_with("OP-01")
        resolver.resolve_by_display_name.assert_not_called()

    def test_resolve_other_item_matches_via_display_name(self):
        resolver = MagicMock()
        resolver.resolve.return_value = []
        resolver.resolve_by_display_name.return_value = [{"sku": "OPE-KUMA"}]
        item = {"name": "熊本スペシャル", "raw_code": "", "price": 5000}

        result = self.fetcher.resolve_other_item(item, resolver)

        self.assertTrue(result["matched"])
        self.assertEqual(result["method"], "name")

    def test_resolve_other_item_unmatched_stays_unmatched(self):
        # 部分一致・推測はしない: 両方空なら未突合のまま返す
        resolver = MagicMock()
        resolver.resolve.return_value = []
        resolver.resolve_by_display_name.return_value = []
        item = {"name": "謎の商品", "raw_code": "", "price": 100}

        result = self.fetcher.resolve_other_item(item, resolver)

        self.assertFalse(result["matched"])
        self.assertIsNone(result["method"])
        self.assertEqual(result["products"], [])


if __name__ == "__main__":
    unittest.main()
