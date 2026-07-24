from datetime import datetime, timezone
import unittest

from src.gross_profit_guard import (
    GrossProfitParameters, max_buyback_jpy, validate_proposal_gross_margin,
)
from src.pricing_proposal import create_proposal
from src.proposal_readback import ReadbackMismatchError, verify_three_way
from src.telegram_proposal_approval import approve_from_update


NOW = datetime(2026, 7, 24, 1, tzinfo=timezone.utc)


class Stage2SafetyTests(unittest.TestCase):
    def proposal(self):
        return create_proposal("onepiece", [{
            "code": "OP-16", "unit": "BOX", "current": 12400, "proposed": 13200,
        }], created_at=NOW)

    def update(self, proposal, user="123", chat="456"):
        return {"message": {"message_id": 9, "chat": {"id": chat},
            "from": {"id": user, "username": "natsuki"},
            "text": f"承認 {proposal['proposal_id']} {proposal['proposal_hash']}"}}

    def test_telegram_requires_both_chat_and_user_allowlist(self):
        proposal = self.proposal()
        approval = approve_from_update(
            proposal, self.update(proposal), allowed_chat_ids={"456"},
            allowed_user_ids={"123"}, now=NOW,
        )
        self.assertEqual(approval["telegram_user_id"], "123")
        with self.assertRaises(PermissionError):
            approve_from_update(proposal, self.update(proposal, user="999"),
                allowed_chat_ids={"456"}, allowed_user_ids={"123"}, now=NOW)

    def test_three_way_readback_mismatch_fails(self):
        proposal = self.proposal()
        verify_three_way(proposal, {("OP-16", "BOX"): 13200}, {("OP-16", "BOX"): 13200})
        with self.assertRaises(ReadbackMismatchError):
            verify_three_way(proposal, {("OP-16", "BOX"): 13200}, {("OP-16", "BOX"): 13199})

    def test_gross_ceiling_and_missing_values_fail_closed(self):
        params = GrossProfitParameters(0.03, 0.02, 0.10, 0.20)
        self.assertEqual(max_buyback_jpy(20000, 1000, params), 12000)
        with self.assertRaisesRegex(ValueError, "未設定"):
            validate_proposal_gross_margin(self.proposal(), params)


if __name__ == "__main__":
    unittest.main()
