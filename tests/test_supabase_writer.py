import socket
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from unittest import mock

from src.models import UpdatePayload
from src.supabase_writer import (
    _hold_same_day_manual, _rest, _resolve_by_name, _resolve_product, write_prices,
)


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


class HoldSameDayManualExpiryTests(unittest.TestCase):
    """T-567（2026-09-20）: `_hold_same_day_manual` を「同日だけ」から
    expires_at ベースの保護に拡張した回帰テスト。

    事故: 9/19 15:08 の natsuki-decision（PKM-M1S-BOX ¥7,100・
    expires_at=2026-09-25）が、日付が変わった 9/20 00:00 の毎時書き手に
    計算値(ホムラ+200=¥6,500)で上書きされた。「同日だけ」保護は翌日00:00に切れるため
    守れていなかった。
    """

    @staticmethod
    def _row(product_id="pid-1", unit="BOX", value=7100, valid_from=None,
             source="natsuki-decision", expires_at=None):
        if valid_from is None:
            valid_from = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        return {"product_id": product_id, "unit": unit, "value": value,
                "valid_from": valid_from, "source": source, "expires_at": expires_at}

    def _rows_to_write(self, product_id="pid-1", unit="BOX", value=6500):
        return [{"product_id": product_id, "kind": "kaitori", "unit": unit,
                 "currency": "JPY", "value": value, "source": "price-change-kaitori",
                 "valid_from": datetime.now(timezone.utc).isoformat()}]

    def test_natsuki_row_with_future_expiry_is_skipped(self):
        """natsuki% + expires 未来 → skip。"""
        now = datetime.now(timezone.utc)
        latest = self._row(valid_from=(now - timedelta(days=1)).isoformat(),
                           expires_at=(now + timedelta(days=5)).isoformat())
        with mock.patch("src.supabase_writer._rest", return_value=[latest]):
            kept, held = _hold_same_day_manual(self._rows_to_write())
        self.assertEqual(kept, [])
        self.assertEqual(len(held), 1)
        self.assertEqual(held[0]["manual"], 7100)

    def test_natsuki_row_with_past_expiry_falls_back_to_normal_rule(self):
        """expires 過去 → 書く（通常ルールへ復帰）。"""
        now = datetime.now(timezone.utc)
        latest = self._row(valid_from=(now - timedelta(days=10)).isoformat(),
                           expires_at=(now - timedelta(hours=1)).isoformat())
        with mock.patch("src.supabase_writer._rest", return_value=[latest]):
            kept, held = _hold_same_day_manual(self._rows_to_write())
        self.assertEqual(len(kept), 1)
        self.assertEqual(held, [])

    def test_manual_prefix_defaults_to_24h_hold(self):
        """manual% は expires_at 未指定なら既定24hホールド。"""
        now = datetime.now(timezone.utc)
        latest = self._row(source="manual-override", value=6800,
                           valid_from=(now - timedelta(hours=2)).isoformat())
        with mock.patch("src.supabase_writer._rest", return_value=[latest]):
            kept, held = _hold_same_day_manual(self._rows_to_write())
        self.assertEqual(kept, [])
        self.assertEqual(len(held), 1)
        self.assertEqual(held[0]["manual"], 6800)

    def test_manual_prefix_past_24h_falls_back_to_normal_rule(self):
        now = datetime.now(timezone.utc)
        latest = self._row(source="manual-override", value=6800,
                           valid_from=(now - timedelta(hours=30)).isoformat())
        with mock.patch("src.supabase_writer._rest", return_value=[latest]):
            kept, held = _hold_same_day_manual(self._rows_to_write())
        self.assertEqual(len(kept), 1)
        self.assertEqual(held, [])

    def test_same_day_natsuki_row_without_explicit_expiry_is_skipped(self):
        """当日 natsuki% → skip（expires_at 未指定＝永続のデフォルト挙動）。"""
        now = datetime.now(timezone.utc)
        latest = self._row(valid_from=now.isoformat(), expires_at=None)
        with mock.patch("src.supabase_writer._rest", return_value=[latest]):
            kept, held = _hold_same_day_manual(self._rows_to_write())
        self.assertEqual(kept, [])
        self.assertEqual(len(held), 1)

    def test_no_matching_row_writes_normally(self):
        with mock.patch("src.supabase_writer._rest", return_value=[]):
            kept, held = _hold_same_day_manual(self._rows_to_write())
        self.assertEqual(len(kept), 1)
        self.assertEqual(held, [])

    def test_lookup_failure_does_not_block_writes(self):
        with mock.patch("src.supabase_writer._rest", side_effect=RuntimeError("network down")):
            kept, held = _hold_same_day_manual(self._rows_to_write())
        self.assertEqual(len(kept), 1)
        self.assertEqual(held, [])


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
