"""
設定・マスターデータ定数モジュール
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ==============================
# Google Sheets 設定
# ==============================
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "1PBMNNYHliomlgeNsvZgiccrfOWpIJbYPb9EMFtSAgdw")
GOOGLE_CREDENTIALS_PATH = os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json")
GAS_WEBHOOK_URL = os.getenv("GAS_WEBHOOK_URL", "")

# シート名マッピング（ゲーム種別 → スプレッドシートのタブ名）
SHEET_NAMES = {
    "pokemon": "ポケモン",
    "onepiece": "ワンピース",
    "dragonball": "ドラゴンボール",
    "yugioh": "遊戯王",
}

# 各シートの列構成（0始まり）
# A列=商品名, B列=型式, C列=価格1(BOX/シュリンクあり), D列=価格2(カートン/シュリンクなし)
SHEET_COLUMNS = {
    "name_col": 0,    # A
    "code_col": 1,    # B
    "price1_col": 2,  # C
    "price2_col": 3,  # D
    "header_rows": 1, # 先頭1行はヘッダー
}

# ゲーム別列オーバーライド
# dragonball/onepiece/yugioh の列構造:
#   A(0)=空, B(1)=商品名, C(2)=型式, D(3)=BOX価格, E(4)=カートン価格
SHEET_COLUMNS_OVERRIDE = {
    "onepiece":    {**SHEET_COLUMNS, "name_col": 1, "code_col": 2, "price1_col": 3, "price2_col": 4},
    "dragonball":  {**SHEET_COLUMNS, "name_col": 1, "code_col": 2, "price1_col": 3, "price2_col": 4},
    "yugioh":      {**SHEET_COLUMNS, "name_col": 1, "code_col": 2, "price1_col": 3, "price2_col": 4},
}

# ==============================
# マージン設定（円）
# ==============================
MARGINS = {
    "pokemon": {
        "box": int(os.getenv("MARGIN_POKEMON_BOX", "200")),
        "carton": int(os.getenv("MARGIN_POKEMON_CARTON", "200")),
    },
    "onepiece": {
        "box": int(os.getenv("MARGIN_ONEPIECE_BOX", "200")),
        "carton": int(os.getenv("MARGIN_ONEPIECE_CARTON", "2000")),
    },
    "dragonball": {
        "box": int(os.getenv("MARGIN_DRAGONBALL_BOX", "200")),
        "carton": int(os.getenv("MARGIN_DRAGONBALL_CARTON", "1000")),
    },
    "yugioh": {
        "box": int(os.getenv("MARGIN_YUGIOH_BOX", "200")),
        "carton": int(os.getenv("MARGIN_YUGIOH_CARTON", "1000")),
    },
}

# ==============================
# 価格ラベル（レポート表示用）
# ==============================
PRICE_LABELS = {
    "pokemon": {"price1": "シュリンクあり", "price2": "シュリンクなし"},
    "onepiece": {"price1": "BOX", "price2": "カートン"},
    "dragonball": {"price1": "BOX", "price2": "カートン"},
    "yugioh": {"price1": "BOX", "price2": "カートン"},
}

# 大幅変動アラート閾値（円）
LARGE_CHANGE_THRESHOLD = 5000

# ==============================
# 競合設定
# ==============================
DEFAULT_COMPETITORS = {
    "pokemon": "homura",
    "onepiece": "homura",
    "dragonball": "macho",
    "yugioh": "homura",
}

COMPETITOR_NAMES = {
    "homura": "ほむら東京",
    "macho": "マッチョ買取",
    "kaitorihakase": "買取博士",
    "other": "その他",
}

# ==============================
# ポケモンカード マスターデータ（商品名 → 型式）
# ==============================
POKEMON_MASTER = {
    "ニンジャスピナー": "M4",
    "ムニキスゼロ": "M3-box",
    "メガドリームex": "M2a",
    "インフェルノX": "M2",
    "メガブレイブ": "M1L",
    "メガシンフォニア": "M1S",
    "ブラックボルト": "SV11B",
    "ホワイトフレア": "SV11W",
    "ブラックボルトDX": "SV11B-dx",
    "ホワイトフレアDX": "SV11W-dx",
    "ロケット団の栄光": "SV10",
    "熱風のアリーナ": "SV9a",
    "バトルパートナーズ": "SV9",
    "テラスタルフェスex": "SV8a",
    "超電ブレイカー": "SV8",
    "楽園ドラゴーナ": "SV7a",
    "ステラミラクル": "SV7",
    "ナイトワンダラー": "SV6a",
    "変幻の仮面": "SV6",
    "クリムゾンヘイズ": "SV5a",
    "サイバージャッジ": "SV5M",
    "ワイルドフォース": "SV5k",
    "シャイニートレジャーex": "SV4a",
    "古代の咆哮": "SV4K",
    "未来の一閃": "SV4M",
    "レイジングサーフ": "SV3a",
    "黒炎の支配者": "SV3",
    "ポケモンカード151": "SV2a",
    "クレイバースト": "SV2D",
    "スノーハザード": "SV2P",
    "トリプレットビート": "SV1a",
    "スカーレットex": "SV1S",
    "バイオレットex": "SV1V",
    "VSTARユニバース": "S12a",
    "パラダイムトリガー": "S12",
    "白熱のアルカナ": "S11a",
    "ロストアビス": "S11",
    "ポケモンGO": "S10b",
    "ダークファンタズマ": "S10a",
    "タイムゲイザー": "S10D",
    "スペースジャグラー": "S10P",
    "バトルリージョン": "S9a",
    "スターバース": "S9",
    "VMAXクライマックス": "S8b",
    "フュージョンアーツ": "S8",
    "25thアニバーサリー": "S8a",
    "蒼空ストリーム": "S7R",
    "摩天パーフェクト": "S7D",
    "イーブイヒーローズ": "S6a",
    "漆黒のガイスト": "S6K",
    "白銀のランス": "S6H",
    "双璧のファイター": "S5a",
    "連撃マスター": "S5R",
    "一撃マスター": "S5I",
    "伝説の鼓動": "S3a",
    "シャイニースターV": "S4a",
    "仰天のボルテッカー": "S4",
    "ムゲンゾーン": "S3",
    "爆炎ウォーカー": "S2a",
    "反逆クラッシュ": "S2",
    "ソードV": "S1W",
    "シールドV": "S1H",
    "VMAXライジング": "S1a",
    "タッグオールスターズ": "SM12a",
    "オルタージェネシス": "SM12",
    "ドリームリーグ": "SM11b",
    "リミックスバウト": "SM11a",
    "ミラクルツイン": "SM11",
    "スカイレジェンド": "SM10b",
    "ジージーエンド": "SM10a",
    "ダブルブレイズ": "SM10",
    "フルメタルウォール": "SM9b",
    "ナイトユニゾン": "SM9a",
    "タッグボルト": "SM9",
    "GXウルトラシャイニー": "SM8b",
    "ダークオーダー": "SM8a",
    "超爆インパクト": "SM8",
    "フェアリーライズ": "SM7b",
    "迅雷スパーク": "SM7a",
    "禁断の光": "SM6",
    "チャンピオンロード": "SM6b",
    "ドラゴンストーム": "SM6a",
    "ウルトラフォース": "SM5+",
    "ウルトラムーン": "SM5M",
}

# 型式 → 商品名（逆引き）
POKEMON_CODE_TO_NAME = {v: k for k, v in POKEMON_MASTER.items()}

# ==============================
# ワンピースカード マスターデータ
# ==============================
ONEPIECE_MASTER = {
    "OP-01": "ROMANCE DAWN",
    "OP-02": "頂上決戦",
    "OP-03": "強大な敵",
    "OP-04": "謀略の王国",
    "OP-05": "新時代の主役",
    "OP-06": "双璧の覇者",
    "OP-07": "500年後の未来",
    "OP-08": "二つの伝説",
    "OP-09": "新たなる皇帝",
    "OP-10": "王族の血統",
    "OP-11": "神速の拳",
    "OP-12": "師弟の絆",
    "OP-13": "受け継がれる意志",
    "OP-14": "蒼海の七傑",
    "OP-15": "神の島の冒険",
    "PRB-01": "THE BEST vol.1",
    "PRB-02": "THE BEST vol.2",
    "EB-01": "メモリアルコレクション",
    "EB-02": "Anime 25th collection",
    "EB-03": "Heroines Edition",
    "EB-04": "EGGHEAD CRISIS",
}

# 商品名 → 型式（逆引き）
ONEPIECE_NAME_TO_CODE = {v: k for k, v in ONEPIECE_MASTER.items()}

# ==============================
# ドラゴンボールカード マスターデータ
# ==============================
DRAGONBALL_MASTER = {
    "FB-01": "覚醒の鼓動",
    "FB-02": "烈火の闘気",
    "FB-03": "怒りの咆哮",
    "FB-04": "限界を超えし者",
    "FB-05": "未知なる冒険",
    "FB-06": "迫り来る脅威",
    "FB-07": "神龍への願い",
    "FB-08": "誇り高き戦闘民族",
    "SB-01": "MANGA 01",
    "SB-02": "MANGA 02",
}

# 商品名 → 型式（逆引き）
DRAGONBALL_NAME_TO_CODE = {v: k for k, v in DRAGONBALL_MASTER.items()}

# ゲーム別マスターデータ取得
def get_master_data(game: str) -> dict:
    """ゲーム種別に応じたマスターデータを返す（型式 → 商品名）"""
    if game == "pokemon":
        return POKEMON_CODE_TO_NAME
    elif game == "onepiece":
        return ONEPIECE_MASTER
    elif game == "dragonball":
        return DRAGONBALL_MASTER
    return {}
