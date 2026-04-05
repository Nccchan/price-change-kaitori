# price-change-kaitori プロジェクト メモ

## プロジェクト概要

競合店の買取価格を解析し、自社スプレッドシートの買取価格を自動更新するツール。

## 基本運用方針

**メイン参照: ほむら東京（毎日ウェブ自動取得）**
**サブ参照: 買取博士（毎日ウェブ自動取得）← 2026/04/05 よりマッチョから変更**

マッチョはウェブスクレイピング不可のため廃止。買取博士は自動取得ワークフロー稼働済み。
サブ更新のマージン: BOX +200円 / カートン +200円（ほむらとは別設定）

**⚠️ ワンピースについては例外**
ワンピースは買取博士のスクレイピング信頼性が低いため、ほむら東京をベースとする。
指示があった場合のみマッチョ価格（画像提供）で上書き更新する。

## 日次ルーティン

### 1. ほむら東京の価格取得（メイン）

GitHub Actions の「ホムラ価格取得」ワークフローで3ゲーム（ポケモン・ワンピース・ドラゴンボール）の価格を取得し、スプレッドシートを更新する。
**遊戯王は手動管理（スプレッドシート列構造の違いにより自動更新対象外）。**

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
python main.py --from-json data/homura_MM_DD_onepiece.json   -g onepiece   --yes
python main.py --from-json data/homura_MM_DD_dragonball.json -g dragonball --yes
```

※ 遊戯王は手動管理のため除外。

### 2. 買取博士の価格取得（サブ）

GitHub Actions の「買取博士価格取得」ワークフローで価格を取得する。
fetch-kaitorihakase.yml にはすでに push トリガーが設定済みのため、トリガーファイルをプッシュするだけでよい:

1. **トリガーファイルをプッシュ**（`mcp__github__push_files` で `trigger-kaitorihakase` ファイルを作成）
2. **完了を待つ**（約1〜2分。`mcp__github__list_commits` で `github-actions[bot]` のコミットを確認）
3. **後片付け**: `mcp__github__delete_file` で `trigger-kaitorihakase` を削除
4. **ローカルにフェッチ**: `git fetch origin <branch> && git checkout origin/<branch> -- data/kaitorihakase_MM_DD_*.json`

取得後、以下のコマンドでスプレッドシートを更新する（マージン: BOX +200円 / カートン +200円）:

```bash
python main.py --from-json data/kaitorihakase_MM_DD_onepiece.json   -g onepiece   --margin-box 200 --margin-carton 200 --yes
python main.py --from-json data/kaitorihakase_MM_DD_dragonball.json -g dragonball --margin-box 200 --margin-carton 200 --yes
```

※ ポケモンはほむらのみで管理。遊戯王は手動管理のため除外。
※ ほむらとの比較後に実施し、博士がほむら+マージンを上回る場合のみ更新する。

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

## マージン設定

### ほむら東京基準（メイン）

| ゲーム | BOX / シュリンクあり | カートン / シュリンクなし |
|--------|---------------------|--------------------------|
| ポケモン | +200円 | +200円 |
| ワンピース | +200円 | +2,000円 |
| ドラゴンボール | +200円 | +1,000円 |
| 遊戯王 | 手動管理 | — |

### 買取博士基準（サブ）

| ゲーム | BOX | カートン |
|--------|-----|---------|
| ワンピース | +200円 | +200円 |
| ドラゴンボール | +200円 | +200円 |

**注意**: 博士はカートン価格が低めに設定されている商品がある。ほむら+マージンより低くなる場合はほむら価格を維持すること（ツールが自動判定）。
