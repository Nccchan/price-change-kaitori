"""
Google Sheets API v4 でスプレッドシートに価格を書き込むモジュール
"""
import os
from typing import List, Optional

from src.config import (
    SPREADSHEET_ID,
    SHEET_NAMES,
    SHEET_COLUMNS,
    GOOGLE_CREDENTIALS_PATH,
)
from src.models import UpdatePayload


def _col_letter(idx: int) -> str:
    """0始まり列インデックスをアルファベット列名に変換（例: 0→A, 26→AA）"""
    result = ""
    idx += 1
    while idx > 0:
        idx, remainder = divmod(idx - 1, 26)
        result = chr(65 + remainder) + result
    return result


class SheetsWriter:
    """Google Sheets API v4 で価格を書き込むクラス"""

    def __init__(
        self,
        spreadsheet_id: str = SPREADSHEET_ID,
        credentials_path: Optional[str] = None,
    ):
        self.spreadsheet_id = spreadsheet_id
        self.credentials_path = credentials_path or GOOGLE_CREDENTIALS_PATH
        self._service = None

    def _get_service(self):
        if self._service is not None:
            return self._service

        if not os.path.exists(self.credentials_path):
            raise FileNotFoundError(
                f"サービスアカウント認証ファイルが見つかりません: {self.credentials_path}\n"
                "credentials.json を配置するか GOOGLE_CREDENTIALS_PATH を設定してください。\n"
                "設定方法: https://cloud.google.com/docs/authentication/getting-started"
            )

        from googleapiclient.discovery import build
        from google.oauth2 import service_account

        creds = service_account.Credentials.from_service_account_file(
            self.credentials_path,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        self._service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        return self._service

    def write_prices(
        self,
        game: str,
        payloads: List[UpdatePayload],
    ) -> int:
        """
        スプレッドシートに価格を一括書き込みする。

        Args:
            game: ゲーム種別
            payloads: 書き込み対象のペイロードリスト

        Returns:
            更新セル数
        """
        sheet_name = SHEET_NAMES.get(game)
        if not sheet_name:
            raise ValueError(f"Unknown game: {game}")

        price1_col = _col_letter(SHEET_COLUMNS["price1_col"])
        price2_col = _col_letter(SHEET_COLUMNS["price2_col"])
        name_col = _col_letter(SHEET_COLUMNS["name_col"])
        code_col = _col_letter(SHEET_COLUMNS["code_col"])

        service = self._get_service()
        data = []

        for payload in payloads:
            row = payload.row_index

            if payload.is_new:
                # 新商品: 名前・型式・価格を全カラム書き込む
                data.append(
                    {
                        "range": f"'{sheet_name}'!{name_col}{row}:{price2_col}{row}",
                        "values": [
                            [
                                payload.name,
                                payload.code,
                                payload.new_price_1 or "",
                                payload.new_price_2 or "",
                            ]
                        ],
                    }
                )
            else:
                # 既存商品: 価格列のみ更新
                if payload.new_price_1 is not None:
                    data.append(
                        {
                            "range": f"'{sheet_name}'!{price1_col}{row}",
                            "values": [[payload.new_price_1]],
                        }
                    )
                if payload.new_price_2 is not None:
                    data.append(
                        {
                            "range": f"'{sheet_name}'!{price2_col}{row}",
                            "values": [[payload.new_price_2]],
                        }
                    )

        if not data:
            return 0

        body = {
            "valueInputOption": "USER_ENTERED",
            "data": data,
        }
        result = (
            service.spreadsheets()
            .values()
            .batchUpdate(spreadsheetId=self.spreadsheet_id, body=body)
            .execute()
        )
        return result.get("totalUpdatedCells", 0)

    def get_next_available_row(self, game: str) -> int:
        """
        指定ゲームシートの最終データ行の次の行番号を返す（新商品追加用）。
        """
        sheet_name = SHEET_NAMES.get(game)
        if not sheet_name:
            raise ValueError(f"Unknown game: {game}")

        service = self._get_service()
        result = (
            service.spreadsheets()
            .values()
            .get(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{sheet_name}'!A:A",
            )
            .execute()
        )
        values = result.get("values", [])
        return len(values) + 1
