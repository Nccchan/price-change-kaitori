# price-change-kaitori プロジェクト メモ

## プロジェクト概要

競合店（ほむら東京など）の買取価格表画像を解析し、自社スプレッドシートの買取価格を自動更新するツール。

## 日次ルーティン

毎日の更新は以下の流れで行う。

### 1. JSONファイルを手動作成

画像を見て各ゲームの `data/homura_MM_DD_GAME.json` を作成する。
前日のJSONをコピーして差分だけ修正するのが効率的。

```
data/homura_3_12_pokemon.json
data/homura_3_12_onepiece.json
data/homura_3_12_yugioh.json
data/homura_3_12_dragonball.json
```

### 2. 全ゲームを順番に実行

```bash
python main.py --from-json data/homura_MM_DD_pokemon.json -g pokemon --yes
python main.py --from-json data/homura_MM_DD_onepiece.json -g onepiece --yes
python main.py --from-json data/homura_MM_DD_yugioh.json -g yugioh --yes
python main.py --from-json data/homura_MM_DD_dragonball.json -g dragonball --yes
```

**⚠️ 重要: 全ゲーム分を実行したか必ず確認すること。実行漏れに注意。**

### 3. コミット＆プッシュ

```bash
git add data/homura_MM_DD_*.json
git commit -m "Add homura MM/DD price data for all games and update spreadsheet"
git push -u origin claude/trading-card-price-updater-AoJuA
```

## 前日更新をスキップした場合

「〇/〇は更新していないので現在のスプレッドシートの価格と比較」という指示の場合、
`main.py` が自動的にスプレッドシートの現在値と比較するため、特別な操作は不要。
前日JSONが存在しなくても問題ない。

## JSONファイルの形式

```json
{
  "competitor": "homura_tokyo",
  "game": "pokemon",
  "date": "2026/03/12",
  "items": [
    {"name": "商品名", "code": "型式コード", "price_1": 10000, "price_2": 8000}
  ]
}
```

- `price_1`: シュリンクあり / BOX価格（シュリンクなし専用商品は `null`）
- `price_2`: シュリンクなし / カートン価格

## スプレッドシート書き込み方式

2つの方式を使い分けている:

| 方式 | 用途 | 認証 |
|------|------|------|
| GAS Webhook（`GAS_WEBHOOK_URL`） | 価格セルの書き込み（メイン） | 不要（GASがオーナー） |
| Sheets API v4（`credentials.json`） | 日付セル（A2）の更新 | サービスアカウント |

現在、Sheets API側で403エラーが発生しており、日付の自動更新はできていない。
価格データ本体はGAS経由で正常に書き込まれている。

## ゲーム別価格ラベル

| ゲーム | price_1 | price_2 |
|--------|---------|---------|
| pokemon | シュリンクあり | シュリンクなし |
| onepiece | BOX | カートン |
| dragonball | BOX | カートン |
| yugioh | BOX（price_1のみ使用） | — |

## マージン設定

### ほむら東京（homura）
- ポケモン: BOX +200円 / シュリンクなし +200円
- ワンピース: BOX +200円 / カートン +1,000円
- ドラゴンボール: BOX +200円 / カートン +1,000円
- 遊戯王: BOX +200円

### マッチョ（macho）
- ワンピース: **BOX 同額（0円）/ カートン +1,000円**
  ```bash
  python main.py --from-json data/macho_MM_DD_onepiece.json -g onepiece --margin-box 0 --margin-carton 1000 --yes
  ```
