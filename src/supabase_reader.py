"""現行の買取価格を Supabase から読む（『にこにこ買取(旧)』シート読みの置き換え）。

なつき指示 2026-08-05「②も旧シートも消していい。使わないでしょう」。
ただし旧シートは main.py が「現行価格」を読む本体で、そこから
  ・差分（要対応）の判定
  ・5%超の値上げ停止
  ・大幅値下げ停止
  ・is_new（新弾）判定
が全部作られている。だから読み元を差し替えるまでシートは外せない。

GVizReader と同じ List[CardItem] を返すので、main.py 側は差し替えるだけでよい。

対応関係:
  code        = products.homura_ref（競合(ホムラ)の型式。comparatorはこれで突合する）
  name        = products.name_jp（BOX行の名前）
  price_1     = 現在の買取 BOX
  price_2     = ポケモン=NS / それ以外=CARTON
  locked_1/2  = products.kaitori_prep（買取「準備中」＝書き換え禁止）
  row_index   = None（シートに書かないため不要。sheets_writer は名前でマッチする方式）
"""
import json
import os
import re
import urllib.parse
import urllib.request
from typing import List, Optional

from src.models import CardItem

_ORG = "/Users/nastuki_sever/aigive/org"
# price_2 が何を指すか（main.py / supabase_writer と同じ規約）
_PRICE2_UNIT = {"pokemon": "NS", "onepiece": "CARTON", "dragonball": "CARTON", "yugioh": "CARTON"}
_PREFIX = {"pokemon": "PKM", "onepiece": "OPE", "dragonball": "DBZ", "yugioh": "YGO"}


def _conf():
    env = {}
    for path in (f"{_ORG}/apps/pricing/.env.local", f"{_ORG}/.env"):
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    m = re.match(r'\s*([A-Z_]+)\s*=\s*"?([^"\n]+)"?', line)
                    if m:
                        env.setdefault(m.group(1), m.group(2).strip())
        except FileNotFoundError:
            pass
    url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or env.get("NEXT_PUBLIC_SUPABASE_URL")
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
           or env.get("SUPABASE_SERVICE_ROLE_KEY") or env.get("SUPABASE_SERVICE"))
    if not url or not key:
        raise RuntimeError("Supabase認証情報が無い")
    return url, key


def _rest(path):
    url, key = _conf()
    req = urllib.request.Request(f"{url}/rest/v1/{path}",
                                 headers={"apikey": key, "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
        return json.loads(raw) if raw else []


def _page(path):
    """1000行上限で静かに欠ける事故（F-002）を避けるため必ずページングする。"""
    out, off = [], 0
    while True:
        rows = _rest(f"{path}&limit=1000&offset={off}")
        out += rows
        if len(rows) < 1000:
            return out
        off += 1000


class SupabaseReader:
    """GVizReader と同じインターフェースで現行価格を返す。"""

    def read_sheet(self, game: str) -> List[CardItem]:
        pref = _PREFIX.get(game)
        if not pref:
            raise ValueError(f"Unknown game: {game}")
        unit2 = _PRICE2_UNIT[game]

        prods = _page(
            f"products?select=id,sku,name_jp,homura_ref,kaitori_prep"
            f"&is_active=eq.true&sku=like.{urllib.parse.quote(pref + '-%')}")
        by_id = {p["id"]: p for p in prods}
        if not by_id:
            return []

        # 現在の買取（price_history の最新）。公開ビューではなく実値を見る
        latest = {}
        ids = list(by_id)
        for i in range(0, len(ids), 50):
            chunk = ",".join(ids[i:i + 50])
            for r in _rest(f"price_history?kind=eq.kaitori&product_id=in.({chunk})"
                           f"&select=product_id,unit,value,valid_from"
                           f"&order=valid_from.desc&limit=10000"):
                k = (r["product_id"], r["unit"])
                if k not in latest:      # desc なので最初が最新
                    v = int(r["value"])
                    # 旧方式の「準備中=0」はシート側では空欄と同じ扱いなので None に寄せる
                    latest[k] = v if v > 0 else None

        # stem 単位に畳む（BOX と NS/CARTON を1行にまとめる＝シートの1行に相当）
        rows = {}
        for p in prods:
            m = re.match(r"^(.*)-(BOX|CTN|CARTON|NS|PACK|DECK|SET)$", p["sku"])
            stem = m.group(1) if m else p["sku"]
            unit = m.group(2) if m else "BOX"
            if unit == "CTN":
                unit = "CARTON"
            e = rows.setdefault(stem, {"name": "", "code": "", "p1": None, "p2": None,
                                       "l1": False, "l2": False})
            v = latest.get((p["id"], unit))
            if unit == "BOX":
                e["p1"] = v
                e["l1"] = bool(p.get("kaitori_prep"))
                if p.get("name_jp"):
                    e["name"] = p["name_jp"].strip()
                if p.get("homura_ref"):
                    e["code"] = p["homura_ref"].strip()
            elif unit == unit2:
                e["p2"] = v
                e["l2"] = bool(p.get("kaitori_prep"))
                if not e["code"] and p.get("homura_ref"):
                    e["code"] = p["homura_ref"].strip()
                if not e["name"] and p.get("name_jp"):
                    # 「(カートン)」「（シュリンクなし）」は落として BOX 名に寄せる
                    e["name"] = re.sub(r"\s*[（(](?:カートン|シュリンクなし|パック)[)）]\s*$", "",
                                       p["name_jp"]).strip()

        items = []
        for stem, e in rows.items():
            if e["p1"] is None and e["p2"] is None:
                continue          # 買取値が一度も入っていないものはシートにも無い
            items.append(CardItem(name=e["name"], code=e["code"],
                                  price_1=e["p1"], price_2=e["p2"],
                                  row_index=None, locked_1=e["l1"], locked_2=e["l2"]))
        return items
