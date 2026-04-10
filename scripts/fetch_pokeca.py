#!/usr/bin/env python3
"""ポケカ買取チェッカーから最高買取価格を取得してJSONに保存するスクリプト"""
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.pokeca_fetcher import PokecaFetcher
from src.models import GameType

fetcher = PokecaFetcher()
today = date.today()
os.makedirs("data", exist_ok=True)

# pokeca-box-hikaku.com はポケモンのみ対応
game = GameType.POKEMON
try:
    data = fetcher.fetch(game)
    path = f"data/pokeca_{today.month}_{today.day}_{game.value}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(fetcher.to_json(data), f, ensure_ascii=False, indent=2)
    print(f"  保存: {path} ({len(data.items)} 商品)")
except Exception as e:
    print(f"  [ERROR] {game.value}: {e}", file=sys.stderr)
    sys.exit(1)
