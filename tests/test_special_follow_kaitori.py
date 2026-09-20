"""special_follow_kaitori.py の なつき決裁値/manual上書きホールドの回帰テスト。

## 背景1（2026-09-17・F-096系）
special_follow_kaitori.py には、メインの書き手 src.supabase_writer._hold_same_day_manual
と同じ「JST同日中のなつき決裁値(source like 'natsuki%')は上書きしない」保護が無く、
2026-09-17 15:38 の natsuki-decision 行(DECK ¥13,800 / FUT ¥38,500)を同日 16:00 の
毎時cronが計算値で上書きした（なつき「価格下げないで」16:04）。

## 背景2（2026-09-20・T-567）
「同日中だけ」の判定は翌日00:00に切れるため、9/19 15:08 の natsuki-decision
（PKM-M1S-BOX ¥7,100・expires_at=2026-09-25）が 9/20 00:00 の毎時書き手に
計算値(ホムラ+200)で上書きされた。DB側トリガー
（20260913000003_kaitori_manual_scope.sql）と同じ規則をアプリ側にも入れた:
  - natsuki%: expires_at が NULL または未来なら保護（明示指定が無ければ永続）
  - manual%:  expires_at が無ければ valid_from+24h を既定として保護
  - expires_at を過ぎていれば保護は外れ、通常のルール計算に戻る

ここでは `_manual_hold_today()` を、
1) 当日のnatsuki%行（expires_at無し=永続）はホールドする
2) 数日前のnatsuki%行でもexpires_atが未来ならホールドする
3) natsuki%行でもexpires_atが過去ならホールドしない（通常ルールへ復帰）
4) manual%行はexpires_at無しなら既定24hでホールドし、24hを過ぎたらホールドしない
の4点で検証する。HTTPは一切呼ばず、_rest をモックする。
"""
import os
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone
from io import StringIO
from contextlib import redirect_stdout
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import special_follow_kaitori as sfk  # noqa: E402
from src import supabase_writer  # noqa: E402


def _hold_rest_stub(rows_by_pid):
    """price_history?...&or=(source.like.natsuki%25,source.like.manual%25)&product_id=in.(...)
    相当のクエリを模した _rest スタブ。(product_id,unit)ごとに valid_from 最新の1件を返す
    （本物のPostgRESTの order+重複排除の代わり）。
    """
    def fake_rest(path, method="GET", body=None):
        assert path.startswith("price_history?kind=eq.kaitori")
        assert "or=(source.like.natsuki%25,source.like.manual%25)" in path
        ids = re.search(r"product_id=in\.\(([^)]*)\)", path).group(1).split(",")
        out, seen = [], set()
        for pid in ids:
            rows = sorted(rows_by_pid.get(pid, []), key=lambda r: r["valid_from"], reverse=True)
            for r in rows:
                k = (r["product_id"], r["unit"])
                if k in seen:
                    continue
                seen.add(k)
                out.append(r)
        return out
    return fake_rest


def _row(product_id, unit, value, valid_from, source="natsuki-decision", expires_at=None):
    return {"product_id": product_id, "unit": unit, "value": value,
            "valid_from": valid_from, "source": source, "expires_at": expires_at}


class ManualHoldTodayTests(unittest.TestCase):
    def test_holds_same_day_natsuki_row_with_no_explicit_expiry(self):
        """当日 natsuki% → skip（2026-09-17 事故の再現ケース）。"""
        now_utc = datetime.now(timezone.utc)
        rows = {"pid-deck": [_row("pid-deck", "BOX", 13800, now_utc.isoformat())]}
        with patch.object(supabase_writer, "_rest", side_effect=_hold_rest_stub(rows)):
            held, value = sfk._manual_hold_today("pid-deck", "BOX")
        self.assertTrue(held)
        self.assertEqual(value, 13800)

    def test_holds_older_natsuki_row_when_expiry_is_in_the_future(self):
        """natsuki% + expires 未来 → skip（T-567: 9/19の決定が9/25まで有効な例）。"""
        now_utc = datetime.now(timezone.utc)
        valid_from = now_utc - timedelta(days=1)
        expires_at = now_utc + timedelta(days=5)
        rows = {"pid-m1s": [_row("pid-m1s", "BOX", 7100, valid_from.isoformat(),
                                 expires_at=expires_at.isoformat())]}
        with patch.object(supabase_writer, "_rest", side_effect=_hold_rest_stub(rows)):
            held, value = sfk._manual_hold_today("pid-m1s", "BOX")
        self.assertTrue(held)
        self.assertEqual(value, 7100)

    def test_does_not_hold_natsuki_row_once_expiry_has_passed(self):
        """expires 過去 → 書く（通常ルールへ復帰）。"""
        now_utc = datetime.now(timezone.utc)
        valid_from = now_utc - timedelta(days=10)
        expires_at = now_utc - timedelta(hours=1)
        rows = {"pid-m1s": [_row("pid-m1s", "BOX", 7100, valid_from.isoformat(),
                                 expires_at=expires_at.isoformat())]}
        with patch.object(supabase_writer, "_rest", side_effect=_hold_rest_stub(rows)):
            held, value = sfk._manual_hold_today("pid-m1s", "BOX")
        self.assertFalse(held)
        self.assertIsNone(value)

    def test_manual_prefix_row_defaults_to_24h_hold(self):
        """manual% は expires_at 未指定なら既定24hホールド。"""
        now_utc = datetime.now(timezone.utc)
        valid_from = now_utc - timedelta(hours=2)
        rows = {"pid-x": [_row("pid-x", "BOX", 5000, valid_from.isoformat(),
                               source="manual-override")]}
        with patch.object(supabase_writer, "_rest", side_effect=_hold_rest_stub(rows)):
            held, value = sfk._manual_hold_today("pid-x", "BOX")
        self.assertTrue(held)
        self.assertEqual(value, 5000)

    def test_manual_prefix_row_stops_holding_after_24h(self):
        """manual% の既定24hを過ぎたらホールドしない（通常ルールへ復帰）。"""
        now_utc = datetime.now(timezone.utc)
        valid_from = now_utc - timedelta(hours=30)
        rows = {"pid-x": [_row("pid-x", "BOX", 5000, valid_from.isoformat(),
                               source="manual-override")]}
        with patch.object(supabase_writer, "_rest", side_effect=_hold_rest_stub(rows)):
            held, value = sfk._manual_hold_today("pid-x", "BOX")
        self.assertFalse(held)
        self.assertIsNone(value)


class RunSkipsManualHeldTargetTests(unittest.TestCase):
    """run()レベル: DECKに有効期限内のnatsuki-decisionがある一方、FUT-BOXは
    natsuki-decisionの期限が切れている、という混在状態で、DECKだけがホールドされ
    FUT-BOXは通常どおり候補行になることを確認する。"""

    def test_run_dry_run_holds_deck_and_still_candidates_fut_box(self):
        now_utc = datetime.now(timezone.utc)

        prods = [
            {"id": "pid-fut", "sku": "PKM-M6A-FUT-BOX", "homura_ref": "FUT"},
            {"id": "pid-deck", "sku": "PKM-M6A-DECK", "homura_ref": "DECK"},
        ]
        resolved = [
            {"price": 37500, "products": [{"sku": "PKM-M6A-FUT-BOX"}]},
            {"price": 12800, "products": [{"sku": "PKM-M6A-DECK"}]},
        ]
        manual_rows = {
            # 期限切れ(1時間前に失効)→ホールドしない
            "pid-fut": [_row("pid-fut", "BOX", 37000, (now_utc - timedelta(days=10)).isoformat(),
                             expires_at=(now_utc - timedelta(hours=1)).isoformat())],
            # 本日決裁・expires_at無し(永続)→ホールドする
            "pid-deck": [_row("pid-deck", "BOX", 13800, now_utc.isoformat())],
        }
        current_rows = [
            {"product_id": "pid-fut", "unit": "BOX", "value": 38500,
             "valid_from": (now_utc - timedelta(days=1)).isoformat()},
            {"product_id": "pid-deck", "unit": "BOX", "value": 13800, "valid_from": now_utc.isoformat()},
        ]

        def fake_rest(path, method="GET", body=None):
            if path.startswith("products?"):
                return prods
            if "or=(source.like.natsuki%25,source.like.manual%25)" in path:
                return _hold_rest_stub(manual_rows)(path, method, body)
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
        self.assertIn("natsuki 決定値を保護", printed)
        self.assertIn("PKM-M6A-DECK", printed)
        self.assertIn("[candidate] PKM-M6A-FUT-BOX", printed)
        self.assertNotIn("[candidate] PKM-M6A-DECK", printed)


if __name__ == "__main__":
    unittest.main()
