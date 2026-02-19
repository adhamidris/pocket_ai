from __future__ import annotations

from unittest import mock

from django.test import SimpleTestCase

from apps.llm.llm_provider import (
    LLM_RETRY_MAX_ATTEMPTS,
    PromptGenerationError,
    _ProviderRequestError,
    _call_with_retry,
)


class LlmRetryTests(SimpleTestCase):
    @mock.patch("apps.llm.llm_provider.time.sleep", autospec=True)
    def test_retries_once_for_rate_limit_then_succeeds(self, _sleep: mock.MagicMock) -> None:
        attempts = 0

        def _call():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise _ProviderRequestError("rate limited", status_code=429, retryable=True)
            return {"ok": True}

        result = _call_with_retry(
            _call,
            provider="DeepSeekChat",
            model="deepseek-chat",
            operation="chat.completions",
        )
        self.assertEqual(result, {"ok": True})
        self.assertEqual(attempts, 2)
        self.assertEqual(_sleep.call_count, 1)

    @mock.patch("apps.llm.llm_provider.time.sleep", autospec=True)
    def test_raises_after_max_attempts(self, _sleep: mock.MagicMock) -> None:
        attempts = 0

        def _call():
            nonlocal attempts
            attempts += 1
            raise _ProviderRequestError("temporary failure", status_code=503, retryable=True)

        with self.assertRaises(PromptGenerationError):
            _call_with_retry(
                _call,
                provider="OpenAITools",
                model="gpt-4o-mini",
                operation="chat.completions.tools",
            )

        self.assertEqual(attempts, LLM_RETRY_MAX_ATTEMPTS)
        self.assertEqual(_sleep.call_count, max(0, LLM_RETRY_MAX_ATTEMPTS - 1))

    @mock.patch("apps.llm.llm_provider.time.sleep", autospec=True)
    def test_stream_retry_happens_before_output(self, _sleep: mock.MagicMock) -> None:
        attempts = 0
        emitted_output = False

        def _call():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise _ProviderRequestError("stream interrupted", retryable=True)
            return "ok"

        result = _call_with_retry(
            _call,
            provider="DeepSeekTools",
            model="deepseek-chat",
            operation="chat.completions.tools",
            can_retry=lambda: not emitted_output,
        )
        self.assertEqual(result, "ok")
        self.assertEqual(attempts, 2)
        self.assertEqual(_sleep.call_count, 1)

    @mock.patch("apps.llm.llm_provider.time.sleep", autospec=True)
    def test_stream_does_not_retry_after_output(self, _sleep: mock.MagicMock) -> None:
        attempts = 0
        emitted_output = False

        def _call():
            nonlocal attempts, emitted_output
            attempts += 1
            emitted_output = True
            raise _ProviderRequestError("stream interrupted", retryable=True)

        with self.assertRaises(PromptGenerationError):
            _call_with_retry(
                _call,
                provider="DeepSeekTools",
                model="deepseek-chat",
                operation="chat.completions.tools",
                can_retry=lambda: not emitted_output,
            )

        self.assertEqual(attempts, 1)
        self.assertEqual(_sleep.call_count, 0)
