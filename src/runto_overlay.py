"""ラントゥ666価格をホムラ候補へ事前合流する。

値下げ承認ゲートより前に ``max(ホムラ+margin, ラントゥ+margin)`` を確定する。
取得失敗を安いホムラ値として扱わないため、通信・パース異常は呼出元へ送出する。
"""
from dataclasses import dataclass
import html as html_lib
import re
import urllib.error
import urllib.request
from typing import Dict, Iterable, List, Optional, Tuple

from src.models import CardItem, CompetitorData


UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
TITLE_RE = re.compile(r'<h2[^>]*woocommerce-loop-product__title[^>]*>([^<]+)</h2>')
AMOUNT_RE = re.compile(r'<bdi[^>]*>(?:<span[^>]*>[^<]*</span>)?([0-9,]+)</bdi>')
GAME_CONFIG = {
    "pokemon": ("card", 11, "pkm"),
    "onepiece": ("onepiece", 4, "op"),
    "dragonball": ("dg", 5, "op"),
}


@dataclass(frozen=True)
class RuntoSelection:
    name: str
    code: str
    unit: str
    homura_final: Optional[int]
    runto_final: int
    final: int


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def fetch(game: str) -> List[Tuple[str, int, int]]:
    slug, max_pages, _ = GAME_CONFIG[game]
    products: List[Tuple[str, int, int]] = []
    for page in range(1, max_pages + 1):
        base = f"https://runto666.com/product-category/{slug}/"
        url = base if page == 1 else f"{base}page/{page}/"
        try:
            page_html = _get(url)
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and page > 1:
                break
            raise
        titles = list(TITLE_RE.finditer(page_html))
        if not titles:
            if page == 1:
                raise RuntimeError(f"runto666 {game}: 商品を抽出できません")
            break
        for index, match in enumerate(titles):
            end = titles[index + 1].start() if index + 1 < len(titles) else min(len(page_html), match.end() + 4000)
            prices = [int(v.replace(",", "")) for v in AMOUNT_RE.findall(page_html[match.end():end])]
            if prices:
                products.append((html_lib.unescape(match.group(1)).strip(), prices[0], prices[-1]))
    if not products:
        raise RuntimeError(f"runto666 {game}: 取得結果が0件です")
    return products


def _norm(value: str) -> str:
    value = html_lib.unescape(value or "").lower().replace("é", "e")
    value = re.sub(r"one pieceカードゲーム|one piece|ポケモンカードゲーム", "", value)
    value = re.sub(r"mega|拡張パック|強化拡張パック|ハイクラスパック|ブースターパック|box|ボックス", "", value)
    return re.sub(r"[\s　「」『』()（）・･\-_/]", "", value)


def _code(value: str) -> str:
    match = re.search(r"(?:OP|EB|PRB|CHP|FB|SB|SV|S|SM|M)[- ]?[0-9]+[A-Z]?", value or "", re.I)
    return re.sub(r"[^A-Z0-9]", "", match.group(0).upper()) if match else ""


def _match(title: str, items: Iterable[CardItem]) -> Optional[CardItem]:
    title_code = _code(title)
    if title_code:
        matches = [item for item in items if title_code == _code(item.code) or title_code == _code(item.name)]
        if len(matches) == 1:
            return matches[0]
    title_name = _norm(title)
    exact = [item for item in items if _norm(item.name) == title_name and title_name]
    if len(exact) == 1:
        return exact[0]
    partial = [item for item in items if min(len(_norm(item.name)), len(title_name)) >= 4 and (_norm(item.name) in title_name or title_name in _norm(item.name))]
    return partial[0] if len(partial) == 1 else None


def apply_runto_max(
    game: str,
    data: CompetitorData,
    margin_box: int,
    margin_second: int,
    products: Optional[List[Tuple[str, int, int]]] = None,
    runto_margin_second: Optional[int] = None,
) -> Tuple[List[RuntoSelection], int]:
    """競合生値を上書きし、推奨値が両社の最終値の最大になるようにする。"""
    products = fetch(game) if products is None else products
    _, _, mode = GAME_CONFIG[game]
    bids: Dict[Tuple[int, int], int] = {}
    matched = 0
    for title, low, high in products:
        item = _match(title, data.items)
        if item is None:
            continue
        matched += 1
        if mode == "pkm":
            raw_box = high if high == low or (high - low) / max(low, 1) <= 0.30 else low
            bids[(id(item), 1)] = max(bids.get((id(item), 1), 0), raw_box)
        else:
            bids[(id(item), 1)] = max(bids.get((id(item), 1), 0), low)
            if high > low:
                bids[(id(item), 2)] = max(bids.get((id(item), 2), 0), high)

    if matched == 0:
        raise RuntimeError(f"runto666 {game}: ホムラ商品との照合が0件です")

    selections: List[RuntoSelection] = []
    for item in data.items:
        for index, output_margin, runto_margin, unit in (
            (1, margin_box, margin_box, "BOX"),
            (2, margin_second, runto_margin_second if runto_margin_second is not None else margin_second,
             "NS" if game == "pokemon" else "CARTON"),
        ):
            raw = bids.get((id(item), index))
            if raw is None:
                continue
            current_raw = getattr(item, f"price_{index}")
            homura_final = current_raw + output_margin if current_raw is not None else None
            runto_final = raw + runto_margin
            final = max(homura_final or 0, runto_final)
            if final == runto_final and (homura_final is None or runto_final > homura_final):
                setattr(item, f"price_{index}", final - output_margin)
                selections.append(RuntoSelection(item.name, item.code, unit, homura_final, runto_final, final))
    return selections, matched
