#!/usr/bin/env python3
"""
最新の買取価格データを Claude.ai 等のチャットに貼り付け用テキストとして出力するスクリプト

使い方:
  python scripts/prepare_chat_context.py              # 今日の最新データ
  python scripts/prepare_chat_context.py --date 4/9  # 指定日
  python scripts/prepare_chat_context.py --output context.txt  # ファイルに保存
"""
import argparse
import glob
import json
import os
import re
import sys
from datetime import date
from typing import Optional


def find_latest_json(data_dir: str, competitor: str, game: str, target_date: Optional[str] = None) -> Optional[str]:
    """指定競合・ゲームの最新JSONファイルを返す"""
    pattern = os.path.join(data_dir, f"{competitor}_*_{game}.json")
    candidates = glob.glob(pattern)
    if not candidates:
        return None

    if target_date:
        # 例: "4/9" → month=4, day=9
        m = re.match(r"(\d+)/(\d+)", target_date)
        if m:
            month, day = m.group(1), m.group(2)
            exact = os.path.join(data_dir, f"{competitor}_{month}_{day}_{game}.json")
            if os.path.exists(exact):
                return exact

    return max(candidates, key=os.path.getmtime)


def format_price(price) -> str:
    if price is None:
        return "—"
    return f"¥{int(price):,}"


def load_items(path: str) -> tuple:
    """JSONを読み込んで (date_str, items) を返す"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("date", ""), data.get("items", [])


def build_table(items: list, label1: str, label2: str) -> str:
    if not items:
        return "  （データなし）\n"

    col1_w = max(len(it["name"]) for it in items)
    col1_w = max(col1_w, 4)

    header = f"| {'商品名':<{col1_w}} | {'型式':<8} | {label1:>10} | {label2:>10} |"
    sep    = f"|{'-' * (col1_w + 2)}|{'-' * 10}|{'-' * 12}|{'-' * 12}|"
    lines  = [header, sep]

    for it in items:
        p1 = format_price(it.get("price_1"))
        p2 = format_price(it.get("price_2"))
        lines.append(
            f"| {it['name']:<{col1_w}} | {it.get('code', ''):<8} | {p1:>10} | {p2:>10} |"
        )

    return "\n".join(lines) + "\n"


GAMES = [
    ("pokemon",    "ポケモン",       "シュリンクあり", "シュリンクなし"),
    ("onepiece",   "ワンピース",     "BOX",           "カートン"),
    ("dragonball", "ドラゴンボール", "BOX",           "カートン"),
]

COMPETITORS = [
    ("homura",         "ほむら東京"),
    ("kaitorihakase",  "買取博士"),
    ("pokeca",         "ポケカ買取チェッカー（最高値）"),
]


def main():
    parser = argparse.ArgumentParser(description="買取価格データをチャット用テキストに整形")
    parser.add_argument("--date", default=None, help="対象日 例: 4/9（省略時は最新）")
    parser.add_argument("--output", "-o", default=None, help="出力先ファイル（省略時は標準出力）")
    parser.add_argument("--data-dir", default="data", help="JSONファイルのディレクトリ")
    args = parser.parse_args()

    today = date.today()
    sections = []

    sections.append("# 買取価格データ")
    sections.append(f"生成日: {today.strftime('%Y/%m/%d')}")
    if args.date:
        sections.append(f"対象日: {args.date}")
    sections.append("")
    sections.append(
        "このデータを参考に、お客様からの買取商品に対して価格見積もりを行ってください。\n"
        "price_1: BOX（ポケモンはシュリンクあり）/ price_2: カートン（ポケモンはシュリンクなし）\n"
    )

    for game_key, game_label, label1, label2 in GAMES:
        game_sections = []

        for comp_key, comp_label in COMPETITORS:
            # pokeca はポケモンのみ
            if comp_key == "pokeca" and game_key != "pokemon":
                continue

            path = find_latest_json(args.data_dir, comp_key, game_key, args.date)
            if not path:
                continue

            date_str, items = load_items(path)
            if not items:
                continue

            game_sections.append(f"### {comp_label}（{date_str}）")
            game_sections.append(build_table(items, label1, label2))

        if game_sections:
            sections.append(f"## {game_label}")
            sections.extend(game_sections)

    output = "\n".join(sections)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"保存: {args.output}  ({len(output):,} 文字)", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
