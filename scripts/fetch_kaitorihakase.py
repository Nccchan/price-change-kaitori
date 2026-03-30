#!/usr/bin/env python3
"""買取博士から全ゲームの価格を取得してJSONに保存するスクリプト"""
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.kaitorihakase_fetcher import KaitorihakaseFetcher
from src.models import GameType

fetcher = KaitorihakaseFetcher()
today = date.today()
os.makedirs("data", exist_ok=True)

for game in GameType:
    try:
        data = fetcher.fetch(game)
        path = f"data/kaitorihakase_{today.month}_{today.day}_{game.value}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(fetcher.to_json(data), f, ensure_ascii=False, indent=2)
        print(f"  保存: {path} ({len(data.items)} 商品)")
    except Exception as e:
        print(f"  [ERROR] {game.value}: {e}", file=sys.stderr)
