# トレーディングカード買取価格更新ツール

競合他社の価格表画像を Claude Vision で解析し、Google Sheets の現行価格と比較・自動更新するツールです。

## 機能

1. **画像解析**: 競合価格表画像を Claude Vision API で解析（ほむら東京・マッチョ買取に対応）
2. **価格比較**: スプレッドシートの現行価格と比較し、マージン状態を評価
3. **差分レポート**: 要対応商品・大幅変動品のレポートを生成
4. **自動更新**: Google Sheets API で価格を一括書き換え

## 対応カードゲーム

| ゲーム | 競合 | BOXマージン | カートンマージン |
|--------|------|------------|----------------|
| ポケモン | ほむら東京 | +200円 | +200円 |
| ワンピース | ほむら東京 | +200円 | +1,000円 |
| ドラゴンボール | マッチョ買取 | +200円 | +1,000円 |

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

### 基本的な使い方

```bash
# ポケモン価格更新（ほむら東京の画像）
python main.py -i homura_pokemon_20260303.png -g pokemon -d 3/3

# ワンピース価格更新
python main.py -i homura_onepiece.jpg -g onepiece -d 3/3

# ドラゴンボール価格更新（マッチョ買取）
python main.py -i macho_dragonball.png -g dragonball -d 3/3
```

### オプション

```bash
# 比較レポートのみ（スプレッドシット更新なし）
python main.py -i image.png -g pokemon --dry-run

# 確認プロンプトをスキップ
python main.py -i image.png -g pokemon --yes

# マージンを一時的に変更
python main.py -i image.png -g onepiece --margin-box 300 --margin-carton 1500

# 複数画像を指定（BOXとカートン別画像の場合）
python main.py -i image1.png -i image2.png -g onepiece -d 3/3

# レポートをファイルに保存
python main.py -i image.png -g pokemon --dry-run -o report.md

# 競合を明示指定
python main.py -i image.png -g dragonball -c macho
```

### ヘルプ

```bash
python main.py --help
```

## レポート形式

```markdown
## ワンピースカード 比較（2026/03/03）
競合: **ほむら東京**

| 商品名 | 型式 | 競合BOX | 弊社BOX | 差額 | 状態 | ...
...

## ⚠️ 要対応（N商品）
...

## 更新版リスト（ほむら東京 3/3 BOX +200円 / カートン +1,000円）弊社形式
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
- 大幅な価格変動（±5,000円以上）は ⚡ マークで強調表示されます
- 新規商品は 🆕 マークで表示されシートの末尾に追加されます
