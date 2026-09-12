import unittest
from unittest.mock import MagicMock, patch

from src.supabase_resolver import HomuraSupabaseResolver, normalize_ref
from src.supabase_writer import _resolve_product


class AcquisitionRegressions(unittest.TestCase):
    def test_explicit_onepiece_code_cannot_fall_back_to_other_code(self):
        rows = [{"sku": "OPE-OP16-BOX", "name_jp": "Test expansion"}]
        sku, _ = _resolve_product("onepiece", "OP-15", "Test expansion", "BOX", {rows[0]["sku"]}, rows)
        self.assertIsNone(sku)

    def test_explicit_pokemon_code_cannot_fall_back_to_other_code(self):
        rows = [{"sku": "PKM-SV5K-BOX", "name_jp": "Test expansion"}]
        sku, _ = _resolve_product("pokemon", "SV5M", "Test expansion", "BOX", {rows[0]["sku"]}, rows)
        self.assertIsNone(sku)

    def test_barcode_can_still_use_unique_name(self):
        rows = [{"sku": "PKM-SV5K-BOX", "name_jp": "Test expansion"}]
        sku, _ = _resolve_product("pokemon", "4900000000000", "Test expansion", "BOX", {rows[0]["sku"]}, rows)
        self.assertEqual(sku, rows[0]["sku"])

    @patch("src.supabase_resolver.requests.get")
    def test_all_refs_includes_products_after_first_page(self, get):
        page1 = MagicMock()
        page1.json.return_value = [{"id": str(i), "sku": f"PKM-X{i}-BOX", "homura_ref": f"X{i}"} for i in range(1000)]
        page2 = MagicMock()
        page2.json.return_value = [{"id": "tail", "sku": "OPE-OP16-BOX", "homura_ref": "OP-16"}]
        get.side_effect = [page1, page2]
        resolver = HomuraSupabaseResolver(url="https://example.invalid", service_key="test")
        self.assertIn(normalize_ref("OP-16"), resolver.resolve_all_homura_refs())
        self.assertEqual(get.call_count, 2)

    @patch("src.supabase_resolver.requests.get")
    def test_failed_fetch_does_not_cache_empty_forever(self, get):
        success = MagicMock()
        success.json.return_value = [{"id": "p", "sku": "OPE-OP16-BOX", "homura_ref": "OP-16"}]
        get.side_effect = [RuntimeError("offline"), success]
        resolver = HomuraSupabaseResolver(url="https://example.invalid", service_key="test")
        self.assertEqual(resolver.resolve_all_homura_refs(), {})
        self.assertIn(normalize_ref("OP-16"), resolver.resolve_all_homura_refs())


if __name__ == "__main__":
    unittest.main()
