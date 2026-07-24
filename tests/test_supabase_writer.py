import unittest

from src.models import UpdatePayload
from src.supabase_writer import _resolve_by_name, _resolve_product, write_prices


ROWS = [
    {"sku": "PKM-M5-BOX", "name_jp": "アビスアイ"},
    {"sku": "PKM-M5-NS", "name_jp": "アビスアイ（シュリンクなし）"},
]


class SupabaseResolverTests(unittest.TestCase):
    def test_code_wins(self):
        sku, method = _resolve_product("pokemon", "M5", "違う名前", "BOX", {r["sku"] for r in ROWS}, ROWS)
        self.assertEqual((sku, method), ("PKM-M5-BOX", "code"))

    def test_blank_code_falls_back_to_unique_name(self):
        sku, method = _resolve_product("pokemon", "", "〖BOX〗アビスアイ", "BOX", {r["sku"] for r in ROWS}, ROWS)
        self.assertEqual((sku, method), ("PKM-M5-BOX", "name"))

    def test_name_fallback_respects_unit(self):
        sku, method = _resolve_by_name("アビスアイ", "NS", ROWS)
        self.assertEqual((sku, method), ("PKM-M5-NS", "name"))

    def test_ambiguous_name_fails_closed(self):
        rows = ROWS + [{"sku": "PKM-OTHER-BOX", "name_jp": "アビスアイ"}]
        sku, method = _resolve_by_name("アビスアイ", "BOX", rows)
        self.assertIsNone(sku)
        self.assertEqual(method, "ambiguous-name")

    def test_onepiece_production_write_requires_approved_proposal(self):
        payload = UpdatePayload(2, "決戦の刻", "OP-16", 13200, None)
        with self.assertRaisesRegex(RuntimeError, "proposal・approval"):
            write_prices("onepiece", [payload], dry_run=False)


if __name__ == "__main__":
    unittest.main()
