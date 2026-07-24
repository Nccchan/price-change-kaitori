import copy
from datetime import datetime, timedelta, timezone
import unittest

from src.pricing_proposal import (
    ProposalVerificationError,
    create_approval,
    create_proposal,
    verify_approval,
    verify_proposal,
)


NOW = datetime(2026, 7, 24, 1, 0, tzinfo=timezone.utc)


class PricingProposalTests(unittest.TestCase):
    def item(self):
        return {
            "code": "OP-16", "unit": "BOX", "current": 12400, "proposed": 13200,
            "homura_raw": 12000, "runto_raw": 13000, "selected_source": "runto",
            "parser_version": "runto-onepiece-variation-v2.1",
            "variation_json_sha256": "a" * 64,
        }

    def test_hash_is_stable_and_approval_verifies(self):
        proposal = create_proposal("onepiece", [self.item()], run_id="run-1", created_at=NOW)
        actual = verify_proposal(proposal, proposal["proposal_hash"], now=NOW)
        approval = create_approval(proposal, actual, "natsuki", approved_at=NOW)
        verify_approval(proposal, approval, actual, now=NOW)

    def test_any_price_change_invalidates_hash(self):
        proposal = create_proposal("onepiece", [self.item()], created_at=NOW)
        changed = copy.deepcopy(proposal)
        changed["items"][0]["proposed"] += 1
        with self.assertRaisesRegex(ProposalVerificationError, "hash不一致"):
            verify_proposal(changed, proposal["proposal_hash"], now=NOW)

    def test_parser_or_content_hash_change_invalidates_hash(self):
        proposal = create_proposal("onepiece", [self.item()], created_at=NOW)
        for field, value in (("parser_version", "v-next"), ("variation_json_sha256", "b" * 64)):
            changed = copy.deepcopy(proposal)
            changed["items"][0][field] = value
            with self.assertRaises(ProposalVerificationError):
                verify_proposal(changed, proposal["proposal_hash"], now=NOW)

    def test_proposal_expires_on_next_jst_day(self):
        proposal = create_proposal("onepiece", [self.item()], created_at=NOW)
        with self.assertRaisesRegex(ProposalVerificationError, "期限切れ"):
            verify_proposal(proposal, now=NOW + timedelta(days=1))


if __name__ == "__main__":
    unittest.main()
