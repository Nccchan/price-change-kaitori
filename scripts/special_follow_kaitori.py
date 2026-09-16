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

  - 値上げ: price_increase_guard と同じ閾値（20%超は保留・Telegram通知）
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
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# notify_dedupe は org 側（別リポジトリ）の共通部品をそのまま import する
# （_ORG_ROOT は src/supabase_writer.py と同じ決め打ちパス）。
sys.path.insert(0, "/Users/nastuki_sever/aigive/org/scripts/utils")

from dotenv import load_dotenv

load_dotenv()

from src.homura_fetcher import HomuraFetcher
from src.models import GameType
from src.supabase_resolver import HomuraSupabaseResolver
from src.supabase_writer import _rest, _conf, _latest_kaitori_prices, JST, _notify
from src.price_increase_guard import should_hold as increase_should_hold
from src.price_decrease_guard import should_hold as decrease_should_hold
from notify_dedupe import notify_if_new  # noqa: E402

MARGIN_BOX = 200  # 「BOXと同じ扱い」(A: PKM BOX margin) に合わせる

# 毎時cronで同じ「値上げ/値下げ要承認」を繰り返し送らないための重複抑止状態ファイル。
# 保留が続く限り current は動かず proposed もほぼ同じなので、無抑止だと毎時同文が飛ぶ。
NOTIFY_STATE = "/Users/nastuki_sever/aigive/org/logs/notify-state/special_follow_kaitori.json"

# 表示名はホムラの実掲載文言そのもの（2026-09-16 実測・fetch_other_raw で確認済み）。
# resolve_other_item は products.homura_ref をこの文字列に正規化一致させて解決する。
TARGETS = {
    "PKM-M6A-FUT-BOX": {
        "homura_display_name": "30th CELEBRATION FUTURISTIC BOX",
        "unit": "BOX",
        "note": "T-507 2026-09-16 30周年スペシャルセット自動追従(FUTURISTIC BOX)",
        "evidence_note": "2026-09-16 実測: ホムラ カテゴリ130 表示名"
                          " 'FUTURISTIC BOX' で ¥50,000 を確認済み(fetch_other_raw)",
    },
    "PKM-M6A-DECK": {
        "homura_display_name": "30th CELEBRATION プレミアムデッキセット エーフィ・ブラッキー",
        "unit": "BOX",  # canonical_unit=BOX（DECKも旧来BOXレーンに格納。products側の実値に合わせる）
        "note": "T-507 2026-09-16 30周年スペシャルセット自動追従(プレミアムデッキセット)",
        "evidence_note": "2026-09-16 実測: ホムラ カテゴリ130 表示名"
                          " 'プレミアムデッキセット エーフィ・ブラッキー' で ¥14,000 を確認済み(fetch_other_raw)",
    },
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
        if row.get("homura_ref") != cfg["homura_display_name"]:
            _rest(f"products?id=eq.{row['id']}", method="PATCH",
                  body={"homura_ref": cfg["homura_display_name"]})
            print(f"[setup] {sku}: homura_ref を設定 -> {cfg['homura_display_name']!r}")
        else:
            print(f"[setup] {sku}: homura_ref 設定済み")

        existing = _rest("external_price_sources?select=id,status,external_ref"
                          f"&product_id=eq.{row['id']}&source=eq.homura")
        body = {
            "product_id": row["id"], "source": "homura",
            "search_hint": cfg["homura_display_name"],
            "external_ref": cfg["homura_display_name"],
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


def run(apply: bool):
    sb_url, sb_key, _, _ = _conf()
    resolver = HomuraSupabaseResolver(url=sb_url, service_key=sb_key)
    if not resolver.enabled:
        print("[special-follow] Supabase未接続のため中止（SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY未設定）")
        return 1
    fetcher = HomuraFetcher()
    resolved = fetcher.resolve_other_items(GameType.POKEMON, resolver)

    prods = {r["sku"]: r for r in _rest(
        "products?select=id,sku,homura_ref&sku=in.(" + ",".join(TARGETS) + ")")}

    rows = []
    for item in resolved:
        for p in item.get("products", []):
            sku = p.get("sku")
            if sku not in TARGETS:
                continue
            product_id = prods.get(sku, {}).get("id") or p.get("id")
            unit = TARGETS[sku]["unit"]
            raw_price = item.get("price")
            if not isinstance(raw_price, int) or raw_price <= 0:
                print(f"[skip] {sku}: 価格が不正 ({raw_price!r})")
                continue
            proposed = raw_price + MARGIN_BOX
            current = _current_price(product_id, unit)

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

    found_skus = {r["product_id"] for r in rows}
    for sku in TARGETS:
        pid = prods.get(sku, {}).get("id")
        if pid and pid not in found_skus and not any(
                p.get("sku") == sku for item in resolved for p in item.get("products", [])):
            print(f"[not-listed] {sku}: 本日のカテゴリ130にホムラ表示が無い（書込なし・保留のみ）")

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
