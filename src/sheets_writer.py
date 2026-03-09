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
    GAS_WEBHOOK_URL,
)
from src.models import UpdatePayload


class GasWriter:
    """Google Apps Script Webhook 経由でスプレッドシートに書き込むクラス"""

    def __init__(self, webhook_url: str = GAS_WEBHOOK_URL):
        self.webhook_url = webhook_url

    def write_prices(self, game: str, payloads: List[UpdatePayload]) -> int:
        import requests as _req

        sheet_name = SHEET_NAMES.get(game)
        if not sheet_name:
            raise ValueError(f"Unknown game: {game}")

        # 行番号ではなく商品名でマッチングする方式
        updates = []
        for p in payloads:
            if not p.name:
                continue
            update = {"name": p.name}
            if p.new_price_1 is not None:
                update["price1"] = p.new_price_1
            if p.new_price_2 is not None:
                update["price2"] = p.new_price_2
            if len(update) > 1:  # nameだけでなく価格もある場合のみ
                updates.append(update)

        if not updates:
            return 0

        resp = _req.post(
            self.webhook_url,
            json={"sheet": sheet_name, "updates": updates},
            timeout=60,
        )
        resp.raise_for_status()
        result = resp.json()
        if not result.get("ok"):
            raise RuntimeError(f"GAS error: {result}")
        return result.get("count", len(updates))

    def write_date_cell(self, game: str, date_str: str) -> bool:
        """シートのA1セルに日付を書き込む"""
        import requests as _req
        sheet_name = SHEET_NAMES.get(game)
        if not sheet_name:
            return False
        resp = _req.post(
            self.webhook_url,
            json={"sheet": sheet_name, "setCell": {"address": "A1", "value": date_str}},
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()
        return result.get("ok", False)


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
        import google.auth.transport.requests
        import requests as _requests

        creds = service_account.Credentials.from_service_account_file(
            self.credentials_path,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        # requestsベースのトランスポートでトークンを取得
        auth_request = google.auth.transport.requests.Request(
            session=_requests.Session()
        )
        creds.refresh(auth_request)

        # discovery ドキュメントをキャッシュなしで取得
        resp = _requests.get(
            "https://sheets.googleapis.com/$discovery/rest?version=v4",
            headers={"Authorization": f"Bearer {creds.token}"},
            timeout=30,
        )
        resp.raise_for_status()

        from googleapiclient.discovery import build_from_document
        self._service = build_from_document(resp.text, credentials=creds)
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
