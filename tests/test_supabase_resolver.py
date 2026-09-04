import unittest
from unittest.mock import patch, MagicMock

from src.supabase_resolver import HomuraSupabaseResolver, normalize_ref


class NormalizeRefTests(unittest.TestCase):
    def test_normalizes_width_and_case(self):
        self.assertEqual(normalize_ref("OP-01"), normalize_ref("op01"))
        self.assertEqual(normalize_ref("OP-01"), normalize_ref("ＯＰ－０１"))

    def test_strips_spaces_and_symbols(self):
        self.assertEqual(normalize_ref("熊本 スペシャル"), normalize_ref("熊本スペシャル"))
        self.assertEqual(normalize_ref("熊本・スペシャル"), normalize_ref("熊本スペシャル"))
        # 【】等のブラケット自体は除去するが、中の語（BOXなど）はそのまま残る
        # （プレフィクスの除去は HomuraFetcher._clean_name の責務であり、normalize_ref は行わない）
        self.assertEqual(normalize_ref("【BOX】熊本スペシャル"), normalize_ref("BOX熊本スペシャル"))

    def test_empty_input(self):
        self.assertEqual(normalize_ref(""), "")
        self.assertEqual(normalize_ref(None), "")


class ResolveAllHomuraRefsTests(unittest.TestCase):
    def _resolver_with_url_key(self):
        return HomuraSupabaseResolver(url="https://example.supabase.co", service_key="test-key")

    def test_disabled_without_credentials_returns_empty(self):
        resolver = HomuraSupabaseResolver(url="", service_key="")
        self.assertFalse(resolver.enabled)
        self.assertEqual(resolver.resolve_all_homura_refs(), {})
        self.assertEqual(resolver.resolve_by_display_name("熊本スペシャル"), [])

    @patch("src.supabase_resolver.requests.get")
    def test_groups_rows_by_normalized_ref(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {"id": 1, "sku": "OPE-KUMA", "homura_ref": "熊本スペシャル", "is_active": True},
            {"id": 2, "sku": "PKM-S5R-BOX", "homura_ref": "S5R", "is_active": True},
        ]
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        resolver = self._resolver_with_url_key()
        grouped = resolver.resolve_all_homura_refs()

        self.assertIn(normalize_ref("熊本スペシャル"), grouped)
        self.assertEqual(grouped[normalize_ref("熊本スペシャル")][0]["sku"], "OPE-KUMA")

        # 2回目はキャッシュから返り、requests.get は再度呼ばれない
        resolver.resolve_all_homura_refs()
        mock_get.assert_called_once()

    @patch("src.supabase_resolver.requests.get")
    def test_resolve_by_display_name_exact_match_only(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {"id": 1, "sku": "OPE-KUMA", "homura_ref": "熊本スペシャル", "is_active": True},
        ]
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        resolver = self._resolver_with_url_key()

        self.assertEqual(len(resolver.resolve_by_display_name("熊本スペシャル")), 1)
        # 部分一致はしない
        self.assertEqual(resolver.resolve_by_display_name("熊本"), [])
        self.assertEqual(resolver.resolve_by_display_name("スペシャル"), [])


if __name__ == "__main__":
    unittest.main()
