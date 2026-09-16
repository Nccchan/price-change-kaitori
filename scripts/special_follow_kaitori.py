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
external_price_sources に kind="manual" の行を追加して対象化する（--setup-sources）。価格マージンは active pricing_rules を読み、固定値を持たない。
`kind="manual"` は collection_supervision.build_manifest() の special 判定に使われ、
標準コレクタ（カテゴリ128/129の巡回）の「期待に反する」扱いにはならない。

## 取得方式

カテゴリ130全件を取得する既存の report-only 経路（HomuraFetcher.fetch_other_raw /
resolve_other_item・src/supabase_resolver.HomuraSupabaseResolver）をそのまま使う。
新しいスクレイピング経路は作らない。解決できたのが TARGETS の SKU のときだけ書込む。

## ガード（BOXと同じ扱い）

  - 値上げ: price_increase_guard と同じ閾値（20%超は保留・Telegram通知）
  - 値下げ: price_decrease_guard と同じ閾値（10%超 or ¥3,000超は保留・Telegram通知）
  - 差分だけ書く: src.supabase_writer._latest_kaitori_prices と同じロジックで
    「前回と同値・かつ今日(JST)書込済み」なら書かない
  - 準備中(kaitori_prep)は書込を止めない（取得側は常に最新を書く。公開可否はflag側の役割。
    既存の write_prices() と同じ方針）
  - 0円/未掲載/名前不一致は書かない（新規公開はなつき承認、フォロー開始後の欠測は保留のみ）
  - 台帳/価格ルール/当日手動決定は実行時に確認。欠測・曖昧は非ゼロ終了。
  - 取得成功と guard/manual 保留は別。dry-run 成功は公開完了を意味しない。

使い方:
  python scripts/special_follow_kaitori.py --setup-sources   # homura_ref/external_price_sources を1回設定
  python scripts/special_follow_kaitori.py                   # dry-run（既定）
  python scripts/special_follow_kaitori.py --apply           # 本番書込（price_history INSERT）

毎時 price-update.sh から呼ぶ想定（BOX/NSと同じ頻度）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# notify_dedupe は org 側（別リポジトリ）の共通部品をそのまま import する
# （_ORG_ROOT は src/supabase_writer.py と同じ決め打ちパス）。
sys.path.insert(0, "/Users/nastuki_sever/aigive/org/scripts/utils")

from dotenv import load_dotenv

load_dotenv()

from src.homura_fetcher import HomuraFetcher
from src.models import GameType
from src.supabase_resolver import HomuraSupabaseResolver, normalize_ref
from src.supabase_writer import (_rest, _conf, _latest_kaitori_prices, _notify,
    _hold_same_day_manual, _drop_unchanged_today, read_back)
from src.price_increase_guard import should_hold as increase_should_hold
from src.price_decrease_guard import should_hold as decrease_should_hold
def notify_if_new(*args):
    # Optional on a developer's machine; dry-run must not depend on the Mac's
    # notification installation. The production notifier is still shared.
    from notify_dedupe import notify_if_new as send
    return send(*args)

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


def active_box_rule():
    """Use the same active pricing_rules row as read-pricing-rule.py, no +200 copy."""
    rules = _rest("pricing_rules?status=eq.active&select=version,rule_json&limit=2")
    if len(rules) != 1:
        raise ValueError("active_rule_not_unique")
    rule = rules[0]
    kaitori = rule["rule_json"].get("kaitori", {})
    if kaitori.get("categories"):
        policy = kaitori["categories"].get("PKM", {})
        if policy.get("enabled") is not True or policy.get("source") != "homura":
            raise ValueError("homura_rule_disabled")
    else:
        policy = kaitori
        if policy.get("source_primary") != "homura":
            raise ValueError("homura_rule_disabled")
    margin = policy.get("box_offset")
    if type(margin) is not int or margin < 0:
        raise ValueError("box_offset_invalid")
    # The wrapper already loaded this rule. Refuse to run half of a cycle on a
    # newer rule instead of silently mixing versions/offsets across collectors.
    if os.getenv("RULE_VERSION") not in (None, str(rule["version"])):
        raise ValueError("rule_version_changed")
    return margin, rule["version"]


def validate_target(product, contracts, candidates):
    """BOX here is the DB price lane, not a claim of a sealed booster box."""
    if not product or product.get("is_active") is not True or product.get("kaitori_hidden") is not False:
        return None, "product_unavailable"
    if product.get("category") != "Pokemon" or not product.get("product_type"):
        return None, "product_identity_invalid"
    unit = product.get("canonical_unit")
    if unit != "BOX":
        return None, "canonical_unit_changed"
    if len(contracts) != 1:
        return None, "source_contract_not_unique"
    contract = contracts[0]
    if (contract.get("source") != "homura" or contract.get("status") != "active"
            or contract.get("kind") != "manual" or contract.get("unit_verified") is not True
            or contract.get("unit") != unit or contract.get("derive_rule") or contract.get("derive_from_sku")):
        return None, "source_contract_invalid"
    ref = normalize_ref(product.get("homura_ref"))
    if not ref or normalize_ref(contract.get("external_ref")) != ref:
        return None, "source_ref_difference"
    if len(candidates) != 1:
        return None, "listing_unconfirmed" if not candidates else "ambiguous_listing"
    candidate = candidates[0]
    if normalize_ref(candidate.get("name")) != ref:
        return None, "listing_identity_difference"
    matches = candidate.get("products", [])
    if len(matches) != 1 or matches[0].get("id") != product["id"]:
        return None, "ambiguous_product_match"
    if type(candidate.get("price")) is not int or candidate["price"] <= 0:
        return None, "invalid_observed_price"
    return candidate, None


def run(apply: bool, report_path=None):
    margin, rule_version = active_box_rule()
    sb_url, sb_key, _, _ = _conf()
    resolver = HomuraSupabaseResolver(url=sb_url, service_key=sb_key)
    if not resolver.enabled:
        raise RuntimeError("database_unavailable")
    prods = {r["sku"]: r for r in _rest(
        "products?select=id,sku,homura_ref,canonical_unit,product_type,category,is_active,kaitori_hidden,kaitori_prep"
        "&sku=in.(" + ",".join(TARGETS) + ")")}
    ids = ",".join(p["id"] for p in prods.values())
    contracts = _rest("external_price_sources?select=*&source=eq.homura&product_id=in.(" + ids + ")") if ids else []
    resolved = HomuraFetcher().resolve_other_items(GameType.POKEMON, resolver)
    now = datetime.now(timezone.utc).isoformat()
    latest = _latest_kaitori_prices([p["id"] for p in prods.values()])
    rows, results = [], []
    for sku in TARGETS:  # approval scope only; identity/rule/unit come from the ledger
        product = prods.get(sku)
        pid = (product or {}).get("id")
        ref = normalize_ref((product or {}).get("homura_ref"))
        candidates = [it for it in resolved if ref and normalize_ref(it.get("name")) == ref]
        candidate, reason = validate_target(product, [c for c in contracts if c["product_id"] == pid], candidates)
        result = {"sku": sku, "product_id": pid, "result": "blocked", "reason_code": reason,
                  "observed_at": now, "source": "homura", "category_id": 130,
                  "external_unit": "SPECIAL_SET", "canonical_unit": (product or {}).get("canonical_unit"),
                  "product_type": (product or {}).get("product_type"), "rule_version": rule_version}
        results.append(result)
        if reason:
            continue
        raw_price = candidate["price"]
        proposed = raw_price + margin
        current = latest.get((pid, product["canonical_unit"]), (None, None))[0]
        result.update(observed_price=raw_price, proposed_price=proposed, current_price=current,
                      external_ref=product["homura_ref"], result="success", reason_code=None)
        row = {"product_id": pid, "kind": "kaitori", "unit": product["canonical_unit"],
               "currency": "JPY", "value": proposed, "source": "price-change-kaitori", "valid_from": now}
        allowed, held = _hold_same_day_manual([row], strict=True)
        if held:
            result.update(write_action="manual_hold", reason_code="same_day_natsuki_decision")
            continue
        hold_up, _, _ = increase_should_hold(current, proposed)
        hold_down, _, _ = decrease_should_hold(current, proposed, product["canonical_unit"])
        if hold_up or hold_down:
            result.update(write_action="guard_hold", reason_code="increase_guard" if hold_up else "decrease_guard")
            msg = f"[special-follow] {sku}: {current} -> {proposed} ({result['reason_code']})"
            print(msg)
            if apply:
                notify_if_new(NOTIFY_STATE, msg, _notify)
            continue
        allowed, skipped = _drop_unchanged_today(allowed)
        result["write_action"] = "unchanged" if skipped else "candidate"
        rows.extend(allowed)
    failures = [r for r in results if r["result"] != "success"]
    # A missing/ambiguous target is no longer a successful zero-row run. It also
    # cannot cause a half-applied initial release of the two approved sets.
    inserted = 0
    if apply and not failures:
        if rows:
            _rest("price_history", method="POST", body=rows)
            inserted = len(rows)
        expected = [{"product_id": r["product_id"], "unit": r["canonical_unit"], "value": r["proposed_price"]}
                    for r in results if r.get("write_action") in {"candidate", "unchanged"}]
        verdict, _ = read_back("pokemon-special", expected, "special-follow-" + now)
        if verdict != "PASS":
            failures.append({"reason_code": "readback_failed"})
    report = {"checked_at": now, "applied": apply and not failures, "inserted": inserted, "rule_version": rule_version,
              "ok": not failures, "results": results, "would_insert": len(rows),
              "publication_checked": False}
    if report_path:
        Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))
    return 2 if failures else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--setup-sources", action="store_true",
                     help="products.homura_ref / external_price_sources を対象2点に設定して終了")
    ap.add_argument("--report-json", help="Local preflight report; does not authorize publication")
    args = ap.parse_args()
    if args.setup_sources:
        setup_sources()
        return 0
    try:
        return run(args.apply, args.report_json)
    except Exception as exc:
        # Do not print arbitrary HTTP responses or environment/credential data.
        report = {"ok": False, "error_type": type(exc).__name__, "publication_checked": False}
        output = json.dumps(report, ensure_ascii=False) + "\n"
        if args.report_json:
            Path(args.report_json).write_text(output)
        print(output)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
