"""special_follow_kaitori.py の同日なつき決裁値ホールド（2026-09-17）の回帰テスト。

事象: 30th スペシャルセット2点(PKM-M6A-DECK / PKM-M6A-FUT-BOX)を毎時追従する
special_follow_kaitori.py には、メインの書き手 src.supabase_writer._hold_same_day_manual
と同じ「JST同日中のなつき決裁値(source like 'natsuki%')は上書きしない」保護が無く、
2026-09-17 15:38 の natsuki-decision 行(DECK ¥13,800 / FUT ¥38,500)を同日 16:00 の
毎時cronが計算値で上書きした（なつき「価格下げないで」16:04）。

ここでは追加した special_follow_kaitori._manual_hold_today() を、
1) 同日のnatsuki%行がある(product_id,unit)はホールドする
2) 前日(24時間より前)だけのnatsuki%行はホールドしない（同日行が無ければ通常どおり書く）
の2点で検証する。HTTPは一切呼ばず、_rest をモックする。
"""
import os
import re
import sys
import unittest
import urllib.parse
from datetime import datetime, timedelta, timezone
from io import StringIO
from contextlib import redirect_stdout
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import special_follow_kaitori as sfk  # noqa: E402
from src import supabase_writer  # noqa: E402


def _manual_rest_stub(rows_by_pid):
    """price_history?...source=like.natsuki%25... 相当のクエリを模した _rest スタブ。

    実際のSupabaseの `valid_from=gte.<since>` フィルタと同じ意味になるよう、
    渡された since より古い行はここで自前で除外する（本物のPostgRESTの代わり）。
    """
    def fake_rest(path, method="GET", body=None):
        assert path.startswith("price_history?kind=eq.kaitori")
        pid = re.search(r"product_id=eq\.([^&]+)", path).group(1)
        since_raw = re.search(r"valid_from=gte\.([^&]+)", path).group(1)
        since_dt = datetime.fromisoformat(urllib.parse.unquote(since_raw))
        rows = rows_by_pid.get(pid, [])
        matched = [r for r in rows
                   if datetime.fromisoformat(str(r["valid_from"]).replace("Z", "+00:00")) >= since_dt]
        matched.sort(key=lambda r: r["valid_from"], reverse=True)
        return matched[:1]
    return fake_rest


class ManualHoldTodayTests(unittest.TestCase):
    def test_holds_when_same_day_natsuki_row_exists(self):
        now_utc = datetime.now(timezone.utc)
        rows = {"pid-deck": [{"value": 13800, "valid_from": now_utc.isoformat()}]}
        with patch.object(sfk, "_rest", side_effect=_manual_rest_stub(rows)):
            held, value = sfk._manual_hold_today("pid-deck", "BOX")
        self.assertTrue(held)
        self.assertEqual(value, 13800)

    def test_does_not_hold_when_only_yesterdays_natsuki_row_exists(self):
        now_utc = datetime.now(timezone.utc)
        rows = {"pid-fut": [{"value": 37000, "valid_from": (now_utc - timedelta(hours=24)).isoformat()}]}
        with patch.object(sfk, "_rest", side_effect=_manual_rest_stub(rows)):
            held, value = sfk._manual_hold_today("pid-fut", "BOX")
        self.assertFalse(held)
        self.assertIsNone(value)


class RunSkipsManualHeldTargetTests(unittest.TestCase):
    """run()レベル: DECKに本日のnatsuki-decisionがある一方、FUT-BOXには無い、という
    2026-09-17と同じ形の混在状態で、DECKだけがホールドされFUT-BOXは通常どおり
    候補行になることを確認する。"""

    def test_run_dry_run_holds_deck_and_still_candidates_fut_box(self):
        now_utc = datetime.now(timezone.utc)
        yesterday_utc = now_utc - timedelta(hours=24)

        prods = [
            {"id": "pid-fut", "sku": "PKM-M6A-FUT-BOX", "homura_ref": "FUT"},
            {"id": "pid-deck", "sku": "PKM-M6A-DECK", "homura_ref": "DECK"},
        ]
        resolved = [
            {"price": 37500, "products": [{"sku": "PKM-M6A-FUT-BOX"}]},
            {"price": 12800, "products": [{"sku": "PKM-M6A-DECK"}]},
        ]
        manual_rows = {
            "pid-fut": [{"value": 37000, "valid_from": yesterday_utc.isoformat()}],  # 前日のみ→ホールドしない
            "pid-deck": [{"value": 13800, "valid_from": now_utc.isoformat()}],       # 本日→ホールドする
        }
        current_rows = [
            {"product_id": "pid-fut", "unit": "BOX", "value": 38500, "valid_from": yesterday_utc.isoformat()},
            {"product_id": "pid-deck", "unit": "BOX", "value": 13800, "valid_from": now_utc.isoformat()},
        ]

        def fake_rest(path, method="GET", body=None):
            if path.startswith("products?"):
                return prods
            if "source=like.natsuki" in path:
                return _manual_rest_stub(manual_rows)(path, method, body)
            if path.startswith("price_history?kind=eq.kaitori&product_id=in."):
                ids = re.search(r"product_id=in\.\(([^)]*)\)", path).group(1).split(",")
                return [r for r in current_rows if r["product_id"] in ids]
            raise AssertionError(f"想定外のクエリ: {path}")

        mock_fetcher = MagicMock()
        mock_fetcher.resolve_other_items.return_value = resolved
        mock_resolver_instance = MagicMock()
        mock_resolver_instance.enabled = True

        out = StringIO()
        with patch.object(sfk, "_rest", side_effect=fake_rest), \
             patch.object(supabase_writer, "_rest", side_effect=fake_rest), \
             patch.object(sfk, "HomuraFetcher", return_value=mock_fetcher), \
             patch.object(sfk, "HomuraSupabaseResolver", return_value=mock_resolver_instance), \
             redirect_stdout(out):
            rc = sfk.run(apply=False)

        self.assertEqual(rc, 0)
        printed = out.getvalue()
        self.assertIn("[manual-hold] 本日のなつき決裁値を維持: PKM-M6A-DECK ¥13,800", printed)
        self.assertIn("[candidate] PKM-M6A-FUT-BOX", printed)
        self.assertNotIn("[candidate] PKM-M6A-DECK", printed)


if __name__ == "__main__":
    unittest.main()
