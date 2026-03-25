# price-change-kaitori プロジェクト メモ

## プロジェクト概要

競合店（ほむら東京・マッチョ）の買取価格を解析し、自社スプレッドシートの買取価格を自動更新するツール。

## 競合店の対応方式

| 競合店 | 方式 | 備考 |
|--------|------|------|
| ほむら東京（homura） | **ウェブ自動取得** | GitHub Actions で手動トリガー実行 |
| マッチョ（macho） | **画像貼り付け** | ウェブサイトなし。画像を渡してAI解析 |

## 環境変数（.env）

セッション開始時に `.claude/hooks/session-start.sh` が自動的に `.env` を生成する。
手動で作成する場合は以下を `.env` に記載：

```
GAS_WEBHOOK_URL=https://script.google.com/macros/s/AKfycbxppdSa5_-jnLBTkXZBRGpXaNx27Fb80UkqbktkZCJyICW6HvUxsYHRqK2o6vIT5_NH_A/exec
SPREADSHEET_ID=1PBMNNYHliomlgeNsvZgiccrfOWpIJbYPb9EMFtSAgdw
```

GitHub Actions では `secrets.GAS_WEBHOOK_URL` シークレットを使用。

## 制限事項（Claude Code on the web）

- `kaitori-homura.com` へのアクセス不可（DNS解決失敗）→ ホムラの価格取得はこの環境では実行できない
- GitHub Actions を直接トリガーする手段なし（プロキシが git 操作のみ対応）
- **ホムラの価格取得・スプレッドシート書き込みは GitHub の Actions タブから手動で実行すること**
- Sheets API（日付セル A2 の更新）は 403 エラーのため機能しない。価格データは GAS 経由で書き込む

## 日次ルーティン

### ほむら東京（homura）

**GitHub の Actions タブ** → `ホムラ価格取得` → Run workflow で手動実行する。

実行すると全4ゲーム分を取得し、`data/homura_MM_DD_GAME.json` を生成・コミット・スプレッドシート書き込みまで自動で行う。

### マッチョ（macho）

#### ① 画像から解析してスプレッドシート更新（通常）

```bash
python main.py -i macho_MM_DD_onepiece.jpg   -g onepiece   --margin-box 100 --margin-carton 1000 --yes
python main.py -i macho_MM_DD_dragonball.jpg -g dragonball --margin-box 100 --margin-carton 1000 --yes
python main.py -i macho_MM_DD_yugioh.jpg     -g yugioh     --margin-box 100 --yes
```

**⚠️ 重要: 全ゲーム分を実行したか必ず確認すること。実行漏れに注意。**

#### ② 既存JSONからスプレッドシート更新（再実行・書き込み漏れ時）

```bash
python main.py --from-json data/macho_MM_DD_onepiece.json   -g onepiece   --margin-box 100 --margin-carton 1000 --yes
python main.py --from-json data/macho_MM_DD_dragonball.json -g dragonball --margin-box 100 --margin-carton 1000 --yes
python main.py --from-json data/macho_MM_DD_yugioh.json     -g yugioh     --margin-box 100 --yes
```

#### ③ GitHub Actions で処理（JSONがすでにある場合）

Actions タブ → `マッチョ価格処理` → ゲームを選択して Run workflow。
最新の `data/macho_*_{game}.json` を自動検出してスプレッドシートに書き込む。

### コミット＆プッシュ

```bash
git add data/
git commit -m "Add macho MM/DD price data and update spreadsheet"
git push -u origin claude/fetch-competitor-prices-1htLM
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

| 方式 | 用途 | 認証 | 状態 |
|------|------|------|------|
| GAS Webhook（`GAS_WEBHOOK_URL`） | 価格セルの書き込み（メイン） | 不要（GASがオーナー） | ✅ 正常 |
| Sheets API v4（`credentials.json`） | 日付セル（A2）の更新 | サービスアカウント | ❌ 403エラー |

## ゲーム別価格ラベル

| ゲーム | price_1 | price_2 |
|--------|---------|---------|
| pokemon | シュリンクあり | シュリンクなし |
| onepiece | BOX | カートン |
| dragonball | BOX | カートン |
| yugioh | BOX（price_1のみ使用） | — |

## マージン設定

### ほむら東京（homura）

| ゲーム | BOX / シュリンクあり | カートン / シュリンクなし |
|--------|---------------------|--------------------------|
| ポケモン | +200円 | +200円 |
| ワンピース | +200円 | +1,000円 |
| ドラゴンボール | +200円 | +1,000円 |
| 遊戯王 | +200円 | — |

### マッチョ（macho）

| ゲーム | BOX / シュリンクあり | カートン / シュリンクなし |
|--------|---------------------|--------------------------|
| 全ゲーム共通 | +100円 | +1,000円 |
