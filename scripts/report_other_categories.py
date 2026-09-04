#!/usr/bin/env python3
"""
ホムラ「その他」カテゴリ（弾コード無し商品）の取得・突合レポート（report-only / T-348）。

- スプレッドシート・Supabase・price_history への書込は一切行わない（読み取り専用）。
- 突合は 1) 型式コード完全一致 → 2) 表示名の正規化完全一致 の2段のみ。
  部分一致・推測マッチは行わない（F-058 幻価格の再発防止）。
- SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY が未設定の場合は Supabase 照合をスキップし、
  --fallback-map で渡した既知の対応表（なつき作成分）でのみ突合結果を示す。

使用例:
  python scripts/report_other_categories.py
  python scripts/report_other_categories.py --game onepiece
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv

load_dotenv()

from src.homura_fetcher import HomuraFetcher
from src.models import GameType
from src.supabase_resolver import HomuraSupabaseResolver, normalize_ref

# 2026-09-05 なつき作成の対応表（T-348 note）。
# Supabase の products.homura_ref がまだ空の7件を含む。
# Supabase に接続できない環境（このdry-runを含む）でも突合結果を示せるよう、
# 既知の対応をローカルにも保持しておく。**実運用では resolver 経由の Supabase 参照を優先し、
# ここへの追記だけで済ませない**（真実の源は products.homura_ref）。
KNOWN_MAPPING = {
    # ホムラ表示名（正規化前の生の名前） -> (AiGIVE SKU, Supabase homura_ref 登録状況)
    "連撃マスター": ("PKM-S5R-BOX", "既存(S5R)"),
    "一撃マスター": ("PKM-S5I-BOX", "既存(S5I)"),
    "メガエルレイドSP": ("PKM-M3-SP-BOX", "既存(M3-SP)"),
    "DUAL EVOLUTION BOX": ("DBZ-FB09-BOX", "既存(FB-09)"),
    "STORY BOOSTER 01 BOX": ("DBZ-ST01-BOX", "既存(ST01)"),
    "プレイマットWCI": ("OPE-PM-WCI", "空(要登録)"),
    "PSAミホーク": ("OPE-PR-PSA-MH", "空(要登録)"),
    "ROUND1パック": ("OPE-PR-R1-PACK", "空(要登録)"),
    "OPD2024": ("OPE-OPD-2024", "空(要登録)"),
    "BASE SHOP vol.1": ("OPE-BASE-01", "空(要登録)"),
    "熊本スペシャル": ("OPE-KUMA", "空(要登録)"),
    "ルフィプロモP-159": ("OPE-PR-WSJ33-LUFFY", "空(要登録)"),
    "PCC6 vol.1": (None, "当社マスタ無し"),
}
KNOWN_MAPPING_BY_KEY = {normalize_ref(k): (k, v) for k, v in KNOWN_MAPPING.items()}


def fallback_lookup(name: str):
    """Supabase未接続時のフォールバック。既知の対応表のみを正規化キーで完全一致させる。
    部分一致は行わない。"""
    key = normalize_ref(name)
    return KNOWN_MAPPING_BY_KEY.get(key)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--game",
        choices=[g.value for g in GameType],
        default=None,
        help="単一ゲームのみ確認する（省略時は全ゲーム）",
    )
    args = parser.parse_args()

    fetcher = HomuraFetcher()
    resolver = HomuraSupabaseResolver.from_env()

    games = [GameType(args.game)] if args.game else list(GameType)

    if resolver.enabled:
        print("[INFO] Supabase 接続あり。products.homura_ref を正規化して照合します。")
    else:
        print(
            "[INFO] Supabase 未接続（SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 未設定）。\n"
            "       このワークツリーの .env には元々この鍵が入っていないため（本番はGitHub Actions側で保持）、\n"
            "       T-348 note の既知対応表のみでフォールバック照合します。実運用では必ず Supabase 参照を使うこと。"
        )

    grand_total = 0
    grand_matched = 0
    grand_unmatched = []

    for game in games:
        cat_ids = fetcher.OTHER_CATEGORY_IDS.get(game, [])
        print(f"\n=== {game.value} ===")
        if not cat_ids:
            print("  対象カテゴリなし（サイト上に専用の「その他」カテゴリを確認できていない）")
            continue

        try:
            items = fetcher.fetch_other_raw(game)
        except Exception as e:
            print(f"  [ERROR] 取得失敗: {e}", file=sys.stderr)
            continue

        total = len(items)
        matched_rows = []
        unmatched_rows = []

        for it in items:
            result = fetcher.resolve_other_item(it, resolver)
            if not result["matched"] and not resolver.enabled:
                fb = fallback_lookup(it["name"])
                if fb:
                    raw_key, (sku, ref_status) = fb
                    result = {
                        **result,
                        "matched": sku is not None,
                        "method": "fallback_map",
                        "matched_code": sku,
                        "ref_status": ref_status,
                    }

            if result["matched"]:
                matched_rows.append((it["name"], it["price"], result.get("matched_code") or result.get("method")))
            else:
                ref_status = result.get("ref_status")
                unmatched_rows.append((it["name"], it["price"], ref_status))

        print(f"  取得行数: {total} 件（カテゴリID: {cat_ids}）")
        print(f"  突合○: {len(matched_rows)} 件")
        for name, price, via in matched_rows:
            print(f"    - {name} / ¥{price:,} → {via}")
        print(f"  突合×（未突合）: {len(unmatched_rows)} 件")
        for name, price, ref_status in unmatched_rows:
            extra = f"（{ref_status}）" if ref_status else ""
            print(f"    - {name} / ¥{price:,}{extra}")

        grand_total += total
        grand_matched += len(matched_rows)
        grand_unmatched.extend(f"{game.value}:{name}" for name, _, _ in unmatched_rows)

    print("\n=== 合計 ===")
    print(f"  取得行数: {grand_total} 件")
    print(f"  突合○: {grand_matched} 件")
    print(f"  未突合: {len(grand_unmatched)} 件")
    if grand_unmatched:
        for name in grand_unmatched:
            print(f"    - {name}")


if __name__ == "__main__":
    main()
