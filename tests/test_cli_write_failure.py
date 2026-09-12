import unittest
from unittest.mock import patch
from click.testing import CliRunner
from src.models import CardItem, CompetitorData, GameType, CompetitorType
from main import main


class CliWriteFailureTests(unittest.TestCase):
    def test_failed_supabase_write_cannot_exit_success(self):
        runner = CliRunner()
        data = CompetitorData(GameType.POKEMON, CompetitorType.HOMURA, "2026/09/08",
                              [CardItem("Test expansion", "M5", 10000, 9000)])
        with runner.isolated_filesystem(), \
             patch.dict("os.environ", {"ENABLE_SB_DUAL_WRITE": "1", "KAITORI_WRITE_TO_SHEET": "0", "HOMURA_RESOLVE_ENABLED": "0"}), \
             patch("src.homura_fetcher.HomuraFetcher.fetch", return_value=data), \
             patch("src.supabase_reader.SupabaseReader.read_sheet", return_value=[CardItem("Test expansion", "M5", 10000, 9000, 2)]), \
             patch("src.sheets_writer.SheetsWriter.__init__", return_value=None), \
             patch("src.sheets_writer.SheetsWriter.get_next_available_row", return_value=2), \
             patch("src.sheets_writer.GasWriter.__init__", return_value=None), \
             patch("src.sheets_writer.GasWriter.write_date_cell", return_value=True), \
             patch("src.supabase_writer.write_prices", side_effect=RuntimeError("database unavailable")) as write, \
             patch("src.supabase_writer._notify"), \
             patch("urllib.request.urlopen", side_effect=AssertionError("unexpected network")), \
             patch("requests.sessions.Session.request", side_effect=AssertionError("unexpected network")):
            result = runner.invoke(main, ["--fetch-web", "--competitor", "homura", "-g", "pokemon", "--yes"])
            write.assert_called_once()
            self.assertNotEqual(result.exit_code, 0, result.output)
            self.assertNotIn("✅ 価格更新が完了", result.output)


if __name__ == "__main__":
    unittest.main()
