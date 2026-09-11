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
from datetime import datetime, timezone, timedelta, time as dt_time

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


MANUAL_SOURCE = "natsuki-decision"


def _hold_same_day_manual(rows):
    """JSTの同日中になつき決裁値がある (product_id, unit) は、再計算の書込を見送る。

    「後から書いた方が勝つ」設計自体は維持する（翌日のホムラ取得では通常ルールに戻る）。
    ここで防ぐのは、POPや投稿に出した価格が"同じ日のうちに"黙って書き換わることだけ。
    戻り値: (書き込むrows, 維持した内訳)
    """
    if not rows:
        return rows, []
    jst_today = datetime.now(timezone(timedelta(hours=9))).date()
    since = datetime.combine(jst_today, dt_time(0, 0),
                             tzinfo=timezone(timedelta(hours=9))).astimezone(timezone.utc).isoformat()
    ids = sorted({r["product_id"] for r in rows})
    manual = {}
    try:
        for i in range(0, len(ids), 50):
            chunk = ",".join(ids[i:i + 50])
            got = _rest(f"price_history?kind=eq.kaitori&source=eq.{MANUAL_SOURCE}"
                        f"&product_id=in.({chunk})&valid_from=gte.{urllib.parse.quote(since)}"
                        f"&select=product_id,unit,value,valid_from&order=valid_from.desc")
            for row in got:
                manual.setdefault((row["product_id"], row["unit"]), int(row["value"]))
    except Exception as e:
        # 照会に失敗したら握りつぶさず、従来どおり書く（＝安全側は"止めない"）。
        print(f"[manual-hold] 決裁値の照会に失敗したため通常書込を継続: {e}")
        return rows, []

    keep, held = [], []
    for r in rows:
        k = (r["product_id"], r["unit"])
        if k in manual and manual[k] != int(r["value"]):
            held.append({"product_id": r["product_id"], "unit": r["unit"],
                         "manual": manual[k], "calc": int(r["value"])})
        else:
            keep.append(r)
    return keep, held


def _quality_log(run_id, target, rule_id, verdict, expected=None, observed=None,
                 evidence=None, note=None):
    """検査結果を quality_runs（追記専用）に残す。ここの失敗は本処理を止めない。"""
    try:
        _rest("quality_runs", method="POST", body=[{
            "run_id": run_id, "target": target, "rule_id": rule_id, "verdict": verdict,
            "severity": {"PASS": "info", "WARN": "minor",
                         "FAIL": "major", "BLOCK": "critical"}.get(verdict),
            "expected": expected, "observed": observed, "evidence": evidence,
            "rules_version": os.getenv("RULE_VERSION"), "note": note,
        }])
    except Exception as e:
        print(f"[quality] quality_runs 記録失敗（本処理は継続）: {e}")


def read_back(game, rows, run_id):
    """PRICE-010: 書いたつもりの値と、price_history から読み直した実値を突合する。

    F-057の教訓 =「APIが成功した」を完了の根拠にしない。反映先の実データで確認する。
    rows は write_prices が INSERT した dict のリスト（product_id / unit / value）。
    戻り値: (verdict, mismatches)
    """
    target = f"kaitori:{game}"
    if not rows:
        _quality_log(run_id, target, "PRICE-010", "PASS", note="書込対象なし")
        return "PASS", []

    expected = {}  # (product_id, unit) -> 意図した値
    for r in rows:
        expected[(r["product_id"], r["unit"])] = int(r["value"])

    # 実データを読み直す（対象product_idだけを、分割して取得）
    ids = sorted({pid for pid, _ in expected})
    latest = {}
    for i in range(0, len(ids), 50):
        chunk = ",".join(ids[i:i + 50])
        got = _rest(f"price_history?kind=eq.kaitori&product_id=in.({chunk})"
                    f"&select=id,product_id,unit,value,valid_from"
                    f"&order=valid_from.desc&limit=5000")
        for row in got:
            k = (row["product_id"], row["unit"])
            if k not in latest:  # order=desc なので最初に来たものが最新
                latest[k] = row

    mismatches = []
    for k, want in expected.items():
        got = latest.get(k)
        if got is None:
            mismatches.append({"product_id": k[0], "unit": k[1], "expected": want,
                               "observed": None, "why": "反映が見つからない"})
        elif int(got["value"]) != want:
            mismatches.append({"product_id": k[0], "unit": k[1], "expected": want,
                               "observed": int(got["value"]), "why": "値が一致しない"})

    verdict = "PASS" if not mismatches else "FAIL"
    _quality_log(run_id, target, "PRICE-010", verdict,
                 expected={"count": len(expected)},
                 observed={"matched": len(expected) - len(mismatches),
                           "mismatched": len(mismatches)},
                 evidence={"sample_price_history_ids":
                           [v["id"] for v in list(latest.values())[:5]]},
                 note=None if not mismatches else json.dumps(mismatches[:20], ensure_ascii=False))

    if mismatches:
        print(f"[quality] ❌ PRICE-010 FAIL ({game}): {len(mismatches)}件が一致しません")
        for m in mismatches[:10]:
            print(f"  {m['unit']} expected={m['expected']} observed={m['observed']} ({m['why']})")
        _notify(f"❌ 買取Read-back失敗 ({game}): {len(mismatches)}件が意図した値になっていません\n"
                f"書込{len(expected)}件中 一致{len(expected) - len(mismatches)}件。"
                f"詳細は quality_runs run_id={run_id}")
    else:
        print(f"[quality] ✅ PRICE-010 PASS ({game}): {len(expected)}件すべて意図どおり反映")
    return verdict, mismatches


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
    # products.name_jp の単位注記は、unit/SKU suffix で既に絞るため除去可能。
    value = re.sub(r"\s*[\(（](?:シュリンクなし|シュリンク無し|カートン|パック)[\)）]\s*$", "", value)
    return re.sub(r"[\s　()（）・]+", "", value)


def _resolve_by_name(name, unit, product_rows, game=None):
    """正規化名が一意な場合だけSKUを返す。0件/複数件は fail closed。

    ⚠️ game を必ず渡すこと。**ゲームをまたいだ名前一致を許すと事故る**（2026-08-17/18・F-122）:
       ポケモンの取得結果に紛れ込んだ「決戦の刻」が名前一致で OPE-OP16-BOX に解決され、
       ¥200 が2日連続で書き込まれた。買取だけでなく販売価格まで ¥300/¥400 に落ちて公開された。
       ポケモンの run は PKM- のSKUしか書いてはいけない。
       current が ¥0（新規扱い）だったため値下げガードもすり抜けている＝ガードを足すより
       そもそも他ゲームのSKUに触らせないのが正しい。
    """
    target = _normalize_name(name)
    if not target:
        return None, "missing-name"
    suffix = _UNIT_SUFFIX[unit]
    prefix = f"{_PREFIX[game]}-" if game in _PREFIX else None
    matches = [
        row["sku"] for row in product_rows
        if row.get("sku", "").endswith(suffix)
        and (prefix is None or row.get("sku", "").startswith(prefix))
        and _normalize_name(row.get("name_jp")) == target
    ]
    if len(matches) == 1:
        return matches[0], "name"
    return None, "ambiguous-name" if len(matches) > 1 else "unresolved-name"


def _resolve_product(game, code, name, unit, active, product_rows):
    sku = _resolve_sku(game, code, unit, active)
    if sku:
        return sku, "code"
    return _resolve_by_name(name, unit, product_rows, game)


# ---- メイン書込 ----------------------------------------------------------------
def write_prices(
    game, payloads, dry_run=False, *, proposal=None, approval=None,
    expected_proposal_hash=None,
):
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

    # 2026-07-24に暫定で入れた「ワンピは承認済みproposalが無いと本番書込できない」ガード。
    # cronはproposalを渡さないので、7/28に凍結解除した後もSupabaseへ一度も書けず、
    # ワンピの買取が9日間止まっていた（F-060）。しかも --yes 欠落で手前で止まっていたため
    # このガードにすら到達しておらず、誰も気づかなかった。
    # 現在は同じ役割を次の仕組みが担っている:
    #   price_guard(NS≤BOX等) / price_increase_guard(5%超は保留) / price_decrease_guard(大幅値下げは保留)
    #   / PRICE-010 Read-back(反映後に実データで確認) / kaitori_prep(公開可否の分離)
    # よって既定では要求しない。復活させる場合のみ KAITORI_OPE_REQUIRE_PROPOSAL=1。
    if (game == "onepiece" and not dry_run
            and os.getenv("KAITORI_OPE_REQUIRE_PROPOSAL") == "1"):
        if not proposal or not approval or not expected_proposal_hash:
            raise RuntimeError("One Piece本番書込にはproposal・approval・expected hashが必須です")
        from src.pricing_proposal import verify_approval
        verify_approval(proposal, approval, expected_proposal_hash)
        payload_values = set()
        for payload in payloads:
            code = (getattr(payload, "code", "") or "").strip().upper()
            if getattr(payload, "new_price_1", None) is not None:
                payload_values.add((code, "BOX", int(payload.new_price_1)))
            if getattr(payload, "new_price_2", None) is not None:
                payload_values.add((code, _PRICE2_UNIT[game], int(payload.new_price_2)))
        proposal_values = {
            (item["code"].strip().upper(), item["unit"], int(item["proposed"]))
            for item in proposal.get("items", [])
        }
        if payload_values != proposal_values:
            raise RuntimeError(
                "apply payloadと承認済みproposalが一致しません "
                f"(payload={len(payload_values)}, proposal={len(proposal_values)})"
            )

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

    # 旧「準備中(0以下)ガード」は撤廃（2026-08-04）。買取「準備中」の公開停止は Web 側の
    # products.kaitori_prep フラグ＋ v_public_kaitori の除外で行う（"価格取得"と"公開可否"を分離）。
    # 取得側は常に最新買取を書き、公開の可否はフラグ側に委ねる（準備中でも内部に価格は入り続ける）。
    # ※適用順序: 先に Web の migration(kaitori_prep列＋view) を本番適用してから本撤廃を入れること。
    p2_unit = _PRICE2_UNIT[game]
    rows, unresolved, prep_skipped = [], [], 0
    now = datetime.now(timezone.utc).isoformat()

    for p in payloads:
        # 新弾(is_new)でも products に登録済みなら書き込む（F-054 修正 2026-08-03）。
        # 旧実装は is_new を一律 continue していたため、新弾の買取が Supabase に入らず
        # /pricing/kohyo に永遠に出なかった（Homura取得→シートには書くが公開ビューに未反映）。
        # 未登録の弾は _resolve_product が code/name で解決できず unresolved に回るので誤書込は起きない。
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

    # 当日のなつき決裁値を、同じ日の再計算で消さない（2026-08-04 F-059）。
    # なつきの方針: 手動決裁は"その日限り"。翌日のホムラ取得では通常ルール(+マージン)に戻ってよい。
    # 困るのは「同じ日のうちに、POPや投稿に出した価格が黙って書き換わる」こと。
    # よって JST の同日中に source='natsuki-decision' がある (product_id, unit) だけ書込をスキップする。
    rows, held = _hold_same_day_manual(rows)
    from src.collection_evidence import observe_manual_holds
    observe_manual_holds(game, held)
    if held:
        print(f"[manual-hold] 本日のなつき決裁値を維持（再計算をスキップ）: {len(held)}件")
        for h in held[:10]:
            print(f"  {h['unit']} 決裁¥{h['manual']:,} を維持（計算値¥{h['calc']:,} は不採用）")
        _quality_log(f"kaitori-{game}-{now}", f"kaitori:{game}", "PRICE-012", "WARN",
                     expected={"manual": [h["manual"] for h in held[:20]]},
                     observed={"calculated": [h["calc"] for h in held[:20]]},
                     note=f"当日のなつき決裁値を維持し再計算を見送り {len(held)}件（翌日は通常ルールに戻る）")

    if dry_run:
        print(f"[sb-dual-write DRY-RUN] {game}: insert予定 {len(rows)} 行 / 未解決 {len(unresolved)}件 / 準備中skip {prep_skipped}件")
        if unresolved:
            print(f"  未解決: {unresolved}")
        return {"would_insert": len(rows), "unresolved": unresolved}

    for i in range(0, len(rows), 200):  # fail-soft: チャンク投入
        _rest("price_history", method="POST", body=rows[i:i + 200])

    # Quality Phase 1 / PRICE-010: 書いた直後に読み直して、意図した値になっているか確認する。
    # 「INSERTが200を返した」では完了と見なさない（F-057）。
    run_id = f"kaitori-{game}-{now}"
    try:
        read_back(game, rows, run_id)
    except Exception as e:
        print(f"[quality] Read-back実行失敗（書込自体は完了済）: {e}")
        _quality_log(run_id, f"kaitori:{game}", "PRICE-010", "WARN",
                     note=f"Read-back実行不可: {e}")

    if unresolved:
        _notify("⚠️ [SB dual-write] price_history 未解決SKU "
                f"{len(unresolved)}件（{game}）: {unresolved[:15]}")
    print(f"[sb-dual-write] {game}: {len(rows)}行INSERT / 未解決{len(unresolved)}件 / 準備中skip{prep_skipped}件")
    return {"inserted": len(rows), "unresolved": unresolved}
