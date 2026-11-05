import json
import unittest
from pathlib import Path

from src.shinsoku_fetcher import ShinsokuFetcher

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "shinsoku_page0.json"


class ShinsokuFetcherCandidateShapeTests(unittest.TestCase):
    """_candidates_from_items の候補構造・source名前空間・除外条件を検証する。"""

    def setUp(self):
        self.fetcher = ShinsokuFetcher()

    def test_candidate_shape_and_source_namespace(self):
        items = [{
            "id": "IAP2500002671",
            "item_id": "IAP2500002671",
            "name": "ギフトパック",
            "name_processed": "処理済みの名前",
            "type": "BOX",
            "is_postal_buy_target": True,
            "is_full_amount_flag": False,
            "postal_purchase_price_s": 1100000,
            "postal_purchase_price_a": None,
        }]
        result = self.fetcher._candidates_from_items(
            items, "BOX", "2026-11-05T00:00:00+00:00",
            "https://shinsoku-tcg.com/api/items?page=0", "deadbeef",
        )
        self.assertEqual(len(result), 1)
        c = result[0]
        self.assertEqual(c["source"], "shinsoku")
        self.assertEqual(c["external_ref"], "IAP2500002671")
        # name_processed ではなく raw の name を保持する
        self.assertEqual(c["name"], "ギフトパック")
        self.assertEqual(c["unit"], "BOX")
        self.assertEqual(c["price"], 1100000)
        self.assertFalse(c["is_full_amount_flag"])
        self.assertEqual(c["observed_at"], "2026-11-05T00:00:00+00:00")
        self.assertEqual(c["page_url"], "https://shinsoku-tcg.com/api/items?page=0")
        self.assertEqual(c["response_sha256"], "deadbeef")

    def test_excludes_non_postal_buy_target(self):
        items = [
            {"item_id": "IAP1", "name": "対象外", "type": "BOX",
             "is_postal_buy_target": False, "is_full_amount_flag": False,
             "postal_purchase_price_s": 5000},
            {"item_id": "IAP2", "name": "対象", "type": "BOX",
             "is_postal_buy_target": True, "is_full_amount_flag": False,
             "postal_purchase_price_s": 6000},
        ]
        result = self.fetcher._candidates_from_items(items, "BOX", "t", "u", "h")
        self.assertEqual([c["external_ref"] for c in result], ["IAP2"])

    def test_null_price_is_kept_not_dropped(self):
        # price_s が null でも is_postal_buy_target が true なら保持する
        # （「安い」と「未設定」を区別するため。除外は is_postal_buy_target=false のときだけ）
        items = [{
            "item_id": "IAP3", "name": "価格未設定", "type": "CARTON",
            "is_postal_buy_target": True, "is_full_amount_flag": False,
            "postal_purchase_price_s": None,
        }]
        result = self.fetcher._candidates_from_items(items, "CARTON", "t", "u", "h")
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0]["price"])
        self.assertEqual(result[0]["unit"], "CARTON")

    def test_unit_comes_from_type_field(self):
        items = [{"item_id": "IAP4", "name": "N", "type": "CARTON",
                  "is_postal_buy_target": True, "is_full_amount_flag": False,
                  "postal_purchase_price_s": 100}]
        # unit引数はBOXでも、item側のtypeがCARTONならCARTONを採用する
        result = self.fetcher._candidates_from_items(items, "BOX", "t", "u", "h")
        self.assertEqual(result[0]["unit"], "CARTON")


class ShinsokuFetcherDedupTests(unittest.TestCase):
    def test_dedup_keeps_first_occurrence(self):
        candidates = [
            {"external_ref": "IAP1", "price": 100},
            {"external_ref": "IAP2", "price": 200},
            {"external_ref": "IAP1", "price": 999},  # 重複（後発は捨てる）
        ]
        result = ShinsokuFetcher._dedup(candidates)
        self.assertEqual([c["external_ref"] for c in result], ["IAP1", "IAP2"])
        self.assertEqual(result[0]["price"], 100)


class ShinsokuFetcherFixtureReplayTests(unittest.TestCase):
    """--dry-run が再生する実フィクスチャ（type=BOX・sort=name_asc・page=0・limit=100）の検証。"""

    def setUp(self):
        self.fetcher = ShinsokuFetcher()
        with open(FIXTURE_PATH, encoding="utf-8") as f:
            self.raw = json.load(f)

    def test_fixture_is_well_formed(self):
        self.assertTrue(self.raw["ok"])
        self.assertIn("items", self.raw["data"])
        self.assertIn("has_more", self.raw["data"])

    def test_fetch_unit_replays_fixture_without_network(self):
        result = self.fetcher.fetch_unit("BOX", fixture_path=FIXTURE_PATH)
        postal_targets = [i for i in self.raw["data"]["items"] if i.get("is_postal_buy_target")]
        self.assertEqual(len(result), len(postal_targets))
        for c in result:
            self.assertEqual(c["source"], "shinsoku")
            self.assertEqual(c["unit"], "BOX")
            self.assertTrue(c["external_ref"])

    def test_fetch_unit_stops_after_one_page_in_fixture_mode(self):
        # フィクスチャは has_more=true のページだが、後続ページを持たないため
        # fixture_path指定時は常に1ページで打ち切る（無限ループ・存在しないファイル読み込みを防ぐ）。
        self.assertTrue(self.raw["data"]["has_more"])
        result = self.fetcher.fetch_unit("BOX", fixture_path=FIXTURE_PATH)
        self.assertTrue(len(result) > 0)

    def test_fetch_all_dry_run_style_only_uses_box_fixture(self):
        # main() の --dry-run はBOXのみ再生する（CARTON用フィクスチャは持たない仕様）。
        result = self.fetcher.fetch_unit("BOX", fixture_path=FIXTURE_PATH)
        self.assertTrue(all(c["unit"] == "BOX" for c in result))


if __name__ == "__main__":
    unittest.main()
