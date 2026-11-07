#!/usr/bin/env python3
"""ポケモン「スペシャルセット」(ホムラ sub_category 130) の商品単位フォロー（T-507）。

## 背景・なぜ要るか

CATEGORY_REGISTRY で 130 (pokemon_special) は mode="monitor"（監視のみ・自動反映しない）。
単位が混在する棚（デッキ/福袋/プロモ単品 等）を丸ごと自動化すると事故りやすいため
カテゴリ全体を auto にはしない（homura_fetcher.py のコメント参照）。

一方で 2026-09-16 なつき決定「30周年のスペシャルセット2点は自動取得が望ましい」により、
このカテゴリの中から **商品単位で指定した2点だけ** を BOX と同じ scale で自動追従させる。
カテゴリ全体の mode="monitor" は変更しない（＝既存のmonitor動作を壊さない）。

## 対象（TARGETS）

  PKM-M6A-FUT-BOX（30th CELEBRATION FUTURISTIC BOX）
  PKM-M6A-DECK（30th CELEBRATION プレミアムデッキセット エーフィ・ブラッキー）

products.homura_ref にホムラの表示名（そのまま・正規化して突合）を設定し、
external_price_sources に kind="special" の行を追加して対象化する（--setup-sources）。
`kind="special"` は collection_supervision.build_manifest() の special 判定に使われ、
標準コレクタ（カテゴリ128/129の巡回）の「期待に反する」扱いにはならない。

## 取得方式

カテゴリ130全件を取得する既存の report-only 経路（HomuraFetcher.fetch_other_raw /
resolve_other_item・src/supabase_resolver.HomuraSupabaseResolver）をそのまま使う。
新しいスクレイピング経路は作らない。解決できたのが TARGETS の SKU のときだけ書込む。

## ガード（BOXと同じ扱い）

  - 値上げ: price_increase_guard と同じ閾値（2026-09-17〜 値上げは全承認・50%以上の急騰だけ保留・Telegram通知）
  - 値下げ: price_decrease_guard と同じ閾値（2026-09-16〜 値下げは全承認・50%以上の急落だけ保留・Telegram通知）
  - 差分だけ書く: src.supabase_writer._latest_kaitori_prices と同じロジックで
    「前回と同値・かつ今日(JST)書込済み」なら書かない
  - 準備中(kaitori_prep)は書込を止めない（取得側は常に最新を書く。公開可否はflag側の役割。
    既存の write_prices() と同じ方針）
  - 0円/未掲載/名前不一致は書かない（新規公開はなつき承認、フォロー開始後の欠測は保留のみ）

使い方:
  python scripts/special_follow_kaitori.py --setup-sources   # homura_ref/external_price_sources を1回設定
  python scripts/special_follow_kaitori.py                   # dry-run（既定）
  python scripts/special_follow_kaitori.py --apply           # 本番書込（price_history INSERT）

毎時 price-update.sh から呼ぶ想定（BOX/NSと同じ頻度）。
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# notify_dedupe は org 側（別リポジトリ）の共通部品をそのまま import する
# （_ORG_ROOT は src/supabase_writer.py と同じ決め打ちパス）。
sys.path.insert(0, "/Users/nastuki_sever/aigive/org/scripts/utils")

from dotenv import load_dotenv

load_dotenv()

from src.homura_fetcher import HomuraFetcher
from src.models import GameType
from src.supabase_resolver import HomuraSupabaseResolver, normalize_ref
from src.supabase_writer import (
    _rest, _conf, _latest_kaitori_prices, JST, _notify,
    _latest_manual_or_natsuki_rows, _parse_iso,
)
from src.price_increase_guard import should_hold as increase_should_hold
from src.price_decrease_guard import should_hold as decrease_should_hold
from notify_dedupe import notify_if_new  # noqa: E402

MARGIN_BOX = 200  # 「BOXと同じ扱い」(A: PKM BOX margin) に合わせる

# 毎時cronで同じ「値上げ/値下げ要承認」を繰り返し送らないための重複抑止状態ファイル。
# 保留が続く限り current は動かず proposed もほぼ同じなので、無抑止だと毎時同文が飛ぶ。
NOTIFY_STATE = "/Users/nastuki_sever/aigive/org/logs/notify-state/special_follow_kaitori.json"

# 表示名はホムラの実掲載文言（別名リスト対応・2026-11-07）。
#
# 2026-11-07 判明: ホムラがカテゴリ130の表示名を英語表記から日本語（カタカナ）表記に
# 変更したため、単一文字列の完全一致だった旧実装（homura_display_name）では
# PKM-M6A-FUT-BOX が照合できず更新停止していた（10/4 ¥61,200 で停止）。
# products.homura_ref は Supabase 側カラムが単一文字列のため複数名を持てない。
# このスクリプト内の照合はもう products.homura_ref 経由の単一一致（resolve_other_items）
# には依存せず、下記 homura_display_names（別名リスト）に対して
# fetch_other_raw() の生表示名を normalize_ref() で正規化した**完全一致のみ**で判定する
# （部分一致・推測マッチは禁止のまま・F-058）。homura_ref には別名リストの先頭
# （＝現行表記）だけを設定する（監視・突合レポート側が見るのはこの1本のため）。
TARGETS = {
    "PKM-M6A-FUT-BOX": {
        "homura_display_names": [
            "30th CELEBRATION フューチャリスティック BOX",  # 現行（2026-11-07 実測・fetch_other_raw）
            "30th CELEBRATION FUTURISTIC BOX",  # 旧表記（2026-09-16時点・英語表記が廃止された）
        ],
        "unit": "BOX",
        "note": "T-507 2026-09-16 30周年スペシャルセット自動追従(FUTURISTIC BOX)"
                "／2026-11-07 ホムラ表示名が日本語化され停止→別名リスト対応で復旧",
        "evidence_note": "2026-11-07 実測: ホムラ カテゴリ130 表示名"
                          " '30th CELEBRATION フューチャリスティック BOX' で ¥50,000 を確認済み(fetch_other_raw)",
    },
    "PKM-M6A-DECK": {
        "homura_display_names": [
            "30th CELEBRATION プレミアムデッキセット エーフィ・ブラッキー",
        ],
        "unit": "BOX",  # canonical_unit=BOX（DECKも旧来BOXレーンに格納。products側の実値に合わせる）
        "note": "T-507 2026-09-16 30周年スペシャルセット自動追従(プレミアムデッキセット)",
        "evidence_note": "2026-11-07 実測: ホムラ カテゴリ130 表示名"
                          " 'プレミアムデッキセット エーフィ・ブラッキー' で ¥15,500 を確認済み(fetch_other_raw)",
    },
    "PKM-M6A-CARDSET-9": {
        "homura_display_names": [
            "30th CELEBRATION カードセット　9種セット",
        ],
        "unit": "BOX",  # canonical_unit=BOX（sales ¥23,000 も BOX レーン。products側の実値に合わせる）
        "note": "2026-10-17 なつき指示『カード9種類の買取準備・9種類のセットだけにする』30周年9種セット自動追従",
        "evidence_note": "2026-11-07 実測: ホムラ カテゴリ130 表示名"
                          " '30th CELEBRATION カードセット　9種セット' で ¥14,300 を確認済み(fetch_other_raw)",
    },
}

# 正規化済み別名 -> SKU の逆引き（完全一致のみ・部分一致禁止）。
ALIAS_TO_SKU = {
    normalize_ref(alias): sku
    for sku, cfg in TARGETS.items()
    for alias in cfg["homura_display_names"]
}


def setup_sources():
    """products.homura_ref と external_price_sources 行を対象2点に設定する（冪等）。"""
    prods = {r["sku"]: r for r in _rest(
        "products?select=id,sku,homura_ref&sku=in.(" + ",".join(TARGETS) + ")")}
    for sku, cfg in TARGETS.items():
        row = prods.get(sku)
        if not row:
            print(f"[setup] {sku}: products に見つからない（スキップ）")
            continue
        canonical_name = cfg["homura_display_names"][0]
        if row.get("homura_ref") != canonical_name:
            _rest(f"products?id=eq.{row['id']}", method="PATCH",
                  body={"homura_ref": canonical_name})
            print(f"[setup] {sku}: homura_ref を設定 -> {canonical_name!r}"
                  f"（旧: {row.get('homura_ref')!r}）")
        else:
            print(f"[setup] {sku}: homura_ref 設定済み")

        existing = _rest("external_price_sources?select=id,status,external_ref"
                          f"&product_id=eq.{row['id']}&source=eq.homura")
        body = {
            "product_id": row["id"], "source": "homura",
            "search_hint": canonical_name,
            "external_ref": canonical_name,
            "unit": cfg["unit"], "status": "active",
            # kind制約(eps_kind_chk)は auto/derived/manual のみ。special値は無い。
            # kind='manual' は build_manifest() の special判定 (kind in {special,other,promo,manual})
            # にも該当するため、標準コレクタ(カテゴリ128/129巡回)の期待から外れる（狙い通り）。
            # eps_kind_required_chk が manual に evidence_url/evidence_note を要求するため付与する。
            "kind": "manual", "evidence_note": cfg["evidence_note"],
            "unit_verified": True, "freshness_sla_days": 3,
            "note": cfg["note"],
        }
        if existing:
            _rest(f"external_price_sources?id=eq.{existing[0]['id']}", method="PATCH", body=body)
            print(f"[setup] {sku}: external_price_sources 更新 (id={existing[0]['id']})")
        else:
            _rest("external_price_sources", method="POST", body=[body])
            print(f"[setup] {sku}: external_price_sources 新規作成")


def _current_price(product_id: str, unit: str):
    latest = _latest_kaitori_prices([product_id])
    row = latest.get((product_id, unit))
    return row[0] if row else None


def _manual_hold_today(product_id: str, unit: str):
    """(product_id, unit) の最新kaitori行が、まだ有効期限内のなつき決裁値
    (source like 'natsuki%') または一時的な手動上書き(source like 'manual%') なら
    (True, value) を返す。期限切れ・該当なしなら (False, None)。

    2026-09-17 事故（なつき「価格下げないで」16:04）: special_follow はメインの書き手
    src.supabase_writer._hold_same_day_manual と同じ保護を持たず、同日 15:38 に入った
    natsuki-decision（DECK ¥13,800 / FUT ¥38,500）を毎時cronが 16:00 に計算値
    （¥13,000 / ¥37,700）で上書きした。

    2026-09-20 T-567 事故: 「同日中だけ」の判定は翌日00:00に切れるため、9/19 15:08 の
    natsuki-decision（PKM-M1S-BOX ¥7,100・expires_at=2026-09-25）が 9/20 00:00 の
    毎時書き手に計算値(ホムラ+200=¥6,500)で上書きされた。DB側トリガー
    （20260913000003_kaitori_manual_scope.sql）と同じ規則
    （natsuki%は明示指定が無ければ永続・manual%は既定24h）を、
    supabase_writer._hold_same_day_manual と共通の _latest_manual_or_natsuki_rows /
    _parse_iso を再利用して判定する（二重管理を避ける）。
    """
    try:
        latest = _latest_manual_or_natsuki_rows([product_id])
    except Exception as e:
        # 照会に失敗したら握りつぶさず、従来どおり書く（安全側は"止めない"。
        # supabase_writer._hold_same_day_manual と同じ方針）。
        print(f"[manual-hold] 決裁値の照会に失敗したため通常判定を継続: {e}")
        return False, None

    row = latest.get((product_id, unit))
    if not row:
        return False, None

    source = str(row.get("source") or "")
    now = datetime.now(timezone.utc)
    expires = _parse_iso(row.get("expires_at"))
    if source.startswith("natsuki"):
        active = expires is None or expires > now
    elif source.startswith("manual"):
        if expires is None:
            valid_from = _parse_iso(row.get("valid_from"))
            expires = (valid_from + timedelta(hours=24)) if valid_from else None
        active = expires is None or expires > now
    else:
        active = False

    if not active:
        return False, None
    expires_label = expires.date().isoformat() if expires is not None else "なし・永続"
    label = "natsuki 決定値" if source.startswith("natsuki") else "manual 上書き"
    print(f"[manual-hold] {label}を保護（expires {expires_label}）: {product_id} {unit}")
    return True, int(row["value"])


def run(apply: bool):
    sb_url, sb_key, _, _ = _conf()
    resolver = HomuraSupabaseResolver(url=sb_url, service_key=sb_key)
    if not resolver.enabled:
        print("[special-follow] Supabase未接続のため中止（SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY未設定）")
        return 1
    fetcher = HomuraFetcher()
    # resolve_other_items() 経由（= products.homura_ref との単一完全一致）には依存しない。
    # ここでは生表示名を直接 ALIAS_TO_SKU（別名リスト・正規化済み）に完全一致させる
    # （部分一致・推測マッチは禁止のまま・F-058）。
    raw_items = fetcher.fetch_other_raw(GameType.POKEMON)

    prods = {r["sku"]: r for r in _rest(
        "products?select=id,sku,homura_ref&sku=in.(" + ",".join(TARGETS) + ")")}

    rows = []
    matched_skus = set()
    for item in raw_items:
        sku = ALIAS_TO_SKU.get(normalize_ref(item.get("name", "")))
        if sku is None:
            continue
        matched_skus.add(sku)
        product_id = prods.get(sku, {}).get("id")
        if not product_id:
            print(f"[skip] {sku}: products にレコードが見つからない")
            continue
        unit = TARGETS[sku]["unit"]
        raw_price = item.get("price")
        if not isinstance(raw_price, int) or raw_price <= 0:
            print(f"[skip] {sku}: 価格が不正 ({raw_price!r})")
            continue
        proposed = raw_price + MARGIN_BOX
        current = _current_price(product_id, unit)

        manual_hold, manual_value = _manual_hold_today(product_id, unit)
        if manual_hold:
            print(f"[manual-hold] 有効期限内の決裁値/手動上書きを維持: {sku} ¥{manual_value:,}")
            continue

        hold, inc, rate = increase_should_hold(current, proposed)
        if hold:
            msg = (f"⚠️ [special-follow] 値上げ要承認 {sku}: ¥{current:,}→¥{proposed:,} "
                   f"(+¥{inc:,}, +{rate:.1%})")
            print(msg)
            if apply:
                if not notify_if_new(NOTIFY_STATE, msg, _notify):
                    print("（同一内容を24時間以内に通知済みのためスキップ）")
            continue
        hold, dec, rate = decrease_should_hold(current, proposed, "BOX")
        if hold:
            msg = (f"⚠️ [special-follow] 値下げ要承認 {sku}: ¥{current:,}→¥{proposed:,} "
                   f"(-¥{dec:,}, -{rate:.1%})")
            print(msg)
            if apply:
                if not notify_if_new(NOTIFY_STATE, msg, _notify):
                    print("（同一内容を24時間以内に通知済みのためスキップ）")
            continue

        print(f"[candidate] {sku} {unit}: ¥{current if current is not None else '(未取得)'}"
              f" -> ¥{proposed:,}（ホムラ {raw_price:,}+{MARGIN_BOX}）")
        rows.append({"product_id": product_id, "kind": "kaitori", "unit": unit,
                     "currency": "JPY", "value": proposed,
                     "source": "price-change-kaitori",
                     "valid_from": datetime.now(timezone.utc).isoformat()})

    for sku, cfg in TARGETS.items():
        if sku in matched_skus:
            continue
        # Telegram通知はしない（ログのみ・なつき確認はTelegramの別経路に限定する方針）。
        print(f"[WARN][not-listed] {sku}: 本日のカテゴリ130に別名リストと一致する表示名が無い"
              f"（候補: {cfg['homura_display_names']}）（書込なし・保留のみ）")

    # T-508と同じ「差分だけ書く」（前回と同値・今日(JST)書込済みならskip）。
    if rows:
        today = datetime.now(JST).date()
        latest = _latest_kaitori_prices([r["product_id"] for r in rows])
        kept = []
        for r in rows:
            prev = latest.get((r["product_id"], r["unit"]))
            if prev is not None and prev[0] == r["value"]:
                from src.supabase_writer import _jst_date
                if _jst_date(prev[1]) == today:
                    print(f"[skip-unchanged] product_id={r['product_id']} {r['unit']}: 差分なし・本日既に書込済み")
                    continue
            kept.append(r)
        rows = kept

    if not apply:
        print(f"[special-follow DRY-RUN] insert予定 {len(rows)} 行")
        return 0

    for row in rows:
        _rest("price_history", method="POST", body=[row])
    print(f"[special-follow] {len(rows)}行INSERT")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--setup-sources", action="store_true",
                     help="products.homura_ref / external_price_sources を対象2点に設定して終了")
    args = ap.parse_args()
    if args.setup_sources:
        setup_sources()
        return 0
    return run(args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
