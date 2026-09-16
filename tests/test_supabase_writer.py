import socket
import unittest
import urllib.error
from unittest import mock

from src.models import UpdatePayload
from src.supabase_writer import _rest, _resolve_by_name, _resolve_product, write_prices


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


class RestTimeoutRetryTests(unittest.TestCase):
    """F-232（2026-09-16）: dual-write が urlopen timeout 1回でそのまま失敗していた
    （DBZ分の書込漏れ）。_rest() が timeout だけリトライすること・timeout以外
    （HTTPError等）はリトライしないことを確認する。"""

    def setUp(self):
        conf_patch = mock.patch("src.supabase_writer._conf",
                                 return_value=("https://example.test", "key", None, "chat"))
        conf_patch.start()
        self.addCleanup(conf_patch.stop)
        sleep_patch = mock.patch("src.supabase_writer.time.sleep")
        sleep_patch.start()
        self.addCleanup(sleep_patch.stop)

    @staticmethod
    def _ok_response(body=b"[]"):
        resp = mock.MagicMock()
        resp.read.return_value = body
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        return resp

    def test_retries_once_after_a_socket_timeout_then_succeeds(self):
        with mock.patch("src.supabase_writer.urllib.request.urlopen",
                         side_effect=[socket.timeout("timed out"), self._ok_response()]) as m:
            result = _rest("price_history", method="POST", body=[{"a": 1}])
        self.assertEqual(result, [])
        self.assertEqual(m.call_count, 2)

    def test_urlerror_wrapping_a_timeout_also_retries(self):
        timeout_err = urllib.error.URLError(socket.timeout("timed out"))
        with mock.patch("src.supabase_writer.urllib.request.urlopen",
                         side_effect=[timeout_err, self._ok_response()]) as m:
            result = _rest("price_history")
        self.assertEqual(result, [])
        self.assertEqual(m.call_count, 2)

    def test_gives_up_after_the_retry_is_exhausted(self):
        with mock.patch("src.supabase_writer.urllib.request.urlopen",
                         side_effect=socket.timeout("timed out")) as m:
            with self.assertRaises(socket.timeout):
                _rest("price_history", method="POST", body=[{"a": 1}])
        self.assertEqual(m.call_count, 2)  # 最初の1回 + リトライ1回

    def test_non_timeout_errors_are_never_retried(self):
        http_err = urllib.error.HTTPError("url", 500, "Server Error", {}, None)
        with mock.patch("src.supabase_writer.urllib.request.urlopen", side_effect=http_err) as m:
            with self.assertRaises(urllib.error.HTTPError):
                _rest("price_history")
        self.assertEqual(m.call_count, 1)


if __name__ == "__main__":
    unittest.main()


def test_resolve_by_name_does_not_cross_games():
    """ポケモンの取得結果がワンピのSKUに解決されないこと（F-122 の再発防止）。

    2026-08-17/18: ポケモンの run に紛れた「決戦の刻」が名前一致で OPE-OP16-BOX に当たり、
    ¥200 が2日連続で書き込まれた。販売価格も ¥300/¥400 になって公開された。
    """
    from src.supabase_writer import _resolve_product

    product_rows = [
        {"sku": "OPE-OP16-BOX", "name_jp": "決戦の刻"},
        {"sku": "PKM-M6-BOX", "name_jp": "ストームエメラルダ"},
    ]
    active = {"OPE-OP16-BOX", "PKM-M6-BOX"}

    # pokemon の run からは OPE- のSKUに解決してはいけない
    sku, method = _resolve_product("pokemon", "", "決戦の刻", "BOX", active, product_rows)
    assert sku is None, f"ゲームをまたいで解決した: {sku} ({method})"

    # onepiece の run なら従来どおり解決する
    sku, method = _resolve_product("onepiece", "", "決戦の刻", "BOX", active, product_rows)
    assert sku == "OPE-OP16-BOX" and method == "name"
