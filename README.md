# トレーディングカード買取価格更新ツール

競合他社の価格表画像を Claude Vision で解析し、Google Sheets の現行価格と比較・自動更新するツールです。

## 機能

1. **画像解析**: 競合価格表画像を Claude Vision API で解析（ほむら東京・マッチョ買取に対応）
2. **JSON読み込み**: 解析済みJSONファイルから直接データを読み込むことも可能
3. **価格比較**: スプレッドシートの現行価格と比較し、マージン状態を評価
4. **差分レポート**: 要対応商品・大幅変動品のレポートを生成
5. **自動更新**: Google Sheets API で価格を一括書き換え

## 対応カードゲーム・競合

| ゲーム | 競合 | BOXマージン | カートンマージン |
|--------|------|------------|----------------|
| ポケモン | ほむら東京 | +200円 | +200円 |
| ワンピース | ほむら東京 | +200円 | +1,000円 |
| ワンピース | マッチョ買取 | +200円 | +1,000円 |
| ドラゴンボール | ほむら東京 | +200円 | +1,000円 |
| 遊戯王 | ほむら東京 | +200円 | +1,000円 |

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
#   GOOGLE_CREDENTIALS_PATH=credentials.json
```

### 3. Google Sheets API 認証設定（書き込みに必要）

1. [Google Cloud Console](https://console.cloud.google.com/) でプロジェクト作成
2. **Google Sheets API** を有効化
3. **サービスアカウント** を作成して JSON キーをダウンロード
4. `credentials.json` としてプロジェクトルートに配置
5. スプレッドシートのサービスアカウントメールアドレスに**編集権限**を付与

> 読み取り専用（`--dry-run`）の場合、スプレッドシートが公開されていれば認証不要です。

## 使い方

### 画像から直接更新

```bash
# ポケモン価格更新（ほむら東京の画像）
python main.py -i homura_pokemon.png -g pokemon

# ワンピース価格更新
python main.py -i homura_onepiece.jpg -g onepiece

# ドラゴンボール価格更新（マッチョ買取）
python main.py -i macho_dragonball.png -g dragonball

# 複数画像を指定（BOXとカートン別画像の場合）
python main.py -i image1.png -i image2.png -g onepiece
```

### JSONファイルから更新（推奨）

画像解析済みのJSONを `data/` フォルダに保存しておき、JSONから直接読み込む方法です。
**日付はスプレッドシート上で手動更新してください。**

```bash
# ほむら東京・ポケモン
python main.py --from-json data/homura_3_9_pokemon.json -g pokemon --yes

# ほむら東京・ワンピース
python main.py --from-json data/homura_3_9_onepiece.json -g onepiece --yes

# ほむら東京・ドラゴンボール
python main.py --from-json data/homura_3_9_dragonball.json -g dragonball --yes

# ほむら東京・遊戯王
python main.py --from-json data/homura_3_9_yugioh.json -g yugioh --yes

# マッチョ買取・ワンピース
python main.py --from-json data/macho_3_9_onepiece.json -g onepiece --yes
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
-i, --image       競合価格表画像ファイルパスまたはURL（複数指定可）
--from-json       解析済みJSONファイルから読み込む（画像解析をスキップ）
-g, --game        カードゲーム種別（pokemon / onepiece / dragonball / yugioh）[必須]
-d, --date        競合価格の日付（例: 3/9）※手動更新のため通常省略
-c, --competitor  競合名（homura / macho）省略時は自動判定
--dry-run         スプレッドシートを更新せず比較レポートのみ出力
-y, --yes         確認プロンプトをスキップして自動更新
--margin-box      BOXマージン上書き（円）
--margin-carton   カートンマージン上書き（円）
-o, --output      レポートをファイルに保存（省略時は標準出力）
--spreadsheet-id  スプレッドシートID（環境変数 SPREADSHEET_ID より優先）
```

### ヘルプ

```bash
python main.py --help
```

## 日次更新の手順

1. 競合サイトから価格表画像を取得し、`data/` フォルダに保存（または画像解析してJSONを生成）
2. `--from-json` を使ってスプレッドシートに書き込む
3. スプレッドシート上の**日付セルを手動で更新**する

## レポート形式

```markdown
## ワンピースカード 比較（2026/03/09）
競合: **ほむら東京**

| 商品名 | 型式 | 競合BOX | 弊社BOX | 差額 | 状態 | ...

## ⚠️ 要対応（N商品）
...

## 更新版リスト（ほむら東京 3/9 BOX +200円 / カートン +1,000円）弊社形式
```
商品名	型式	買取単価（BOX）	買取単価（カートン）
ROMANCE DAWN	OP-01	10200	122000
...
```
```

## スプレッドシート構成

各ゲームのタブ（シート）は以下の列構成を前提としています：

| A列 | B列 | C列 | D列 |
|-----|-----|-----|-----|
| 商品名 | 型式 | 価格1（BOX/シュリンクあり） | 価格2（カートン/シュリンクなし） |

> 1行目はヘッダー行として扱います。

## スプレッドシート ID

`1PBMNNYHliomlgeNsvZgiccrfOWpIJbYPb9EMFtSAgdw`

## 注意事項

- 画像解析には `ANTHROPIC_API_KEY` が必要です
- スプレッドシートへの書き込みには `credentials.json` が必要です
- 日付はスプレッドシート上で**手動更新**してください（`-d` オプションは原則省略）
- 大幅な価格変動（±5,000円以上）は ⚡ マークで強調表示されます
- 新規商品は 🆕 マークで表示されシートの末尾に追加されます
