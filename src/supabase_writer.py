"""price-change-kaitori → Supabase price_history 直接書込（Phase A 二重書込）。

ENABLE_SB_DUAL_WRITE=1 のときだけ main.py から呼ばれる。シート書込（現行運用）とは
独立しており、ここでの失敗はシート書込を絶対に巻き込まない（main.py 側で try/except）。

設計上の約束（docs/legacy-migration/04-phase-a-execution.md）:
- 書込値はシートに書いたものと同一（payload.new_price_1 / new_price_2 をそのまま使う。再計算しない）
- SKU解決は型式コードベースのみ。名前ベースのマッチングは禁止（EB-02固着事件の教訓）
- 未解決SKU・あいまいは静かにスキップせず Telegram 通知
- products lookup は1000行上限を避けてページング
- valid_from は UTC 保存
"""
import os
import re
import json
import urllib.request
import urllib.parse
import unicodedata
from datetime import datetime, timezone

# ---- 認証読込（org 側 .env.local / .env から。環境変数優先）---------------------
_ORG_ROOT = "/Users/nastuki_sever/aigive/org"


def _load_env_file(path):
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
    org = _load_env_file(f"{_ORG_ROOT}/.env")
    url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or env.get("NEXT_PUBLIC_SUPABASE_URL")
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
           or env.get("SUPABASE_SERVICE_ROLE_KEY") or env.get("SUPABASE_SERVICE"))
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN") or org.get("TELEGRAM_BOT_TOKEN")
    tg_chat = os.environ.get("TELEGRAM_CHAT_ID") or org.get("TELEGRAM_CHAT_ID") or "6742029728"
    return url, key, tg_token, tg_chat


def _notify(text):
    _, _, token, chat = _conf()
    if not token:
        print(f"[sb-dual-write] (telegram未設定) {text}")
        return
    try:
        data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
        urllib.request.urlopen(
            urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                                   data=data, method="POST"), timeout=15)
    except Exception as e:
        print(f"[sb-dual-write] telegram通知失敗: {e}")


# ---- Supabase REST -------------------------------------------------------------
def _rest(path, method="GET", body=None):
    url, key, _, _ = _conf()
    if not url or not key:
        raise RuntimeError("Supabase認証情報が無い（NEXT_PUBLIC_SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY）")
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{url}/rest/v1/{path}", data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        return json.loads(raw) if raw else []


def _active_skus():
    """products の active SKU 全件を set で返す（1000行上限を避けてページング）。"""
    out, page, offset = set(), 1000, 0
    while True:
        rows = _rest(f"products?select=sku&is_active=eq.true&limit={page}&offset={offset}")
        for row in rows:
            out.add(row["sku"])
        if len(rows) < page:
            break
        offset += page
    return out


# ---- SKU 解決（型式コードベース）-----------------------------------------------
_PREFIX = {"pokemon": "PKM", "onepiece": "OPE", "dragonball": "DBZ", "yugioh": "YGO"}
# price_2 列の意味はゲーム別: pokemon=NS, それ以外=CARTON
_PRICE2_UNIT = {"pokemon": "NS", "onepiece": "CARTON", "dragonball": "CARTON", "yugioh": "CARTON"}
_UNIT_SUFFIX = {"BOX": "-BOX", "NS": "-NS", "CARTON": "-CTN"}


def _code_to_stem(game, code):
    """ホムラ等の型式コード → products SKU の stem 部分。
    OPE/DBZ/YGO: 'OP-15'→'OP15' / 'EB-02'→'EB02' / 'FB-01'→'FB01'（英字-数字のハイフン除去）
    PKM: そのまま大文字（'SV11B-DX' は保持、'M3-box' の末尾 -BOX は除去）
    """
    c = (code or "").strip().upper()
    if c.endswith("-BOX"):
        c = c[:-4]
    if game in ("onepiece", "dragonball", "yugioh"):
        m = re.match(r"^([A-Z]+)-(\d+)$", c)
        if m:
            c = m.group(1) + m.group(2)
    return c


def _resolve_sku(game, code, unit, active):
    """(game, code, unit) → products SKU。active に存在しなければ None。"""
    stem = _code_to_stem(game, code)
    if not stem or not stem.isascii() or not re.match(r"^[A-Z0-9-]+$", stem):
        return None
    sku = f"{_PREFIX[game]}-{stem}{_UNIT_SUFFIX[unit]}"
    return sku if sku in active else None


def _sku_to_id(active_ids, sku):
    return active_ids.get(sku)


def _normalize_name(value):
    value = unicodedata.normalize("NFKC", value or "").lower().strip()
    value = re.sub(r"^[「【〖].*?[」】〗]\s*", "", value)
    value = re.sub(r"\s*※.*$", "", value)
    return re.sub(r"[\s　()（）・]+", "", value)


def _resolve_by_name(name, unit, product_rows):
    """正規化名が一意な場合だけSKUを返す。0件/複数件は fail closed。"""
    target = _normalize_name(name)
    if not target:
        return None, "missing-name"
    suffix = _UNIT_SUFFIX[unit]
    matches = [
        row["sku"] for row in product_rows
        if row.get("sku", "").endswith(suffix)
        and _normalize_name(row.get("name_jp")) == target
    ]
    if len(matches) == 1:
        return matches[0], "name"
    return None, "ambiguous-name" if len(matches) > 1 else "unresolved-name"


def _resolve_product(game, code, name, unit, active, product_rows):
    sku = _resolve_sku(game, code, unit, active)
    if sku:
        return sku, "code"
    return _resolve_by_name(name, unit, product_rows)


# ---- メイン書込 ----------------------------------------------------------------
def write_prices(game, payloads, dry_run=False):
    """payloads: main.py の UpdatePayload リスト（.code / .new_price_1(BOX) / .new_price_2 / .is_new）。
    price_2 列はゲーム別に NS(pokemon) または CARTON(その他)。
    """
    from src.price_guard import guard_payloads
    payloads, violations = guard_payloads(game, payloads)
    for v in violations:
        print(f"[price-guard] rejected {v.code}: BOX={v.box} NS={v.ns}")

    if game not in _PREFIX:
        print(f"[sb-dual-write] 未対応ゲーム: {game}")
        return

    # products: sku→id（active）
    id_rows, product_rows, page, offset = {}, [], 1000, 0
    while True:
        rows = _rest(f"products?select=id,sku,name_jp,homura_ref&is_active=eq.true&limit={page}&offset={offset}")
        for row in rows:
            id_rows[row["sku"]] = row["id"]
            product_rows.append(row)
        if len(rows) < page:
            break
        offset += page
    active = set(id_rows.keys())

    p2_unit = _PRICE2_UNIT[game]
    rows, unresolved = [], []
    now = datetime.now(timezone.utc).isoformat()

    for p in payloads:
        if getattr(p, "is_new", False):
            continue  # 新規行はSupabaseに未登録の可能性 → 直結書込の対象外
        code = getattr(p, "code", "") or ""
        # BOX (price_1)
        if getattr(p, "new_price_1", None) is not None:
            sku, method = _resolve_product(game, code, getattr(p, "name", ""), "BOX", active, product_rows)
            if sku:
                rows.append({"product_id": id_rows[sku], "kind": "kaitori", "unit": "BOX",
                             "currency": "JPY", "value": int(p.new_price_1),
                             "source": "price-change-kaitori", "valid_from": now})
            else:
                unresolved.append(f"{code or getattr(p, 'name', '')}/BOX:{method}")
        # price_2 (NS or CARTON)
        if getattr(p, "new_price_2", None) is not None:
            sku, method = _resolve_product(game, code, getattr(p, "name", ""), p2_unit, active, product_rows)
            if sku:
                rows.append({"product_id": id_rows[sku], "kind": "kaitori", "unit": p2_unit,
                             "currency": "JPY", "value": int(p.new_price_2),
                             "source": "price-change-kaitori", "valid_from": now})
            else:
                unresolved.append(f"{code or getattr(p, 'name', '')}/{p2_unit}:{method}")

    if dry_run:
        print(f"[sb-dual-write DRY-RUN] {game}: insert予定 {len(rows)} 行 / 未解決 {len(unresolved)}件")
        if unresolved:
            print(f"  未解決: {unresolved}")
        return {"would_insert": len(rows), "unresolved": unresolved}

    for i in range(0, len(rows), 200):  # fail-soft: チャンク投入
        _rest("price_history", method="POST", body=rows[i:i + 200])

    if unresolved:
        _notify("⚠️ [SB dual-write] price_history 未解決SKU "
                f"{len(unresolved)}件（{game}）: {unresolved[:15]}")
    print(f"[sb-dual-write] {game}: {len(rows)}行INSERT / 未解決{len(unresolved)}件")
    return {"inserted": len(rows), "unresolved": unresolved}
