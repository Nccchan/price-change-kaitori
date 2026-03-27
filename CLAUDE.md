# price-change-kaitori プロジェクト メモ

## プロジェクト概要

競合店の買取価格を解析し、自社スプレッドシートの買取価格を自動更新するツール。

## 基本運用方針

**メイン参照: ほむら東京（毎日ウェブ自動取得）**
サブ更新: 指示があった場合に、ゲームを指定して画像またはテキストで任意の競合店の価格を読み取り更新する。
競合店はマッチョに限らず、どの店舗でも対応可能。

## 日次ルーティン

### 1. ほむら東京の価格取得（メイン）

GitHub Actions の「ホムラ価格取得」ワークフローで全4ゲームの価格を取得し、スプレッドシートを更新する。

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

取得後、以下のコマンドでスプレッドシートを更新する:

```bash
python main.py --from-json data/homura_MM_DD_pokemon.json    -g pokemon    --yes
python main.py --from-json data/homura_MM_DD_onepiece.json   -g onepiece   --margin-box 500 --margin-carton 3000 --yes
python main.py --from-json data/homura_MM_DD_yugioh.json     -g yugioh     --yes
python main.py --from-json data/homura_MM_DD_dragonball.json -g dragonball --yes
```

### 2. サブ更新（任意競合・指示ベース）

「〇〇の△△（ゲーム名）を更新して」と指示があった場合に実行。
画像・テキスト・URLなど形式は問わない。競合店もマッチョ以外でも可。

```bash
# 画像から読み取る場合
python main.py -i <画像ファイル> -g <ゲーム> -c <競合名> --margin-box <N> --margin-carton <M> --yes

# テキストやJSONから更新する場合
python main.py --from-json data/<競合>_MM_DD_<ゲーム>.json -g <ゲーム> --margin-box <N> --margin-carton <M> --yes
```

**更新前に必ず現在価格と比較して確認してから更新すること（`--dry-run` または比較スクリプト）。**

### 3. コミット＆プッシュ

```bash
git add data/
git commit -m "Add MM/DD price data and update spreadsheet"
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

## マージン設定（ほむら東京基準）

**基本方針: すべてほむら東京を参照する。**
サブ更新時は指示に従いマージンを調整すること。

| ゲーム | BOX / シュリンクあり | カートン / シュリンクなし |
|--------|---------------------|--------------------------|
| ポケモン | +200円 | +200円 |
| ワンピース | +500円 | +3,000円 |
| ドラゴンボール | +200円 | +1,000円 |
| 遊戯王 | +200円 | — |
