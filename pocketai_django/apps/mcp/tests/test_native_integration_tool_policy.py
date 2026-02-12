from __future__ import annotations

from django.test import TestCase

from apps.accounts.constants import FEATURE_FLAG_METADATA_KEY
from apps.accounts.models import (
    AgentProfile,
    BusinessProfile,
    EmailAccountProvider,
    EmailAccountStatus,
    IntegrationAccountStatus,
    IntegrationProvider,
    IntegrationType,
    McpConnectionApprovalMode,
    RegistrationSession,
    User,
)
from apps.integrations.models import (
    EmailAccount,
    IntegrationAccount,
)
from apps.conversations.models import Conversation
from apps.mcp.orchestrator import McpOrchestratorService


class _ToolRecordingProvider:
    def __init__(self) -> None:
        self.tool_name_sets: list[set[str]] = []

    def chat(
        self,
        messages,
        *,
        tools=None,
        on_stream_delta=None,
        on_reasoning_delta=None,
        on_tool_call_start=None,
        on_tool_call_delta=None,
        response_format=None,
        should_cancel=None,
    ):
        if tools is not None:
            names: set[str] = set()
            for tool_def in tools:
                name = (tool_def or {}).get("function", {}).get("name")
                if isinstance(name, str) and name:
                    names.add(name)
            self.tool_name_sets.append(names)
        return {"message": {"role": "assistant", "content": "ok"}}


class NativeIntegrationExposureTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create(email="native-exposure@example.com", first_name="Native")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Native Exposure Co",
            industry="software",
            metadata={FEATURE_FLAG_METADATA_KEY: {"rag_agentic_mode": True}},
        )
        self.agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.user,
            name="Native Agent",
            role="Assistant",
        )

    def _build_conversation(self, *, actor_user: User | None) -> Conversation:
        metadata = {}
        if actor_user is not None:
            metadata["actor_user_id"] = str(actor_user.id)
        return Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            session_token=f"native-exposure-session-{self.business.conversations.count() + 1}",
            metadata=metadata,
        )

    def test_calendar_tools_hidden_when_not_connected(self) -> None:
        conversation = self._build_conversation(actor_user=self.user)
        provider = _ToolRecordingProvider()
        orchestrator = McpOrchestratorService(agent=self.agent, provider=provider)

        orchestrator.stream_turn(conversation=conversation, user_message="Can you check my calendar?")

        self.assertTrue(provider.tool_name_sets)
        advertised = provider.tool_name_sets[0]
        self.assertNotIn("calendar_list_events", advertised)
        self.assertNotIn("calendar_create_event", advertised)

    def test_calendar_tools_exposed_when_connected_for_actor(self) -> None:
        IntegrationAccount.objects.create(
            business_profile=self.business,
            user=self.user,
            integration_type=IntegrationType.GOOGLE_CALENDAR,
            provider=IntegrationProvider.GOOGLE,
            status=IntegrationAccountStatus.CONNECTED,
            account_identifier="native-exposure@example.com",
            credentials={"access_token": "token"},
        )
        conversation = self._build_conversation(actor_user=self.user)
        provider = _ToolRecordingProvider()
        orchestrator = McpOrchestratorService(agent=self.agent, provider=provider)

        orchestrator.stream_turn(conversation=conversation, user_message="Can you check my calendar?")

        self.assertTrue(provider.tool_name_sets)
        advertised = provider.tool_name_sets[0]
        self.assertIn("calendar_list_events", advertised)
        self.assertIn("calendar_get_event", advertised)
        self.assertIn("calendar_create_event", advertised)
        self.assertIn("calendar_update_event", advertised)

    def test_disabled_calendar_tool_is_hidden_from_exposure(self) -> None:
        IntegrationAccount.objects.create(
            business_profile=self.business,
            user=self.user,
            integration_type=IntegrationType.GOOGLE_CALENDAR,
            provider=IntegrationProvider.GOOGLE,
            status=IntegrationAccountStatus.CONNECTED,
            account_identifier="native-exposure@example.com",
            credentials={"access_token": "token"},
            metadata={"tool_settings": {"calendar_create_event": {"enabled": False}}},
        )
        conversation = self._build_conversation(actor_user=self.user)
        provider = _ToolRecordingProvider()
        orchestrator = McpOrchestratorService(agent=self.agent, provider=provider)

        orchestrator.stream_turn(conversation=conversation, user_message="Can you check my calendar?")

        self.assertTrue(provider.tool_name_sets)
        advertised = provider.tool_name_sets[0]
        self.assertIn("calendar_list_events", advertised)
        self.assertNotIn("calendar_create_event", advertised)

    def test_email_tools_hidden_when_not_connected(self) -> None:
        conversation = self._build_conversation(actor_user=self.user)
        provider = _ToolRecordingProvider()
        orchestrator = McpOrchestratorService(agent=self.agent, provider=provider)

        orchestrator.stream_turn(conversation=conversation, user_message="Search my email inbox.")

        self.assertTrue(provider.tool_name_sets)
        advertised = provider.tool_name_sets[0]
        self.assertNotIn("email_search", advertised)
        self.assertNotIn("email_send_draft", advertised)

    def test_email_tools_exposed_when_connected_for_actor(self) -> None:
        EmailAccount.objects.create(
            business_profile=self.business,
            user=self.user,
            provider=EmailAccountProvider.GOOGLE,
            email_address="native-exposure@example.com",
            status=EmailAccountStatus.CONNECTED,
            credentials={"access_token": "token"},
        )
        conversation = self._build_conversation(actor_user=self.user)
        provider = _ToolRecordingProvider()
        orchestrator = McpOrchestratorService(agent=self.agent, provider=provider)

        orchestrator.stream_turn(conversation=conversation, user_message="Search my email inbox.")

        self.assertTrue(provider.tool_name_sets)
        advertised = provider.tool_name_sets[0]
        self.assertIn("email_search", advertised)
        self.assertIn("email_get_message", advertised)
        self.assertIn("email_send_draft", advertised)

    def test_disabled_email_tool_is_hidden_from_exposure(self) -> None:
        EmailAccount.objects.create(
            business_profile=self.business,
            user=self.user,
            provider=EmailAccountProvider.GOOGLE,
            email_address="native-exposure@example.com",
            status=EmailAccountStatus.CONNECTED,
            credentials={"access_token": "token"},
            metadata={"tool_settings": {"email_send_draft": {"enabled": False}}},
        )
        conversation = self._build_conversation(actor_user=self.user)
        provider = _ToolRecordingProvider()
        orchestrator = McpOrchestratorService(agent=self.agent, provider=provider)

        orchestrator.stream_turn(conversation=conversation, user_message="Search my email inbox.")

        self.assertTrue(provider.tool_name_sets)
        advertised = provider.tool_name_sets[0]
        self.assertIn("email_search", advertised)
        self.assertNotIn("email_send_draft", advertised)


class NativeIntegrationPolicyResolverTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create(email="native-policy@example.com", first_name="Native")
        self.other_user = User.objects.create(email="native-policy-other@example.com", first_name="Other")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Native Policy Co",
            industry="software",
        )
        self.agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.user,
            name="Policy Agent",
            role="Assistant",
        )
        self.conversation = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            session_token="native-policy-session",
            metadata={"actor_user_id": str(self.user.id)},
        )

    def _create_calendar_account(self, *, user: User) -> IntegrationAccount:
        return IntegrationAccount.objects.create(
            business_profile=self.business,
            user=user,
            integration_type=IntegrationType.GOOGLE_CALENDAR,
            provider=IntegrationProvider.GOOGLE,
            status=IntegrationAccountStatus.CONNECTED,
            account_identifier=f"{user.email}",
            credentials={"access_token": "token"},
        )

    def test_resolver_auto_allows_reads(self) -> None:
        self._create_calendar_account(user=self.user)
        self.agent.mcp_default_approval_mode = McpConnectionApprovalMode.AUTO
        self.agent.save(update_fields=["mcp_default_approval_mode"])
        orchestrator = McpOrchestratorService(agent=self.agent, provider=None)

        policy = orchestrator._resolve_native_integration_policy(
            conversation=self.conversation,
            tool_name="calendar_list_events",
            arguments={},
        )

        self.assertEqual(policy.get("decision"), "allow")
        self.assertEqual(policy.get("reason_code"), "allowed")

    def test_resolver_approve_writes_requires_confirmation_for_writes(self) -> None:
        self._create_calendar_account(user=self.user)
        self.agent.mcp_default_approval_mode = McpConnectionApprovalMode.APPROVE_WRITES
        self.agent.save(update_fields=["mcp_default_approval_mode"])
        orchestrator = McpOrchestratorService(agent=self.agent, provider=None)

        write_policy = orchestrator._resolve_native_integration_policy(
            conversation=self.conversation,
            tool_name="calendar_create_event",
            arguments={"summary": "Demo", "start_time": "2026-02-06T10:00:00Z", "end_time": "2026-02-06T10:30:00Z"},
        )
        read_policy = orchestrator._resolve_native_integration_policy(
            conversation=self.conversation,
            tool_name="calendar_list_events",
            arguments={},
        )

        self.assertEqual(write_policy.get("decision"), "allow_with_confirmation")
        self.assertEqual(write_policy.get("reason_code"), "approval_required")
        self.assertEqual(read_policy.get("decision"), "allow")

    def test_resolver_denies_mismatched_explicit_account(self) -> None:
        other_account = self._create_calendar_account(user=self.other_user)
        orchestrator = McpOrchestratorService(agent=self.agent, provider=None)

        policy = orchestrator._resolve_native_integration_policy(
            conversation=self.conversation,
            tool_name="calendar_list_events",
            arguments={"integration_account_id": str(other_account.id)},
        )

        self.assertEqual(policy.get("decision"), "deny")
        self.assertEqual(policy.get("reason_code"), "account_mismatch")

    def test_resolver_denies_when_not_connected(self) -> None:
        orchestrator = McpOrchestratorService(agent=self.agent, provider=None)

        policy = orchestrator._resolve_native_integration_policy(
            conversation=self.conversation,
            tool_name="calendar_list_events",
            arguments={},
        )

        self.assertEqual(policy.get("decision"), "deny")
        self.assertEqual(policy.get("reason_code"), "not_connected")
