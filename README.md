# トレーディングカード買取価格更新ツール

競合他社の買取価格を取得・解析し、Google Sheets の現行価格と比較・自動更新するツールです。

## 機能

1. **ウェブ自動取得**: ほむら東京のウェブサイトから価格を自動スクレイピング（GitHub Actions で毎日実行）
2. **画像解析**: 競合価格表画像を Claude Vision API で解析（マッチョ買取など画像のみの競合に対応）
3. **JSON読み込み**: 解析済みJSONファイルから直接データを読み込むことも可能
4. **価格比較**: スプレッドシートの現行価格と比較し、マージン状態を評価
5. **差分レポート**: 要対応商品・大幅変動品のレポートを生成
6. **自動更新**: GAS Webhook で価格を一括書き換え

## 対応カードゲーム・競合

| ゲーム | 競合 | 取得方式 | BOXマージン | カートンマージン |
|--------|------|----------|------------|----------------|
| ポケモン | ほむら東京 | ウェブ自動 | +200円 | +200円 |
| ワンピース | ほむら東京 | ウェブ自動 | +200円 | +1,000円 |
| ワンピース | マッチョ買取 | 画像貼り付け | +100円 | +1,000円 |
| ドラゴンボール | ほむら東京 | ウェブ自動 | +200円 | +1,000円 |
| ドラゴンボール | マッチョ買取 | 画像貼り付け | +100円 | +1,000円 |
| 遊戯王 | ほむら東京 | ウェブ自動 | +200円 | — |
| 遊戯王 | マッチョ買取 | 画像貼り付け | +100円 | — |

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

### ほむら東京（ウェブ自動取得）

GitHub Actions の「ホムラ価格取得」ワークフローが自動実行します。
手動で実行する場合:

```bash
python main.py -g pokemon    --fetch-web --competitor homura --dry-run
python main.py -g onepiece   --fetch-web --competitor homura --dry-run
python main.py -g yugioh     --fetch-web --competitor homura --dry-run
python main.py -g dragonball --fetch-web --competitor homura --dry-run
```

### マッチョ買取（画像貼り付け）

画像を渡してAI解析し、スプレッドシートを更新します。

```bash
python main.py -i macho_onepiece.jpg   -g onepiece   --margin-box 100 --margin-carton 1000 --yes
python main.py -i macho_dragonball.jpg -g dragonball --margin-box 100 --margin-carton 1000 --yes
python main.py -i macho_yugioh.jpg     -g yugioh     --margin-box 100 --yes
```

### JSONファイルから更新

```bash
python main.py --from-json data/homura_3_9_pokemon.json -g pokemon --yes
```

### データファイルの命名規則

```
data/{競合}_{月}_{日}_{ゲーム}.json

例:
  data/homura_3_9_pokemon.json     # ほむら東京・3/9・ポケモン
  data/macho_3_9_onepiece.json     # マッチョ買取・3/9・ワンピース
```

### オプション一覧

```
-i, --image       競合価格表画像ファイルパス（複数指定可）
--from-json       解析済みJSONファイルから読み込む
--fetch-web       ウェブサイトから価格を自動取得（homura のみ対応）
-g, --game        カードゲーム種別（pokemon / onepiece / dragonball / yugioh）[必須]
-c, --competitor  競合名（homura / macho）省略時は自動判定
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
- 大幅な価格変動（±5,000円以上）は ⚡ マークで強調表示されます
- 新規商品は 🆕 マークで表示されシートの末尾に追加されます
