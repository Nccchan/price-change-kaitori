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
import socket
import time
import urllib.error
import urllib.request
import urllib.parse
import unicodedata
from datetime import datetime, timezone, timedelta

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
def _is_timeout_error(exc):
    """socket.timeout（read timeout）と urllib.error.URLError(reason=timeout)（connect
    timeout）の両方を1つの判定にまとめる。timeout以外（HTTPError等）はここでFalse。"""
    if isinstance(exc, socket.timeout):
        return True
    if isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, socket.timeout):
        return True
    return False


def _rest(path, method="GET", body=None, retries=1):
    """F-232（2026-09-16）: 30秒タイムアウトでリトライが無く、毎時実行で偶発的な
    タイムアウト1回がそのままdual-write失敗（DBZ分の書込漏れ）になっていた。
    タイムアウトのときだけ（HTTPError等の応答があるエラーはリトライしない）短い待ち後に
    最大 retries 回再試行する。GET/POSTいずれも冪等でない可能性はあるが、Supabaseは
    宛先未達（タイムアウト＝応答が返る前に切れた）を対象にするため、書込が実際に
    サーバ側で成立していた場合は price_history に重複行が増えるだけ（追記専用ログなので
    実害は小さい・読み出しは valid_from 最新優先のため影響しない）。何も書けないまま
    毎時1回分が欠測するリスクの方が大きいと判断。
    """
    url, key, _, _ = _conf()
    if not url or not key:
        raise RuntimeError("Supabase認証情報が無い（NEXT_PUBLIC_SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY）")
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    data = json.dumps(body).encode() if body is not None else None
    attempt = 0
    while True:
        req = urllib.request.Request(f"{url}/rest/v1/{path}", data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return json.loads(raw) if raw else []
        except Exception as e:
            if attempt < retries and _is_timeout_error(e):
                attempt += 1
                time.sleep(2)
                continue
            raise


MANUAL_SOURCE = "natsuki-decision"
JST = timezone(timedelta(hours=9))


def _jst_date(value):
    """price_history.valid_from（ISO文字列）をJSTの日付(date)に変換。壊れていればNone。"""
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(JST).date()
    except (ValueError, TypeError):
        return None


def _latest_kaitori_prices(product_ids):
    """(product_id, unit) → (最新value, 最新valid_from) を返す。kind=kaitori・source不問。

    T-508（2026-09-16 毎時化）: 差分だけ書くための下敷き。supabase_reader.py の
    「現在価格」取得と同じクエリ・チャンク方式（50件ずつ・valid_from降順・先頭を採用）を流用。
    """
    latest = {}
    ids = [i for i in dict.fromkeys(product_ids) if i]  # 順序維持で重複除去
    for i in range(0, len(ids), 50):
        chunk = ",".join(ids[i:i + 50])
        for r in _rest(f"price_history?kind=eq.kaitori&product_id=in.({chunk})"
                       f"&select=product_id,unit,value,valid_from"
                       f"&order=valid_from.desc&limit=10000"):
            k = (r["product_id"], r["unit"])
            if k not in latest:  # desc なので最初が最新
                latest[k] = (int(r["value"]), r["valid_from"])
    return latest


def _drop_unchanged_today(rows):
    """差分だけ書く（T-508）: 前回のprice_history最新値と同じ、かつ既にJSTの今日
    書き込み済みなら書かない。値が変わった行・今日まだ一度も書いていない行は必ず通す
    （kaitori_stale_to_prep.py 等の「48h以内にprice_historyの行があるか」判定は
    “その日1回は必ず書く”を前提にしているため、これを崩さない）。
    """
    if not rows:
        return rows, 0
    latest = _latest_kaitori_prices({r["product_id"] for r in rows})
    today = datetime.now(JST).date()
    kept, skipped = [], 0
    for r in rows:
        prev = latest.get((r["product_id"], r["unit"]))
        if prev is not None and prev[0] == r["value"] and _jst_date(prev[1]) == today:
            skipped += 1
            continue
        kept.append(r)
    return kept, skipped


def _parse_iso(value):
    """ISO8601文字列（Zサフィックス含む）をtz-awareなdatetimeへ。壊れていればNone。"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _latest_manual_or_natsuki_rows(product_ids):
    """(product_id, unit) → 直近の manual%/natsuki% kaitori 行（value/source/valid_from/expires_at）。

    T-567（2026-09-20）: 同日フィルタを外し、対象(product_id,unit)の最新1行を
    dateに関係なく取得する。natsuki%行が前日以前でも expires_at で判定できるようにする。
    """
    out = {}
    ids = [i for i in dict.fromkeys(product_ids) if i]  # 順序維持で重複除去
    for i in range(0, len(ids), 50):
        chunk = ",".join(ids[i:i + 50])
        got = _rest(
            "price_history?kind=eq.kaitori"
            "&or=(source.like.natsuki%25,source.like.manual%25)"
            f"&product_id=in.({chunk})"
            "&select=product_id,unit,value,valid_from,source,expires_at"
            "&order=valid_from.desc")
        for row in got:
            k = (row["product_id"], row["unit"])
            if k not in out:  # desc なので最初が最新
                out[k] = row
    return out


def _hold_same_day_manual(rows):
    """対象 (product_id, unit) の最新 kaitori 行が、まだ有効期限内の
    なつき決裁値(source like 'natsuki%')／一時的な手動上書き(source like 'manual%')
    なら、再計算の書込を見送る。

    2026-08-04 F-059: 元々は「同日中は触らない」だけの保護だった。
    2026-09-20 T-567 事故: 9/19 15:08 の natsuki-decision（PKM-M1S-BOX ¥7,100・
    expires_at=2026-09-25）が、日付が変わった 9/20 00:00 の毎時書き手に
    ¥6,500（ホムラ+200）で上書きされた。「同日だけ」保護は翌日00:00に切れるため、
    数日先まで有効な決裁値を守れていなかった。
    DB側トリガー（20260913000003_kaitori_manual_scope.sql）と同じ規則をアプリ側にも入れる:
      - natsuki%: expires_at が NULL または未来なら保護（明示指定が無ければ永続）
      - manual%:  expires_at が無ければ valid_from+24h を既定として保護
    expires_at を過ぎていれば保護は外れ、通常のルール計算へ戻る（＝この関数は書込を止めない）。
    「後から書いた方が勝つ」設計自体は維持する。
    戻り値: (書き込むrows, 維持した内訳)
    """
    if not rows:
        return rows, []
    ids = sorted({r["product_id"] for r in rows})
    try:
        latest = _latest_manual_or_natsuki_rows(ids)
    except Exception as e:
        # 照会に失敗したら握りつぶさず、従来どおり書く（＝安全側は"止めない"）。
        print(f"[manual-hold] 決裁値の照会に失敗したため通常書込を継続: {e}")
        return rows, []

    now = datetime.now(timezone.utc)
    keep, held = [], []
    for r in rows:
        k = (r["product_id"], r["unit"])
        latest_row = latest.get(k)
        active = False
        expires = None
        if latest_row:
            source = str(latest_row.get("source") or "")
            expires = _parse_iso(latest_row.get("expires_at"))
            if source.startswith("natsuki"):
                # 明示指定が無ければ永続（DB側トリガーのデフォルトと同じ）。
                active = expires is None or expires > now
            elif source.startswith("manual"):
                if expires is None:
                    valid_from = _parse_iso(latest_row.get("valid_from"))
                    expires = (valid_from + timedelta(hours=24)) if valid_from else None
                active = expires is None or expires > now
        if active and int(latest_row["value"]) != int(r["value"]):
            held.append({"product_id": r["product_id"], "unit": r["unit"],
                         "manual": int(latest_row["value"]), "calc": int(r["value"]),
                         "expires_at": latest_row.get("expires_at")})
            expires_label = expires.date().isoformat() if expires is not None else "なし・永続"
            label = "natsuki 決定値" if str(latest_row.get("source") or "").startswith("natsuki") \
                else "manual 上書き"
            print(f"[manual-hold] {label}を保護（expires {expires_label}）: "
                  f"{r['product_id']} {r['unit']}")
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
    #   price_guard(NS≤BOX等) / price_increase_guard(50%以上の急騰は保留) / price_decrease_guard(50%以上の急落は保留)
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

    # なつき決裁値(natsuki%)・一時的な手動上書き(manual%)を、再計算で消さない
    # （2026-08-04 F-059 / 2026-09-20 T-567 で expires_at 判定に拡張）。
    # natsuki%は明示指定が無ければ永続、manual%は既定24hで expires_at を過ぎたら
    # 通常ルール(+マージン)に戻ってよい。困るのは「有効期限内に、POPや投稿に出した
    # 価格が黙って書き換わる」こと。
    rows, held = _hold_same_day_manual(rows)
    from src.collection_evidence import observe_manual_holds
    observe_manual_holds(game, held)
    if held:
        print(f"[manual-hold] 有効期限内の決裁値/手動上書きを維持（再計算をスキップ）: {len(held)}件")
        for h in held[:10]:
            print(f"  {h['unit']} 決裁¥{h['manual']:,} を維持（計算値¥{h['calc']:,} は不採用・"
                  f"expires={h.get('expires_at') or '永続'}）")
        _quality_log(f"kaitori-{game}-{now}", f"kaitori:{game}", "PRICE-012", "WARN",
                     expected={"manual": [h["manual"] for h in held[:20]]},
                     observed={"calculated": [h["calc"] for h in held[:20]]},
                     note=f"有効期限内の決裁値/手動上書きを維持し再計算を見送り {len(held)}件"
                          "（期限切れは通常ルールに戻る）")

    # T-508（2026-09-16 毎時化）: 差分だけ書く。値が前回と同じ、かつ今日(JST)既に
    # 書込済みならスキップ。値上げ5%(実装上は20%上限)ガード・準備中保護・48hルールは
    # 上のガード群で既に確定済みの行に対してのみ判定するので、ここでの間引きは影響しない。
    rows, unchanged_skipped = _drop_unchanged_today(rows)

    if dry_run:
        print(f"[sb-dual-write DRY-RUN] {game}: insert予定 {len(rows)} 行 / "
              f"未解決 {len(unresolved)}件 / 準備中skip {prep_skipped}件 / 差分なしskip {unchanged_skipped}件")
        if unresolved:
            print(f"  未解決: {unresolved}")
        return {"would_insert": len(rows), "unresolved": unresolved, "unchanged_skipped": unchanged_skipped}

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
    print(f"[sb-dual-write] {game}: {len(rows)}行INSERT / 未解決{len(unresolved)}件 / "
          f"準備中skip{prep_skipped}件 / 差分なしskip{unchanged_skipped}件")
    return {"inserted": len(rows), "unresolved": unresolved, "unchanged_skipped": unchanged_skipped}
