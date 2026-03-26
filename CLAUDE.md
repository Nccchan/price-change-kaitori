# price-change-kaitori プロジェクト メモ

## プロジェクト概要

競合店（ほむら東京・マッチョ）の買取価格を解析し、自社スプレッドシートの買取価格を自動更新するツール。

## 競合店の対応方式

| 競合店 | 方式 | 備考 |
|--------|------|------|
| ほむら東京（homura） | **ウェブ自動取得** | GitHub Actions で毎日自動実行 |
| マッチョ（macho） | **画像貼り付け** | ウェブサイトなし。画像を渡してAI解析 |

## 日次ルーティン

### ほむら東京（homura）

GitHub Actions の「ホムラ価格取得」ワークフローが自動実行する。

**⚠️ Claude Code（ウェブ版）からのトリガー方法**

Claude Code ウェブ版には `gh` CLI も GitHub workflow dispatch MCP ツールも存在しない。
代わりに以下の手順でトリガーする（毎回この手順を実行すること）:

1. **ワークフローに push トリガーを一時追加**（`mcp__github__push_files` で `.github/workflows/fetch-homura.yml` を更新）:
   ```yaml
   on:
     workflow_dispatch:
     push:
       branches: [現在の作業ブランチ名]
       paths: ['trigger-homura']
   ```

2. **トリガーファイルをプッシュ**（`mcp__github__push_files` で `trigger-homura` ファイルを作成）

3. **完了を待つ**（約1〜2分。`mcp__github__list_commits` で `github-actions[bot]` のコミットを確認）

4. **後片付け**:
   - `mcp__github__push_files` でワークフローを元に戻す（push トリガー削除）
   - `mcp__github__delete_file` で `trigger-homura` を削除
   - ローカルに新しいJSONをフェッチ: `git fetch origin <branch> && git checkout origin/<branch> -- data/homura_MM_DD_*.json`

自動実行されると `data/homura_MM_DD_GAME.json` が生成・コミットされる。

### マッチョ（macho）

画像を貼り付けてAIに解析させ、JSONを生成→スプレッドシート更新する。

```bash
python main.py -i macho_MM_DD_onepiece.jpg -g onepiece --yes
python main.py -i macho_MM_DD_dragonball.jpg -g dragonball --yes
python main.py -i macho_MM_DD_yugioh.jpg -g yugioh --yes
```

**⚠️ 重要: 全ゲーム分を実行したか必ず確認すること。実行漏れに注意。**

### コミット＆プッシュ

```bash
git add data/
git commit -m "Add macho MM/DD price data and update spreadsheet"
git push -u origin claude/clarify-capabilities-Q63XS
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

```bash
# マッチョ実行時のオプション例
python main.py -i macho_MM_DD_onepiece.jpg -g onepiece --margin-box 100 --margin-carton 1000 --yes
python main.py -i macho_MM_DD_dragonball.jpg -g dragonball --margin-box 100 --margin-carton 1000 --yes
python main.py -i macho_MM_DD_yugioh.jpg -g yugioh --margin-box 100 --yes
```
