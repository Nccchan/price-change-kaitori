"""
Google Sheets GViz API / Sheets API を使って現行価格を読み込むモジュール
"""
import csv
import io
import json
import re
import os
from typing import List, Optional

import requests

from src.config import (
    SPREADSHEET_ID,
    SHEET_NAMES,
    SHEET_COLUMNS,
    SHEET_COLUMNS_OVERRIDE,
    GOOGLE_CREDENTIALS_PATH,
)
from src.models import CardItem, GameType


def _parse_price(value: str) -> Optional[int]:
    """価格文字列を整数に変換する（¥記号・カンマを除去）"""
    if not value or value.strip() in ("", "—", "-", "N/A", "0"):
        return None
    cleaned = re.sub(r"[¥,\s￥]", "", value.strip())
    try:
        v = int(cleaned)
        return v if v > 0 else None
    except ValueError:
        return None


class GVizReader:
    """Google Sheets GViz API で公開スプレッドシートを読み込むクラス"""

    GVIZ_URL = (
        "https://docs.google.com/spreadsheets/d/{spreadsheet_id}"
        "/gviz/tq?tqx=out:csv&sheet={sheet_name}"
    )

    def __init__(self, spreadsheet_id: str = SPREADSHEET_ID):
        self.spreadsheet_id = spreadsheet_id

    def read_sheet(self, game: str) -> List[CardItem]:
        """
        指定ゲームのシートを読み込んで CardItem リストを返す。
        GViz API に失敗した場合は Sheets API (サービスアカウント) にフォールバック。
        """
        sheet_name = SHEET_NAMES.get(game)
        if not sheet_name:
            raise ValueError(f"Unknown game: {game}")

        self._current_cols = SHEET_COLUMNS_OVERRIDE.get(game, SHEET_COLUMNS)

        try:
            return self._read_via_gviz(sheet_name)
        except Exception as gviz_err:
            print(f"  [GViz fallback] {gviz_err} → Sheets API を試みます...")
            try:
                return self._read_via_sheets_api(sheet_name)
            except Exception as api_err:
                raise RuntimeError(
                    f"スプレッドシートの読み込みに失敗しました: {api_err}"
                ) from api_err

    def _read_via_gviz(self, sheet_name: str) -> List[CardItem]:
        url = self.GVIZ_URL.format(
            spreadsheet_id=self.spreadsheet_id,
            sheet_name=requests.utils.quote(sheet_name),
        )
        resp = requests.get(url, timeout=15)
        if resp.status_code == 401:
            raise PermissionError("GViz: スプレッドシートへのアクセスが拒否されました（非公開）")
        resp.raise_for_status()

        rows = list(csv.reader(io.StringIO(resp.text)))
        return self._parse_rows(rows)

    def _read_via_sheets_api(self, sheet_name: str) -> List[CardItem]:
        from googleapiclient.discovery import build
        from google.oauth2 import service_account

        creds_path = GOOGLE_CREDENTIALS_PATH
        if not os.path.exists(creds_path):
            raise FileNotFoundError(
                f"サービスアカウント認証ファイルが見つかりません: {creds_path}\n"
                "credentials.json を配置するか GOOGLE_CREDENTIALS_PATH を設定してください。"
            )

        creds = service_account.Credentials.from_service_account_file(
            creds_path,
            scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
        )
        service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        result = (
            service.spreadsheets()
            .values()
            .get(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{sheet_name}'!A:D",
            )
            .execute()
        )
        rows = result.get("values", [])
        return self._parse_rows(rows)

    def _parse_rows(self, rows: list) -> List[CardItem]:
        cols = getattr(self, "_current_cols", SHEET_COLUMNS)
        header_rows = cols["header_rows"]
        name_col = cols["name_col"]
        code_col = cols["code_col"]
        price1_col = cols["price1_col"]
        price2_col = cols["price2_col"]

        items: List[CardItem] = []
        for row_idx, row in enumerate(rows):
            if row_idx < header_rows:
                continue
            if not row:
                continue

            def get_col(idx: int) -> str:
                return row[idx].strip() if idx < len(row) else ""

            name = get_col(name_col)
            code = get_col(code_col)
            if not name and not code:
                continue

            price_1 = _parse_price(get_col(price1_col))
            price_2 = _parse_price(get_col(price2_col))

            items.append(
                CardItem(
                    name=name,
                    code=code,
                    price_1=price_1,
                    price_2=price_2,
                    row_index=row_idx + 1,  # 1始まり
                )
            )
        return items
