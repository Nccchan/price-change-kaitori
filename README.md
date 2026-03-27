# トレーディングカード買取価格更新ツール

競合他社の買取価格を取得・解析し、Google Sheets の現行価格と比較・自動更新するツールです。

## 運用方針

- **メイン**: ほむら東京の価格をウェブ自動取得し、毎日全ゲームを更新
- **サブ**: 指示に応じて任意の競合店の価格（画像・テキスト問わず）を読み取り、指定ゲームを更新

## 機能

1. **ウェブ自動取得**: ほむら東京のウェブサイトから価格を自動スクレイピング（GitHub Actions で毎日実行）
2. **画像・テキスト解析**: 競合価格表を Claude Vision API で解析（画像のみの競合店にも対応）
3. **JSON読み込み**: 解析済みJSONファイルから直接データを読み込むことも可能
4. **価格比較**: スプレッドシートの現行価格と比較し、マージン状態を評価
5. **差分レポート**: 要対応商品・大幅変動品のレポートを生成
6. **自動更新**: GAS Webhook で価格を一括書き換え

## 対応カードゲームとマージン設定

| ゲーム | メイン参照 | BOXマージン | カートンマージン |
|--------|-----------|------------|----------------|
| ポケモン | ほむら東京 | +200円 | +200円 |
| ワンピース | ほむら東京 | +500円 | +3,000円 |
| ドラゴンボール | ほむら東京 | +200円 | +1,000円 |
| 遊戯王 | ほむら東京 | +200円 | — |

サブ更新時は指示に応じて競合店・マージンを変更して実行する。

## セットアップ

### 1. 依存関係インストール

```bash
pip install -r requirements.txt
```

### 2. 環境変数設定

```bash
cp .env.example .env
# .env を編集して以下を設定:
#   ANTHROPIC_API_KEY=your_key
#   SPREADSHEET_ID=your_spreadsheet_id
#   GAS_WEBHOOK_URL=your_gas_webhook_url
```

## 使い方

### メイン: ほむら東京（ウェブ自動取得 → スプレッドシート更新）

GitHub Actions の「ホムラ価格取得」ワークフローが自動実行します（データ取得のみ）。
取得後、以下でスプレッドシートを更新します:

```bash
python main.py --from-json data/homura_MM_DD_pokemon.json    -g pokemon    --yes
python main.py --from-json data/homura_MM_DD_onepiece.json   -g onepiece   --margin-box 500 --margin-carton 3000 --yes
python main.py --from-json data/homura_MM_DD_yugioh.json     -g yugioh     --yes
python main.py --from-json data/homura_MM_DD_dragonball.json -g dragonball --yes
```

### サブ: 任意競合店の価格で指定ゲームを更新

画像・テキスト・URLなど形式は問いません。競合店はどこでも対応可能です。

```bash
# 画像から読み取って更新
python main.py -i <画像ファイル> -g <ゲーム> -c <競合名> --margin-box <N> --margin-carton <M> --yes

# ドライランで確認してから更新
python main.py -i <画像ファイル> -g <ゲーム> --dry-run
python main.py -i <画像ファイル> -g <ゲーム> --yes
```

### JSONファイルから更新

```bash
python main.py --from-json data/homura_3_27_pokemon.json -g pokemon --yes
```

### データファイルの命名規則

```
data/{競合}_{月}_{日}_{ゲーム}.json

例:
  data/homura_3_27_pokemon.json    # ほむら東京・3/27・ポケモン
  data/macho_3_26_onepiece.json    # マッチョ買取・3/26・ワンピース
```

### オプション一覧

```
-i, --image       競合価格表画像ファイルパス（複数指定可）
--from-json       解析済みJSONファイルから読み込む
--fetch-web       ウェブサイトから価格を自動取得（homura のみ対応）
-g, --game        カードゲーム種別（pokemon / onepiece / dragonball / yugioh）[必須]
-c, --competitor  競合名（省略時は自動判定）
--dry-run         スプレッドシートを更新せず比較レポートのみ出力
-y, --yes         確認プロンプトをスキップして自動更新
--margin-box      BOXマージン上書き（円）
--margin-carton   カートンマージン上書き（円）
-o, --output      レポートをファイルに保存
```

## スプレッドシート書き込み方式

| 方式 | 用途 |
|------|------|
| GAS Webhook | 価格セルの書き込み（メイン） |
| Sheets API v4 | 日付セル（A2）の更新 |

## スプレッドシート構成

各ゲームのタブは以下の列構成:

| A列 | B列 | C列 | D列 |
|-----|-----|-----|-----|
| 商品名 | 型式 | 価格1（BOX/シュリンクあり） | 価格2（カートン/シュリンクなし） |

## 注意事項

- 画像解析には `ANTHROPIC_API_KEY` が必要です
- サブ更新時は必ず事前に `--dry-run` または比較スクリプトで現在価格と確認してから更新すること
- 大幅な価格変動（±5,000円以上）は ⚡ マークで強調表示されます
- 新規商品は 🆕 マークで表示されシートの末尾に追加されます
