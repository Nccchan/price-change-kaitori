"""Telegram updateをallowlistとproposal ID/hashへ結合する純粋ロジック。"""
from datetime import datetime
import re
from typing import Any, Dict, Iterable

from src.pricing_proposal import create_approval, ProposalVerificationError


COMMAND_RE = re.compile(r"^承認\s+(\S+)\s+([0-9a-f]{64})$")


def approve_from_update(
    proposal: Dict[str, Any], update: Dict[str, Any], *,
    allowed_chat_ids: Iterable[str], allowed_user_ids: Iterable[str],
    now: datetime = None,
) -> Dict[str, Any]:
    message = update.get("message") or {}
    chat_id = str((message.get("chat") or {}).get("id", ""))
    user = message.get("from") or {}
    user_id = str(user.get("id", ""))
    if chat_id not in {str(value) for value in allowed_chat_ids}:
        raise PermissionError("Telegram chatがallowlist外です")
    if user_id not in {str(value) for value in allowed_user_ids}:
        raise PermissionError("Telegram userがallowlist外です")
    match = COMMAND_RE.fullmatch((message.get("text") or "").strip())
    if not match:
        raise ValueError("承認コマンド形式が不正です")
    proposal_id, expected_hash = match.groups()
    if proposal_id != proposal.get("proposal_id"):
        raise ProposalVerificationError("承認対象proposal IDが不一致です")
    approval = create_approval(
        proposal, expected_hash, approved_by=f"telegram:{user_id}", approved_at=now,
    )
    approval.update({
        "telegram_chat_id": chat_id,
        "telegram_user_id": user_id,
        "telegram_username": user.get("username"),
        "telegram_message_id": message.get("message_id"),
    })
    return approval
