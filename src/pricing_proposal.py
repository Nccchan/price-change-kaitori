"""価格proposalの決定的hash・期限・承認証跡。

proposal本体は不変。承認は別ファイル/別レコードとしてproposal hashを参照する。
"""
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
import uuid
from zoneinfo import ZoneInfo


SCHEMA_VERSION = "pricing-proposal-v1"
JST = ZoneInfo("Asia/Tokyo")


class ProposalVerificationError(ValueError):
    pass


def _plain(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(_plain(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _without_hash(proposal: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in proposal.items() if key != "proposal_hash"}


def create_proposal(
    game: str,
    items: Iterable[Dict[str, Any]],
    *,
    run_id: Optional[str] = None,
    created_at: Optional[datetime] = None,
    purpose: str = "price-update",
) -> Dict[str, Any]:
    now = created_at or datetime.now(timezone.utc)
    now = now.astimezone(timezone.utc)
    jst_day = now.astimezone(JST).date()
    normalized_items = sorted(
        [dict(item) for item in items],
        key=lambda item: (item["code"], item["unit"]),
    )
    if not normalized_items:
        raise ValueError("proposal itemsが0件です")
    identities = [(item["code"], item["unit"]) for item in normalized_items]
    if len(identities) != len(set(identities)):
        raise ValueError("proposal内にcode/unit重複があります")
    body = {
        "schema_version": SCHEMA_VERSION,
        "proposal_id": f"{game}-{jst_day.isoformat()}-{uuid.uuid4().hex[:12]}",
        "run_id": run_id or str(uuid.uuid4()),
        "game": game,
        "purpose": purpose,
        "created_at": now.isoformat(),
        "valid_jst_date": jst_day.isoformat(),
        "items": normalized_items,
    }
    body["proposal_hash"] = content_hash(body)
    return body


def verify_proposal(
    proposal: Dict[str, Any],
    expected_hash: Optional[str] = None,
    *,
    now: Optional[datetime] = None,
) -> str:
    if proposal.get("schema_version") != SCHEMA_VERSION:
        raise ProposalVerificationError("proposal schema version不一致")
    actual = content_hash(_without_hash(proposal))
    stored = proposal.get("proposal_hash")
    if not stored or stored != actual:
        raise ProposalVerificationError("proposal hash不一致（内容が変更されています）")
    if expected_hash is not None and expected_hash != actual:
        raise ProposalVerificationError("指定hashとproposal hashが一致しません")
    current = (now or datetime.now(timezone.utc)).astimezone(JST).date().isoformat()
    if proposal.get("valid_jst_date") != current:
        raise ProposalVerificationError("proposalは当日限りです（期限切れ）")
    return actual


def create_approval(
    proposal: Dict[str, Any],
    expected_hash: str,
    approved_by: str,
    *,
    approved_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    verified_hash = verify_proposal(proposal, expected_hash, now=approved_at)
    if not approved_by.strip():
        raise ValueError("approved_byは必須です")
    now = (approved_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {
        "schema_version": "pricing-approval-v1",
        "proposal_id": proposal["proposal_id"],
        "proposal_hash": verified_hash,
        "approved_by": approved_by.strip(),
        "approved_at": now.isoformat(),
    }


def verify_approval(
    proposal: Dict[str, Any], approval: Dict[str, Any], expected_hash: str,
    *, now: Optional[datetime] = None,
) -> None:
    verified_hash = verify_proposal(proposal, expected_hash, now=now)
    if approval.get("schema_version") != "pricing-approval-v1":
        raise ProposalVerificationError("approval schema version不一致")
    if approval.get("proposal_id") != proposal.get("proposal_id"):
        raise ProposalVerificationError("approvalのproposal IDが不一致")
    if approval.get("proposal_hash") != verified_hash:
        raise ProposalVerificationError("approvalのproposal hashが不一致")
    if not approval.get("approved_by") or not approval.get("approved_at"):
        raise ProposalVerificationError("approval証跡が不完全です")


def save_json(path: str, value: Dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_json(path: str) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
