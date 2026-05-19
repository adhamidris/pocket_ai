from __future__ import annotations

from datetime import datetime, timezone as datetime_timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
import random
import time
from typing import Any, Mapping
from urllib.parse import urlencode

import requests

from apps.accounts.models import KnowledgeIssueSeverity
from apps.knowledge.ingestion.contracts import IssuePayload


class AzureDocumentIntelligenceClientMixin:

    def _build_analyze_url(self, *, locale: str | None = None) -> str:
        base_path = self.base_path or "formrecognizer"
        params = {"api-version": self.api_version}
        if locale:
            params["locale"] = locale
        query = urlencode(params)
        return f"{self.endpoint}/{base_path}/documentModels/{self.model}:analyze?{query}"

    @staticmethod
    def _parse_retry_after_seconds(raw_value: Any) -> float | None:
        if raw_value is None:
            return None
        raw = str(raw_value).strip()
        if not raw:
            return None
        try:
            seconds = float(raw)
            if seconds >= 0.0:
                return seconds
        except (TypeError, ValueError):
            pass
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError, OverflowError):
            parsed = None
        if not parsed:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime_timezone.utc)
        delta = (parsed - datetime.now(datetime_timezone.utc)).total_seconds()
        return max(0.0, delta)

    def _retry_delay_seconds(
        self,
        attempt: int,
        *,
        response: Any = None,
    ) -> float:
        backoff = min(
            self.retry_backoff_max_seconds,
            self.retry_backoff_base_seconds * (2 ** max(0, int(attempt) - 1)),
        )
        jitter = random.uniform(0.0, min(0.25, backoff * 0.25))
        delay = backoff + jitter
        if response is not None:
            headers = getattr(response, "headers", None)
            retry_after_value = headers.get("retry-after") if isinstance(headers, Mapping) else None
            retry_after = self._parse_retry_after_seconds(retry_after_value)
            if retry_after is not None:
                delay = max(delay, min(retry_after, self.max_retry_after_seconds))
        return round(max(0.0, delay), 3)

    @classmethod
    def _classify_failure_class(
        cls,
        *,
        status_code: int | None = None,
        exc: Exception | None = None,
    ) -> str:
        if isinstance(exc, requests.Timeout):
            return "timeout"
        if status_code in cls._THROTTLE_HTTP_STATUS:
            return "throttle_retryable"
        if status_code in cls._RETRYABLE_HTTP_STATUS:
            return "throttle_retryable"
        if isinstance(exc, requests.ConnectionError):
            return "throttle_retryable"
        return "hard_failure"

    @staticmethod
    def _append_retry_event(
        meta: dict[str, Any],
        *,
        phase: str,
        attempt: int,
        delay_s: float,
        reason: str,
        status_code: int | None = None,
    ) -> None:
        events = meta.setdefault("retry_events", [])
        if not isinstance(events, list):
            events = []
            meta["retry_events"] = events
        events.append(
            {
                "phase": phase,
                "attempt": int(attempt),
                "delay_s": round(float(delay_s), 3),
                "reason": reason,
                "status_code": status_code,
            }
        )
        if len(events) > 24:
            del events[:-24]

    @staticmethod
    def _finalize_failure_meta(
        meta: dict[str, Any],
        *,
        status: str,
        failure_class: str,
        failure_stage: str,
        failure_reason: str,
        start_time: float,
        failure_status_code: int | None = None,
        last_error: str | None = None,
    ) -> None:
        meta["status"] = status
        meta["failure_class"] = failure_class
        meta["failure_stage"] = failure_stage
        meta["failure_reason"] = failure_reason
        if failure_status_code is not None:
            meta["failure_status_code"] = int(failure_status_code)
        if last_error:
            meta["last_error"] = str(last_error)[:300]
        meta["duration_ms"] = int((time.time() - start_time) * 1000)

    def _analyze_document(self, path: Path) -> tuple[dict[str, Any] | None, list[IssuePayload], dict[str, Any]]:
        issues: list[IssuePayload] = []
        meta: dict[str, Any] = {
            "request_attempts": 0,
            "poll_attempts": 0,
            "poll_http_attempts": 0,
            "retry_events": [],
        }
        if not self.endpoint or not self.key:
            issues.append(
                IssuePayload(
                    code="azure_di_missing",
                    severity=KnowledgeIssueSeverity.INFO.value,
                    description="Azure Document Intelligence credentials are missing; skipping.",
                )
            )
            meta["status"] = "skipped"
            return None, issues, meta

        url = self._build_analyze_url(locale=self.locale)
        headers = {
            "Ocp-Apim-Subscription-Key": self.key,
            "Content-Type": "application/pdf",
        }
        start = time.time()
        response: Any = None
        for attempt in range(1, self.request_max_attempts + 1):
            meta["request_attempts"] = attempt
            try:
                with path.open("rb") as handle:
                    response = requests.post(
                        url,
                        headers=headers,
                        data=handle,
                        timeout=self.timeout_seconds,
                    )
            except requests.RequestException as exc:
                failure_class = self._classify_failure_class(exc=exc)
                retryable = failure_class in {"timeout", "throttle_retryable"}
                if retryable and attempt < self.request_max_attempts:
                    delay = self._retry_delay_seconds(attempt)
                    self._append_retry_event(
                        meta,
                        phase="submit",
                        attempt=attempt,
                        delay_s=delay,
                        reason=f"submit_exception:{exc.__class__.__name__}",
                    )
                    time.sleep(delay)
                    continue
                issues.append(
                    IssuePayload(
                        code="azure_di_request_failed",
                        severity=KnowledgeIssueSeverity.WARNING.value,
                        description=f"Azure DI request failed: {exc}",
                    )
                )
                self._finalize_failure_meta(
                    meta,
                    status=("timeout" if failure_class == "timeout" else "failed"),
                    failure_class=failure_class,
                    failure_stage="submit",
                    failure_reason="request_exception",
                    start_time=start,
                    last_error=str(exc),
                )
                return None, issues, meta

            if response.status_code in {200, 201, 202}:
                break

            failure_class = self._classify_failure_class(status_code=int(response.status_code))
            retryable_status = int(response.status_code) in self._RETRYABLE_HTTP_STATUS
            if retryable_status and attempt < self.request_max_attempts:
                delay = self._retry_delay_seconds(attempt, response=response)
                self._append_retry_event(
                    meta,
                    phase="submit",
                    attempt=attempt,
                    delay_s=delay,
                    reason="submit_http_retry",
                    status_code=int(response.status_code),
                )
                time.sleep(delay)
                continue

            issues.append(
                IssuePayload(
                    code="azure_di_request_error",
                    severity=KnowledgeIssueSeverity.WARNING.value,
                    description=f"Azure DI request error {response.status_code}: {response.text[:200]}",
                )
            )
            self._finalize_failure_meta(
                meta,
                status=("timeout" if failure_class == "timeout" else "failed"),
                failure_class=failure_class,
                failure_stage="submit",
                failure_reason="request_http_error",
                start_time=start,
                failure_status_code=int(response.status_code),
            )
            return None, issues, meta

        operation_url = response.headers.get("operation-location") or response.headers.get("Operation-Location")
        if not operation_url:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            if payload.get("status") == "succeeded" and payload.get("analyzeResult"):
                meta["duration_ms"] = int((time.time() - start) * 1000)
                meta["status"] = "succeeded"
                return payload.get("analyzeResult"), issues, meta
            issues.append(
                IssuePayload(
                    code="azure_di_missing_operation",
                    severity=KnowledgeIssueSeverity.WARNING.value,
                    description="Azure DI response missing operation-location header.",
                )
            )
            self._finalize_failure_meta(
                meta,
                status="failed",
                failure_class="hard_failure",
                failure_stage="submit",
                failure_reason="missing_operation_location",
                start_time=start,
            )
            return None, issues, meta

        poll_headers = {"Ocp-Apim-Subscription-Key": self.key}
        status_payload: dict[str, Any] | None = None
        for poll_attempt in range(1, self.max_polls + 1):
            meta["poll_attempts"] = poll_attempt
            poll_response: Any = None
            for http_attempt in range(1, self.poll_request_max_attempts + 1):
                meta["poll_http_attempts"] = int(meta.get("poll_http_attempts") or 0) + 1
                try:
                    poll_response = requests.get(
                        operation_url,
                        headers=poll_headers,
                        timeout=self.timeout_seconds,
                    )
                except requests.RequestException as exc:
                    failure_class = self._classify_failure_class(exc=exc)
                    retryable = failure_class in {"timeout", "throttle_retryable"}
                    if retryable and http_attempt < self.poll_request_max_attempts:
                        delay = self._retry_delay_seconds(http_attempt)
                        self._append_retry_event(
                            meta,
                            phase="poll",
                            attempt=http_attempt,
                            delay_s=delay,
                            reason=f"poll_exception:{exc.__class__.__name__}",
                        )
                        time.sleep(delay)
                        continue
                    issues.append(
                        IssuePayload(
                            code="azure_di_poll_failed",
                            severity=KnowledgeIssueSeverity.WARNING.value,
                            description=f"Azure DI poll failed: {exc}",
                        )
                    )
                    self._finalize_failure_meta(
                        meta,
                        status=("timeout" if failure_class == "timeout" else "failed"),
                        failure_class=failure_class,
                        failure_stage="poll",
                        failure_reason="poll_exception",
                        start_time=start,
                        last_error=str(exc),
                    )
                    return None, issues, meta

                if poll_response.status_code in {200, 201}:
                    break

                failure_class = self._classify_failure_class(status_code=int(poll_response.status_code))
                retryable_status = int(poll_response.status_code) in self._RETRYABLE_HTTP_STATUS
                if retryable_status and http_attempt < self.poll_request_max_attempts:
                    delay = self._retry_delay_seconds(http_attempt, response=poll_response)
                    self._append_retry_event(
                        meta,
                        phase="poll",
                        attempt=http_attempt,
                        delay_s=delay,
                        reason="poll_http_retry",
                        status_code=int(poll_response.status_code),
                    )
                    time.sleep(delay)
                    continue
                issues.append(
                    IssuePayload(
                        code="azure_di_poll_error",
                        severity=KnowledgeIssueSeverity.WARNING.value,
                        description=f"Azure DI poll error {poll_response.status_code}: {poll_response.text[:200]}",
                    )
                )
                self._finalize_failure_meta(
                    meta,
                    status=("timeout" if failure_class == "timeout" else "failed"),
                    failure_class=failure_class,
                    failure_stage="poll",
                    failure_reason="poll_http_error",
                    start_time=start,
                    failure_status_code=int(poll_response.status_code),
                )
                return None, issues, meta

            if poll_response is None:
                continue
            try:
                status_payload = poll_response.json()
            except ValueError:
                status_payload = None
            if not status_payload:
                time.sleep(self.poll_interval_seconds)
                continue
            status = (status_payload.get("status") or "").lower()
            if status == "succeeded":
                meta["duration_ms"] = int((time.time() - start) * 1000)
                meta["status"] = "succeeded"
                return status_payload.get("analyzeResult"), issues, meta
            if status in {"failed", "error"}:
                issues.append(
                    IssuePayload(
                        code="azure_di_failed",
                        severity=KnowledgeIssueSeverity.WARNING.value,
                        description=f"Azure DI failed: {status_payload.get('error', {})}",
                    )
                )
                self._finalize_failure_meta(
                    meta,
                    status="failed",
                    failure_class="hard_failure",
                    failure_stage="poll",
                    failure_reason="poll_status_failed",
                    start_time=start,
                )
                return None, issues, meta
            time.sleep(self.poll_interval_seconds)

        issues.append(
            IssuePayload(
                code="azure_di_timeout",
                severity=KnowledgeIssueSeverity.WARNING.value,
                description="Azure DI polling timed out.",
            )
        )
        self._finalize_failure_meta(
            meta,
            status="timeout",
            failure_class="timeout",
            failure_stage="poll",
            failure_reason="poll_max_attempts_exceeded",
            start_time=start,
        )
        return None, issues, meta
