from __future__ import annotations

import io
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.api.chat_portal import _business_prefers_mcp
from apps.accounts.models import AgentProfile, BusinessProfile, RegistrationSession
from apps.conversations.models import Conversation, ConversationFile, ConversationFileChunk
from apps.mcp.tools import execute_tool


User = get_user_model()


def _build_test_pdf_bytes(text: str = "Hello from PDF") -> bytes:
    # Keep tests independent from optional PDF generation deps. (We only need a
    # valid PDF containing extractable text.)
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, StreamObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=144)

    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font)
    resources = page.get("/Resources") or DictionaryObject()
    resources[NameObject("/Font")] = DictionaryObject({NameObject("/F1"): font_ref})
    page[NameObject("/Resources")] = resources

    content = StreamObject()
    content._data = f"BT /F1 24 Tf 20 100 Td ({text}) Tj ET".encode("utf-8")
    content_ref = writer._add_object(content)
    page[NameObject("/Contents")] = content_ref

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


@override_settings(SECRET_KEY="test-secret-key")
class ChatPortalFileUploadTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(email="owner@example.com", password="changeme123", first_name="Owner")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Acme Co",
            industry="Retail",
        )
        self.agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.user,
            name="Support AI",
            tone="friendly",
        )
        self.conversation = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            session_token="session_abc",
        )
        self.client.force_login(self.user)

    def test_portal_upload_creates_file_and_chunks_and_downloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with override_settings(MEDIA_ROOT=tmpdir):
                pdf_bytes = _build_test_pdf_bytes("Policy 123")
                uploaded = SimpleUploadedFile("policy.pdf", pdf_bytes, content_type="application/pdf")
                url = reverse("api:chat-portal-files-upload")
                response = self.client.post(url, data={"session_token": self.conversation.session_token, "file": uploaded})
                self.assertEqual(response.status_code, 201)

                payload = response.json()
                file_id = payload["file"]["id"]
                download_url = payload["download_url"]
                download_url_endpoint = reverse("api:chat-portal-files-download-url", args=[file_id])

                self.conversation.refresh_from_db()
                self.assertTrue(bool(self.conversation.metadata.get("mcp_required")))
                # Conversation-level escalation should override any business setting.
                self.business.metadata = {"mcp_orchestrator_enabled": False}
                self.business.save(update_fields=["metadata", "updated_at"])
                self.assertTrue(_business_prefers_mcp(self.business, conversation=self.conversation))

                self.assertTrue(ConversationFile.objects.filter(id=file_id, conversation=self.conversation).exists())
                self.assertGreater(
                    ConversationFileChunk.objects.filter(conversation_file_id=file_id).count(),
                    0,
                )

                # Fetch a fresh signed URL for UI download buttons.
                fresh = self.client.get(f"{download_url_endpoint}?session_token={self.conversation.session_token}")
                self.assertEqual(fresh.status_code, 200)
                fresh_url = fresh.json().get("download_url")
                self.assertTrue(fresh_url)

                download = self.client.get(download_url)
                self.assertEqual(download.status_code, 200)
                self.assertIn("application/pdf", download.get("Content-Type", ""))
                pdf = b"".join(download.streaming_content)
                self.assertTrue(pdf.startswith(b"%PDF"))

    def test_download_rejects_invalid_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with override_settings(MEDIA_ROOT=tmpdir):
                pdf_bytes = _build_test_pdf_bytes("Hello")
                uploaded = SimpleUploadedFile("policy.pdf", pdf_bytes, content_type="application/pdf")
                upload_url = reverse("api:chat-portal-files-upload")
                response = self.client.post(
                    upload_url,
                    data={"session_token": self.conversation.session_token, "file": uploaded},
                )
                self.assertEqual(response.status_code, 201)
                file_id = response.json()["file"]["id"]

                download_url = reverse("api:chat-portal-files-download", args=[file_id])
                bad = self.client.get(f"{download_url}?token=not-a-token")
                self.assertEqual(bad.status_code, 403)

    def test_authenticated_file_actions_support_conversation_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with override_settings(MEDIA_ROOT=tmpdir):
                self.client.force_login(self.user)
                pdf_bytes = _build_test_pdf_bytes("Owned PDF")
                uploaded = SimpleUploadedFile("owned.pdf", pdf_bytes, content_type="application/pdf")
                upload_url = reverse("api:chat-portal-files-upload")
                response = self.client.post(
                    upload_url,
                    data={"conversation_id": str(self.conversation.id), "file": uploaded},
                )
                self.assertEqual(response.status_code, 201)
                file_id = response.json()["file"]["id"]

                refresh_url = reverse("api:chat-portal-files-download-url", args=[file_id])
                fresh = self.client.get(refresh_url, {"conversation_id": str(self.conversation.id)})
                self.assertEqual(fresh.status_code, 200)
                self.assertTrue(fresh.json().get("download_url"))


@override_settings(SECRET_KEY="test-secret-key")
class ChatPortalPdfToolsTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(email="owner2@example.com", password="changeme123", first_name="Owner")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Acme Co",
            industry="Retail",
        )
        self.agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.user,
            name="Support AI",
            tone="friendly",
        )
        self.conversation = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            session_token="session_pdf_tools",
        )

    def test_pdf_generate_creates_artifact_with_download_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with override_settings(MEDIA_ROOT=tmpdir):
                try:
                    import reportlab  # type: ignore
                except Exception:
                    self.skipTest("reportlab not installed")

                result = execute_tool(
                    "pdf_generate",
                    {"content": "Hello world", "title": "Test"},
                    conversation=self.conversation,
                )
                self.assertEqual(result["status"], "ok")
                self.assertIn("download_url", result)
                artifact_id = result["artifact"]["file_id"]
                self.assertTrue(
                    ConversationFile.objects.filter(id=artifact_id, conversation=self.conversation, kind="artifact").exists()
                )

    def test_pdf_merge_creates_merged_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with override_settings(MEDIA_ROOT=tmpdir):
                try:
                    import reportlab  # type: ignore
                except Exception:
                    self.skipTest("reportlab not installed")

                a = execute_tool(
                    "pdf_generate",
                    {"content": "Doc A", "title": "A", "filename": "a.pdf"},
                    conversation=self.conversation,
                )
                b = execute_tool(
                    "pdf_generate",
                    {"content": "Doc B", "title": "B", "filename": "b.pdf"},
                    conversation=self.conversation,
                )
                merged = execute_tool(
                    "pdf_merge",
                    {"file_ids": [a["artifact"]["file_id"], b["artifact"]["file_id"]], "filename": "merged.pdf"},
                    conversation=self.conversation,
                )
                self.assertEqual(merged["status"], "ok")
                self.assertIn("download_url", merged)
