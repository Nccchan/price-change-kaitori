"""
シンソク（shinsoku-tcg.com）からの買取価格取得モジュール（段階1: 取得・JSON出力のみ）。

公開API（認証・署名不要、robots.txt は全許可）を呼び、取得した商品を
candidate 形式（kaitorihakase_fetcher / collection_evidence の candidate 構造を踏襲）
で返す。台帳(external_price_sources)への解決・書き込み・名前マッチングは行わない
（段階2以降の作業）。envelope（attempt_id等の収集エビデンス一式）はホムラ専用の
仕組みのためここでは組み立てない。

GET https://shinsoku-tcg.com/api/items
    ?postal_only=true&sort=name_asc&type={BOX|CARTON}&brand=ポケモン&page={n}&limit=100
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import requests


class ShinsokuFetcher:
    """シンソク公開APIから郵送買取対象商品の candidate 一覧を取得する。"""

    BASE_URL = "https://shinsoku-tcg.com/api/items"
    SOURCE = "shinsoku"  # external_ref の名前空間（ホムラ等の商品番号と衝突させない）
    UNITS = ("BOX", "CARTON")
    PAGE_LIMIT = 100  # API上限（101以上はVALIDATION_ERROR）
    REQUEST_INTERVAL_SEC = 1.0  # 1リクエスト1秒

    def __init__(self, brand: str = "ポケモン") -> None:
        self.brand = brand
        self.session = requests.Session()
        self.session.trust_env = False
        # bot対策回避はしない方針: 偽装せず正直な識別子を名乗る
        self.session.headers["User-Agent"] = (
            "AiGIVE-PriceWatch/1.0 (+contact: cima_6000@icloud.com)"
        )

    def fetch_unit(self, unit: str, fixture_path: Optional[Path] = None) -> List[dict]:
        """1 unit（BOX または CARTON）分の candidate 一覧を取得する。

        fixture_path を渡すと実APIを叩かず、そのJSONファイルを1ページ分の
        レスポンスとして再生する（--dry-run 用）。この場合 has_more の値に関わらず
        1ページで打ち切る（後続ページのフィクスチャを持たないため）。
        """
        if unit not in self.UNITS:
            raise ValueError(f"unsupported unit: {unit}")

        all_candidates: List[dict] = []
        page = 0
        while True:
            if fixture_path is not None:
                raw_text = Path(fixture_path).read_text(encoding="utf-8")
                page_url = f"file://{fixture_path}"
            else:
                params = {
                    "postal_only": "true",
                    "sort": "name_asc",  # 価格順はタイブレークで項目がページ間を移動するため使わない
                    "type": unit,
                    "brand": self.brand,
                    "page": page,
                    "limit": self.PAGE_LIMIT,
                }
                resp = self.session.get(self.BASE_URL, params=params, timeout=30)
                resp.raise_for_status()
                raw_text = resp.text
                page_url = resp.url

            payload = json.loads(raw_text)
            if not payload.get("ok"):
                raise RuntimeError(f"shinsoku API error (unit={unit}, page={page}): {payload}")

            data = payload.get("data", {})
            items = data.get("items", [])
            observed_at = _now_iso()
            response_sha256 = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()

            all_candidates.extend(
                self._candidates_from_items(items, unit, observed_at, page_url, response_sha256)
            )

            has_more = bool(data.get("has_more"))
            if fixture_path is not None or not has_more:
                break
            page += 1
            time.sleep(self.REQUEST_INTERVAL_SEC)  # 1リクエスト1秒

        return self._dedup(all_candidates)

    def fetch_all(self, fixture_path: Optional[Path] = None) -> List[dict]:
        """BOX・CARTON 両方を1リストにまとめて返す（candidate["unit"] で区別。1ファイルにまとめる仕様）。"""
        all_candidates: List[dict] = []
        for i, unit in enumerate(self.UNITS):
            if i > 0 and fixture_path is None:
                time.sleep(self.REQUEST_INTERVAL_SEC)  # unit切り替え時も1リクエスト1秒を維持
            all_candidates.extend(self.fetch_unit(unit, fixture_path=fixture_path))
        return all_candidates

    def _candidates_from_items(
        self,
        items: List[dict],
        unit: str,
        observed_at: str,
        page_url: str,
        response_sha256: str,
    ) -> List[dict]:
        """APIのitems配列からcandidate一覧を作る。

        - is_postal_buy_target が false の行は除外する（唯一の除外条件）。
        - price（postal_purchase_price_s）が null の行も捨てない。「安い(¥0扱い)」と
          「未設定(null)」を区別するため、null のまま保持する（postal_purchase_price_a 等と同じ扱い）。
        - name は raw の name（name_processed ではない）を保持する。
        """
        candidates: List[dict] = []
        for item in items:
            if not item.get("is_postal_buy_target"):
                continue
            candidates.append({
                "source": self.SOURCE,
                "external_ref": item.get("item_id"),
                "name": item.get("name"),
                "unit": item.get("type") or unit,
                "price": item.get("postal_purchase_price_s"),
                "is_full_amount_flag": item.get("is_full_amount_flag"),
                "observed_at": observed_at,
                "page_url": page_url,
                "response_sha256": response_sha256,
            })
        return candidates

    @staticmethod
    def _dedup(candidates: List[dict]) -> List[dict]:
        """external_ref（item_id）で重複除去。先に出現した方を残す。"""
        seen: set = set()
        result: List[dict] = []
        for c in candidates:
            ref = c["external_ref"]
            if ref in seen:
                continue
            seen.add(ref)
            result.append(c)
        return result


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _output_filename(game: str, today: Optional[datetime] = None) -> str:
    today = today or datetime.now()
    # 既存命名規則（例: homura_5_7_pokemon.json）に合わせ、月日はゼロ埋めしない
    return f"shinsoku_{today.month}_{today.day}_{game}.json"


def _default_fixture_path() -> Path:
    return Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "shinsoku_page0.json"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="シンソク買取価格取得（段階1: 取得・JSON出力のみ）",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="実APIを叩かず tests/fixtures/shinsoku_page0.json（type=BOX）を再生する",
    )
    parser.add_argument("--out-dir", default="data", help="出力先ディレクトリ（既定: data/）")
    parser.add_argument("--brand", default="ポケモン", help="brandパラメータ（既定: ポケモン）")
    parser.add_argument("--game", default="pokemon", help="出力ファイル名に使うゲーム識別子（既定: pokemon）")
    args = parser.parse_args()

    fetcher = ShinsokuFetcher(brand=args.brand)

    if args.dry_run:
        fixture_path = _default_fixture_path()
        # フィクスチャは type=BOX の1ページのみ。CARTONは再生対象を持たないため実行しない。
        candidates = fetcher.fetch_unit("BOX", fixture_path=fixture_path)
        print(f"[shinsoku_fetcher] --dry-run: fixture再生のみ（BOXのみ、CARTONは対象外）")
    else:
        candidates = fetcher.fetch_all()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / _output_filename(args.game)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(candidates, f, ensure_ascii=False, indent=2)
        f.write("\n")

    by_unit: Dict[str, int] = {}
    for c in candidates:
        by_unit[c["unit"]] = by_unit.get(c["unit"], 0) + 1
    print(f"[shinsoku_fetcher] {len(candidates)}件 ({by_unit}) → {out_path}")


if __name__ == "__main__":
    main()
