"""ラントゥ666価格をホムラ候補へ事前合流する。

値下げ承認ゲートより前に ``max(ホムラ+margin, ラントゥ+margin)`` を確定する。
取得失敗を安いホムラ値として扱わないため、通信・パース異常は呼出元へ送出する。
"""
from dataclasses import dataclass
import html as html_lib
import json
import re
import urllib.error
import urllib.request
from typing import Dict, Iterable, List, Optional, Tuple

from src.models import CardItem, CompetitorData


UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
TITLE_RE = re.compile(r'<h2[^>]*woocommerce-loop-product__title[^>]*>([^<]+)</h2>')
AMOUNT_RE = re.compile(r'<bdi[^>]*>(?:<span[^>]*>[^<]*</span>)?([0-9,]+)</bdi>')
PRODUCT_LINK_RE = re.compile(
    r'<a[^>]+href=["\']([^"\']+)["\'][^>]*class=["\'][^"\']*woocommerce-LoopProduct-link[^"\']*["\'][^>]*>',
    re.I,
)
VARIATION_DATA_RE = re.compile(r'data-product_variations="([^"]+)"', re.I)
CODE_RE = re.compile(r'【((?:OP|EB|PRB)-?\d{2})】', re.I)
GAME_CONFIG = {
    "pokemon": ("card", 11, "pkm"),
    "onepiece": ("onepiece", 4, "op"),
    "dragonball": ("dg", 5, "op"),
}

# 通常BOXと、同名を含むセット／プロモ等を部分一致で混同しない。
# 例: 「イーブイヒーローズ」へ「イーブイヒーローズ イーブイズセット」
# (¥300,000) を紐付けると通常BOXが2倍超に暴騰する。
VARIANT_TOKENS = ("セット", "プロモ", "カートン", "デッキ", "スペシャル", "コレクション")


@dataclass(frozen=True)
class RuntoSelection:
    name: str
    code: str
    unit: str
    homura_final: Optional[int]
    runto_final: int
    final: int


@dataclass(frozen=True)
class RuntoVariationProduct:
    code: str
    title: str
    url: str
    box: int
    carton: int
    box_variation_id: int
    carton_variation_id: int


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def _variation_labels(page_html: str) -> Dict[str, str]:
    """pa_shrink の value→表示名を読む（ari→シュリンク有、case→カートン等）。"""
    match = re.search(r'<select[^>]+(?:id="pa_shrink"|name="attribute_pa_shrink")[^>]*>(.*?)</select>', page_html, re.I | re.S)
    if not match:
        return {}
    return {
        html_lib.unescape(value): re.sub(r'<[^>]+>', '', html_lib.unescape(label)).strip()
        for value, label in re.findall(r'<option[^>]+value="([^"]+)"[^>]*>(.*?)</option>', match.group(1), re.I | re.S)
        if value
    }


def _parse_onepiece_variations(title: str, url: str, page_html: str) -> RuntoVariationProduct:
    code_match = CODE_RE.search(title)
    if not code_match:
        raise ValueError(f"セットコード不明: {title}")
    code = code_match.group(1).upper()
    data_match = VARIATION_DATA_RE.search(page_html)
    if not data_match:
        raise ValueError(f"variation JSONなし: {code}")
    try:
        variations = json.loads(html_lib.unescape(data_match.group(1)))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"variation JSON解析失敗: {code}") from exc
    labels = _variation_labels(page_html)
    accepted: Dict[str, List[Tuple[int, int]]] = {"BOX": [], "CARTON": []}
    for variation in variations:
        attrs = variation.get("attributes") or {}
        raw = attrs.get("attribute_pa_shrink")
        label = labels.get(raw, raw or "")
        unit = {"シュリンク有": "BOX", "カートン": "CARTON"}.get(label)
        if unit is None:  # テープカット・未知属性は自動対象外
            continue
        active = variation.get("variation_is_active") is True and variation.get("variation_is_visible") is True
        in_stock = variation.get("is_in_stock")
        if in_stock is None:
            in_stock = "in-stock" in (variation.get("availability_html") or "")
        if not active or not in_stock:
            continue
        price = variation.get("display_price")
        variation_id = variation.get("variation_id")
        if not isinstance(price, (int, float)) or not isinstance(variation_id, int):
            continue
        accepted[unit].append((variation_id, int(price)))
    for unit, values in accepted.items():
        if len(values) != 1:
            raise ValueError(f"{code} {unit}: 有効variationが{len(values)}件（1件必須）")
    box_id, box = accepted["BOX"][0]
    carton_id, carton = accepted["CARTON"][0]
    return RuntoVariationProduct(code, title, url, box, carton, box_id, carton_id)


def fetch_onepiece_variations() -> List[RuntoVariationProduct]:
    """カテゴリからセットコード付き商品を列挙し、個別variation価格を取得する。"""
    links: Dict[str, Tuple[str, str]] = {}
    for page in range(1, GAME_CONFIG["onepiece"][1] + 1):
        base = "https://runto666.com/product-category/onepiece/"
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
                raise RuntimeError("runto666 onepiece: カテゴリ商品を抽出できません")
            break
        anchors = list(PRODUCT_LINK_RE.finditer(page_html))
        for title_match in titles:
            title = html_lib.unescape(title_match.group(1)).strip()
            code_match = CODE_RE.search(title)
            if not code_match:
                continue
            preceding = [a for a in anchors if a.start() < title_match.start()]
            if not preceding:
                raise RuntimeError(f"runto666 onepiece: 商品URLなし: {title}")
            code = code_match.group(1).upper()
            if code in links:
                raise RuntimeError(f"runto666 onepiece: セットコード重複: {code}")
            links[code] = (title, html_lib.unescape(preceding[-1].group(1)))
    if not links:
        raise RuntimeError("runto666 onepiece: セットコード付き商品が0件です")
    products = [_parse_onepiece_variations(title, url, _get(url)) for title, url in links.values()]
    return sorted(products, key=lambda product: product.code)


def fetch(game: str) -> List[Tuple[str, int, int]]:
    if game == "onepiece":
        return [(p.title, p.box, p.carton) for p in fetch_onepiece_variations()]
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
    partial = []
    for item in items:
        item_name = _norm(item.name)
        if min(len(item_name), len(title_name)) < 4:
            continue
        if not (item_name in title_name or title_name in item_name):
            continue
        # 外部タイトルだけにvariant語がある場合、通常商品への救済的部分一致を禁止。
        if any(token in title and token not in (item.name or "") for token in VARIANT_TOKENS):
            continue
        partial.append(item)
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
