"""外部価格ソース台帳(external_price_sources)から master データを構築する。

Gate3（2026-07-27）: config.py のハードコード辞書（POKEMON_MASTER 等）を、
Supabase の external_price_sources 台帳読みに置き換えるためのモジュール。

安全設計:
  - フラグ MASTER_FROM_LEDGER=1 のときだけ有効。既定は無効（config.py の辞書のまま）。
  - 台帳読みに失敗 or 空なら None を返す → 呼び出し側は config 辞書にフォールバック。
    ＝ Supabase 障害でも毎日12時の cron は絶対に止まらない。
  - 返り値は config の get_master_data と同形式の {code: name}（弾レベル＝BOX行代表）。

対象: source='homura' かつ status='active' の行。product が対象ゲームの -BOX SKU のものを
弾レベルの代表として {external_ref(code): search_hint(name)} を作る。
（BOX/NS/CTN は同一 external_ref を共有するため、BOX 行 1つで弾を代表できる）
"""
from __future__ import annotations
import os
import logging
from urllib.parse import quote

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

logger = logging.getLogger(__name__)

# config のゲーム名 → products.category
_CATEGORY = {"pokemon": "Pokemon", "onepiece": "OnePiece", "dragonball": "DragonBall", "yugioh": "YuGiOh"}


def is_enabled() -> bool:
    """MASTER_FROM_LEDGER=1 のときだけ台帳読みを使う。既定 False（config 辞書のまま）。"""
    return os.getenv("MASTER_FROM_LEDGER", "0").strip() in {"1", "true", "yes", "on"}


# cron の環境には SUPABASE_* が無いことがあるため、supabase_writer と同じく
# org 側の .env.local からも認証を読む（env var があればそちら優先）。
_ORG_ROOT = "/Users/nastuki_sever/aigive/org"


def _load_env_file(path: str) -> dict:
    import re
    out = {}
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                m = re.match(r'\s*([A-Z_]+)\s*=\s*"?([^"\n]+)"?', line)
                if m:
                    out[m.group(1)] = m.group(2).strip()
    except FileNotFoundError:
        pass
    return out


def _conf():
    env = _load_env_file(f"{_ORG_ROOT}/apps/pricing/.env.local")
    url = (os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL")
           or env.get("NEXT_PUBLIC_SUPABASE_URL") or "").rstrip("/")
    key = (os.getenv("SUPABASE_SERVICE_ROLE_KEY")
           or env.get("SUPABASE_SERVICE_ROLE_KEY") or env.get("SUPABASE_SERVICE") or "")
    return url, key


def load_master(game: str) -> dict | None:
    """台帳から {code: name} を構築。失敗・空・無効時は None（呼び出し側で config にフォールバック）。"""
    if not is_enabled():
        return None
    if requests is None:
        return None
    url, key = _conf()
    category = _CATEGORY.get(game)
    if not url or not key or not category:
        logger.warning("ledger_master: url/key/category 不足 → フォールバック")
        return None
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    try:
        # active homura の (external_ref, search_hint) を、対象ゲームの -BOX 商品に限定して取得
        # products と external_price_sources を PostgREST の埋め込みで join
        q = (
            "external_price_sources?source=eq.homura&status=eq.active"
            "&select=external_ref,search_hint,products!inner(sku,category)"
            f"&products.category=eq.{quote(category)}"
        )
        out, offset = {}, 0
        while True:
            r = requests.get(
                f"{url}/rest/v1/{q}&limit=1000&offset={offset}",
                headers=headers, timeout=15,
            )
            r.raise_for_status()
            rows = r.json()
            if not isinstance(rows, list) or not rows:
                break
            for row in rows:
                prod = row.get("products") or {}
                sku = prod.get("sku", "")
                if not sku.endswith("-BOX"):
                    continue  # 弾レベルは BOX 行で代表
                code = row.get("external_ref")
                name = row.get("search_hint")
                if code and name:
                    out[code] = name
            if len(rows) < 1000:
                break
            offset += 1000
        if not out:
            logger.warning("ledger_master[%s]: 台帳が空 → フォールバック", game)
            return None
        logger.info("ledger_master[%s]: 台帳から %d 弾を読み込み", game, len(out))
        return out
    except Exception as e:  # 台帳読み失敗 → 絶対に例外を上げず None（cron を止めない）
        logger.warning("ledger_master[%s]: 読込失敗 %s → フォールバック", game, e)
        return None
