from __future__ import annotations

import dataclasses
import uuid
from typing import Iterable, Mapping, Sequence

from apps.accounts.models import BusinessProfile
from apps.conversations.models import Conversation
from apps.services.mcp.identifier_registry import IdentifierGuardrail, IdentifierRegistryService


@dataclasses.dataclass(frozen=True)
class IdentifierEvalCase:
    name: str
    upload_id: uuid.UUID
    provided_identifiers: Mapping[str, str]
    expected_status: str  # ok | identifier_required
    match_policy: str | None = None
    notes: str = ""


@dataclasses.dataclass(frozen=True)
class IdentifierEvalResult:
    case: IdentifierEvalCase
    status: str
    required_keys: Sequence[str]
    provided_keys: Sequence[str]
    match_policy: str

    @property
    def passed(self) -> bool:
        return self.status == self.case.expected_status


class IdentifierEvalError(RuntimeError):
    def __init__(self, failures: Sequence[IdentifierEvalResult]):
        self.failures = failures
        names = ", ".join(result.case.name for result in failures)
        super().__init__(f"Identifier eval failed for: {names}")


class IdentifierEvalHarness:
    """
    Lightweight evaluator to validate identifier guardrail behavior against synthetic cases.
    """

    def __init__(self, *, business_profile: BusinessProfile, enforce: bool = True) -> None:
        self.business = business_profile
        self.enforce = enforce

    def run(self, cases: Iterable[IdentifierEvalCase]) -> list[IdentifierEvalResult]:
        results: list[IdentifierEvalResult] = []
        failures: list[IdentifierEvalResult] = []
        for case in cases:
            if case.match_policy:
                IdentifierRegistryService.set_match_policy(
                    business_profile=self.business, policy=case.match_policy
                )
            conversation = Conversation(
                business_profile=self.business,
                session_token=f"identifier-eval-{case.name}",
                metadata={"customer_identifiers": dict(case.provided_identifiers)},
            )
            guard = IdentifierGuardrail.from_conversation(conversation)
            decision = guard.require_for_upload(str(case.upload_id))
            result = IdentifierEvalResult(
                case=case,
                status=decision.status,
                required_keys=decision.required_keys,
                provided_keys=decision.provided_keys,
                match_policy=decision.match_policy or guard.match_policy,
            )
            results.append(result)
            if not result.passed:
                failures.append(result)
        if self.enforce and failures:
            raise IdentifierEvalError(failures)
        return results
