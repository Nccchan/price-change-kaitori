"""
Supabase products.homura_ref を逆引きしてホムラコード → AiGIVE SKU を解決する。

P2-A（2026-06-09 起案）の Step 2 で追加。
main.py からは report-only で呼ばれ、書込先（GAS Webhook → にこにこ買取シート）は変えない。

Phase A 本体（price_history への直接書込）に発展する基盤。

依存: requests（既存 requirements.txt に同梱）/ supabase-py は使わず PostgREST 生叩き
環境変数:
  SUPABASE_URL                  -- 例: https://anwarpbevyrlgspjghym.supabase.co
  SUPABASE_SERVICE_ROLE_KEY     -- service_role の JWT
  HOMURA_RESOLVE_ENABLED        -- "1" で有効、"0" or 未設定で no-op
"""
from __future__ import annotations

import os
import re
import unicodedata
import logging
from typing import Optional
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)


def normalize_ref(s: str) -> str:
    """表示名マッチング用の正規化キー。

    全角/半角統一（NFKC）→ 空白除去 → 記号除去 → 小文字化。
    ホムラの「その他」カテゴリ商品（弾コード無し）を products.homura_ref の
    表示名登録値と突き合わせるために使う。**部分一致ではなく、この正規化後の
    完全一致のみ**を採用する（F-058 幻価格の再発防止・推測マッチ禁止）。
    """
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"[\s　]", "", s)  # 半角/全角空白を除去
    s = re.sub(r"[【】「」『』\[\]（）()・:：,、。※×＊*'’\-‐―—_/／]", "", s)  # 記号除去
    return s.lower()


class HomuraSupabaseResolver:
    """
    ホムラコード（例: "M5", "OP-15", "SV11W"）→ Supabase products.id / sku の解決。

    products.homura_ref に格納されている値（管理シートで人手 or 自動で埋めた値）と
    完全一致するレコードを引く。1コード = 複数SKU（BOX+CTN）の場合があるため、
    返り値は list[dict] にする。
    """

    def __init__(
        self,
        url: Optional[str] = None,
        service_key: Optional[str] = None,
        timeout: float = 10.0,
    ):
        self.url = (url or os.getenv("SUPABASE_URL", "")).rstrip("/")
        self.key = service_key or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
        self.timeout = timeout
        self.enabled = bool(self.url and self.key)
        self._cache: dict[str, list[dict]] = {}
        self._all_refs_cache: Optional[dict[str, list[dict]]] = None
        if not self.enabled:
            logger.warning(
                "HomuraSupabaseResolver: SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY missing — disabled"
            )

    @classmethod
    def from_env(cls) -> "HomuraSupabaseResolver":
        return cls()

    @staticmethod
    def is_enabled_by_flag() -> bool:
        """HOMURA_RESOLVE_ENABLED=1 のときだけ True。デフォルト無効。"""
        return os.getenv("HOMURA_RESOLVE_ENABLED", "0").strip() in {"1", "true", "yes", "on"}

    def resolve(self, homura_code: str) -> list[dict]:
        """
        homura_code に一致する Supabase products を返す。
        該当なしは []。Supabase 未到達時は [] を返し、呼び出し側でフォールバックすること。
        """
        if not self.enabled or not homura_code:
            return []
        if homura_code in self._cache:
            return self._cache[homura_code]
        try:
            r = requests.get(
                f"{self.url}/rest/v1/products",
                params={
                    "select": "id,sku,product_type,homura_ref,is_active",
                    "homura_ref": f"eq.{homura_code}",
                    "is_active": "eq.true",
                },
                headers={
                    "apikey": self.key,
                    "Authorization": f"Bearer {self.key}",
                },
                timeout=self.timeout,
            )
            r.raise_for_status()
            rows = r.json()
        except Exception as e:
            logger.warning("HomuraSupabaseResolver: fetch failed for %s: %s", homura_code, e)
            return []
        self._cache[homura_code] = rows
        return rows

    def resolve_batch(self, homura_codes: list[str]) -> dict[str, list[dict]]:
        """
        複数 homura_code を 1リクエストで解決。`in.(...)` フィルタを使用。
        """
        codes = [c for c in (homura_codes or []) if c]
        if not self.enabled or not codes:
            return {c: [] for c in codes}

        # キャッシュ済はスキップ
        missing = [c for c in codes if c not in self._cache]
        if missing:
            # PostgREST in.() フィルタは "code1,code2" 形式。コードに , が含まれる場合は要 escape。
            # ホムラコードは英数+ハイフンが基本なのでまず安全だが念のため URL encode。
            in_value = ",".join(quote(c, safe="") for c in missing)
            try:
                r = requests.get(
                    f"{self.url}/rest/v1/products",
                    params={
                        "select": "id,sku,product_type,homura_ref,is_active",
                        "homura_ref": f"in.({in_value})",
                        "is_active": "eq.true",
                    },
                    headers={
                        "apikey": self.key,
                        "Authorization": f"Bearer {self.key}",
                    },
                    timeout=self.timeout,
                )
                r.raise_for_status()
                rows = r.json()
            except Exception as e:
                logger.warning("HomuraSupabaseResolver: batch fetch failed: %s", e)
                rows = []

            # コード別に再分配
            grouped: dict[str, list[dict]] = {c: [] for c in missing}
            for row in rows:
                key = row.get("homura_ref")
                if key in grouped:
                    grouped[key].append(row)
            self._cache.update(grouped)

        return {c: self._cache.get(c, []) for c in codes}

    def resolve_all_homura_refs(self) -> dict[str, list[dict]]:
        """
        products.homura_ref が設定済みの全行を取得し、正規化キー（normalize_ref）で
        グルーピングして返す。ホムラ「その他」カテゴリ（弾コード無し商品）を
        表示名で突き合わせるために使う。1セッション内はキャッシュする。

        該当なし/Supabase未到達時は {} を返す（呼び出し側は未突合として扱うこと。
        **部分一致・推測でフォールバックしない**）。
        """
        if not self.enabled:
            return {}
        if self._all_refs_cache is not None:
            return self._all_refs_cache
        try:
            r = requests.get(
                f"{self.url}/rest/v1/products",
                params={
                    "select": "id,sku,product_type,homura_ref,is_active",
                    "homura_ref": "not.is.null",
                    "is_active": "eq.true",
                },
                headers={
                    "apikey": self.key,
                    "Authorization": f"Bearer {self.key}",
                },
                timeout=self.timeout,
            )
            r.raise_for_status()
            rows = r.json()
        except Exception as e:
            logger.warning("HomuraSupabaseResolver: resolve_all_homura_refs failed: %s", e)
            rows = []

        grouped: dict[str, list[dict]] = {}
        for row in rows:
            ref = row.get("homura_ref") or ""
            key = normalize_ref(ref)
            if not key:
                continue
            grouped.setdefault(key, []).append(row)
        self._all_refs_cache = grouped
        return grouped

    def resolve_by_display_name(self, display_name: str) -> list[dict]:
        """
        ホムラの表示名（弾コード無し商品）を正規化して products.homura_ref と突き合わせる。
        完全一致のみ。該当なしは []。
        """
        if not self.enabled or not display_name:
            return []
        key = normalize_ref(display_name)
        if not key:
            return []
        return self.resolve_all_homura_refs().get(key, [])

    def stats(self, homura_codes: list[str]) -> dict:
        """
        解決率の集計。main.py のレポート出力用。
        """
        result = self.resolve_batch(list(set(homura_codes)))
        hit = sum(1 for c, rows in result.items() if rows)
        miss = sum(1 for c, rows in result.items() if not rows)
        return {
            "total_unique_codes": len(result),
            "resolved": hit,
            "unresolved": miss,
            "resolve_rate": (hit / len(result)) if result else 0.0,
            "unresolved_codes": sorted(c for c, rows in result.items() if not rows),
        }
