import unittest

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


if __name__ == "__main__":
    unittest.main()
