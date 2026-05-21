from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping


@dataclass(frozen=True)
class EmailSendPolicyDecision:
    """
    Decision for whether an email send should require explicit human approval.

    This is intentionally provider-agnostic and meant to be used by Gmail/Graph
    connectors (and any future email tooling).
    """

    requires_approval: bool
    reason: str


def _normalize_token(value: str | None) -> str:
    return (value or "").strip().lower()


def _email_domain(address: str) -> str:
    candidate = _normalize_token(address)
    if "@" not in candidate:
        return ""
    return candidate.split("@", 1)[1].strip()


def evaluate_email_send_policy(
    *,
    auto_send_enabled: bool,
    recipients: Iterable[str],
    sender_email: str | None = None,
    config: Mapping[str, object] | None = None,
) -> EmailSendPolicyDecision:
    """
    Evaluate whether a send should require approval.

    Default-safe behavior:
    - If auto-send is not enabled: always require approval (draft + approval flow).
    - If auto-send is enabled: apply "step-up" approvals for risky recipients.

    Note: v1 policy does NOT hard-block recipients. It only decides whether to
    auto-send vs request approval.
    """

    if not auto_send_enabled:
        return EmailSendPolicyDecision(requires_approval=True, reason="draft_plus_approval_default")

    cfg = dict(config or {})
    deny_domains = {_normalize_token(item) for item in (cfg.get("deny_domains") or []) if isinstance(item, str)}

    sender_domain = _email_domain(sender_email or "")
    recipient_list = [str(r or "").strip() for r in recipients if str(r or "").strip()]
    if not recipient_list:
        return EmailSendPolicyDecision(requires_approval=True, reason="missing_recipients")

    # Step-up: any denylisted domain triggers approval.
    for address in recipient_list:
        domain = _email_domain(address)
        if domain and domain in deny_domains:
            return EmailSendPolicyDecision(requires_approval=True, reason="denylisted_domain")

    # Step-up: external recipients (different domain) trigger approval by default.
    require_for_external = bool(cfg.get("step_up_external_domain", True))
    if require_for_external and sender_domain:
        for address in recipient_list:
            domain = _email_domain(address)
            if domain and domain != sender_domain:
                return EmailSendPolicyDecision(requires_approval=True, reason="external_recipient")

    return EmailSendPolicyDecision(requires_approval=False, reason="auto_send_allowed")

