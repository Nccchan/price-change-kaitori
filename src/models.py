"""
データモデル定義
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List


class GameType(str, Enum):
    POKEMON = "pokemon"
    ONEPIECE = "onepiece"
    DRAGONBALL = "dragonball"
    YUGIOH = "yugioh"

    @classmethod
    def from_str(cls, s: str) -> "GameType":
        s = s.lower().strip()
        aliases = {
            "ポケモン": cls.POKEMON,
            "pokemon": cls.POKEMON,
            "ワンピース": cls.ONEPIECE,
            "onepiece": cls.ONEPIECE,
            "one_piece": cls.ONEPIECE,
            "ドラゴンボール": cls.DRAGONBALL,
            "dragonball": cls.DRAGONBALL,
            "dragon_ball": cls.DRAGONBALL,
            "遊戯王": cls.YUGIOH,
            "yugioh": cls.YUGIOH,
        }
        if s in aliases:
            return aliases[s]
        for member in cls:
            if member.value == s:
                return member
        raise ValueError(f"Unknown game type: {s}")


class CompetitorType(str, Enum):
    HOMURA = "homura"
    MACHO = "macho"
    KAITORIHAKASE = "kaitorihakase"
    OTHER = "other"

    @classmethod
    def from_str(cls, s: str) -> "CompetitorType":
        s = s.lower().strip()
        aliases = {
            "ほむら": cls.HOMURA,
            "ほむら東京": cls.HOMURA,
            "homura": cls.HOMURA,
            "homura_tokyo": cls.HOMURA,
            "マッチョ": cls.MACHO,
            "マッチョ買取": cls.MACHO,
            "macho": cls.MACHO,
            "macho_kaitori": cls.MACHO,
            "買取博士": cls.KAITORIHAKASE,
            "kaitorihakase": cls.KAITORIHAKASE,
        }
        if s in aliases:
            return aliases[s]
        return cls.OTHER


@dataclass
class CardItem:
    """スプレッドシートまたは競合データの1商品エントリ"""
    name: str
    code: str
    price_1: Optional[int] = None  # BOX価格 or シュリンクあり価格
    price_2: Optional[int] = None  # カートン価格 or シュリンクなし価格
    row_index: Optional[int] = None  # スプレッドシートの行番号（1始まり）
    locked_1: bool = False  # price_1 が「準備中」で書き換え禁止
    locked_2: bool = False  # price_2 が「準備中」で書き換え禁止

    def display_price(self, price: Optional[int]) -> str:
        if price is None:
            return "—"
        return f"¥{price:,}"


@dataclass
class CompetitorData:
    """競合画像から解析した価格データ"""
    game: GameType
    competitor: CompetitorType
    date: str
    items: List[CardItem] = field(default_factory=list)


@dataclass
class ComparisonResult:
    """現行価格と競合価格の比較結果"""
    name: str
    code: str
    competitor_price_1: Optional[int]
    competitor_price_2: Optional[int]
    current_price_1: Optional[int]
    current_price_2: Optional[int]
    required_margin_1: int
    required_margin_2: int
    is_new: bool = False  # スプレッドシートに存在しない新商品
    locked_1: bool = False  # price_1 が「準備中」で書き換え禁止
    locked_2: bool = False  # price_2 が「準備中」で書き換え禁止

    @property
    def diff_1(self) -> Optional[int]:
        if self.current_price_1 is not None and self.competitor_price_1 is not None:
            return self.current_price_1 - self.competitor_price_1
        return None

    @property
    def diff_2(self) -> Optional[int]:
        if self.current_price_2 is not None and self.competitor_price_2 is not None:
            return self.current_price_2 - self.competitor_price_2
        return None

    @property
    def status_1(self) -> str:
        if self.locked_1:
            return "🔒"
        if self.competitor_price_1 is None:
            return "—"
        if self.diff_1 is None:
            return "⚠️" if not self.is_new else "🆕"
        return "✓" if self.diff_1 >= self.required_margin_1 else "⚠️"

    @property
    def status_2(self) -> str:
        if self.locked_2:
            return "🔒"
        if self.competitor_price_2 is None:
            return "—"
        if self.diff_2 is None:
            return "⚠️" if not self.is_new else "🆕"
        return "✓" if self.diff_2 >= self.required_margin_2 else "⚠️"

    @property
    def recommended_price_1(self) -> Optional[int]:
        if self.locked_1:
            return None
        if self.competitor_price_1 is not None:
            return self.competitor_price_1 + self.required_margin_1
        return None

    @property
    def recommended_price_2(self) -> Optional[int]:
        if self.locked_2:
            return None
        if self.competitor_price_2 is not None:
            return self.competitor_price_2 + self.required_margin_2
        return None

    @property
    def needs_attention(self) -> bool:
        return (
            self.is_new
            or self.status_1 in ("⚠️",)
            or self.status_2 in ("⚠️",)
        )

    @property
    def is_large_change_1(self) -> bool:
        from src.config import LARGE_CHANGE_THRESHOLD
        if self.diff_1 is None:
            return False
        return abs(self.diff_1) >= LARGE_CHANGE_THRESHOLD

    @property
    def is_large_change_2(self) -> bool:
        from src.config import LARGE_CHANGE_THRESHOLD
        if self.diff_2 is None:
            return False
        return abs(self.diff_2) >= LARGE_CHANGE_THRESHOLD


@dataclass
class DailyChangeResult:
    """競合価格の前日比較結果（1商品）"""
    name: str
    code: str
    prev_price_1: Optional[int]
    curr_price_1: Optional[int]
    prev_price_2: Optional[int]
    curr_price_2: Optional[int]

    def _pct(self, prev: Optional[int], curr: Optional[int]) -> Optional[float]:
        if prev and curr:
            return (curr - prev) / prev
        return None

    @property
    def change_pct_1(self) -> Optional[float]:
        return self._pct(self.prev_price_1, self.curr_price_1)

    @property
    def change_pct_2(self) -> Optional[float]:
        return self._pct(self.prev_price_2, self.curr_price_2)

    @property
    def is_significant(self) -> bool:
        """5%以上の変動があるか"""
        return abs(self.change_pct_1 or 0) >= 0.05 or abs(self.change_pct_2 or 0) >= 0.05

    @property
    def changed(self) -> bool:
        return (
            (self.prev_price_1 != self.curr_price_1 and
             (self.prev_price_1 is not None or self.curr_price_1 is not None))
            or
            (self.prev_price_2 != self.curr_price_2 and
             (self.prev_price_2 is not None or self.curr_price_2 is not None))
        )


@dataclass
class UpdatePayload:
    """スプレッドシート更新用データ"""
    row_index: int       # 行番号（1始まり、ヘッダー含む）
    name: str
    code: str
    new_price_1: Optional[int]
    new_price_2: Optional[int]
    is_new: bool = False
