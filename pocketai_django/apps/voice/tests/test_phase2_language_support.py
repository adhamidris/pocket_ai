from __future__ import annotations

import os

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from apps.accounts.models import BusinessProfile, RegistrationSession
from apps.voice.models import CallSession, CallStatus, VoiceProviderConnection
from apps.voice.runtime import _detect_text_language, _stt_language_tags_for_session
from apps.voice.twilio import build_twilio_signature
from apps.voice.views_twilio import twilio_consent, twilio_twiml
from core.tenancy import tenant_context


User = get_user_model()


class VoicePhase2LanguageSupportTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._env_before = dict(os.environ)
        os.environ["VOICE_WS_BASE_URL"] = "wss://ws.example.com"
        os.environ["TWILIO_VALIDATE_SIGNATURES"] = "true"
        self.user = User.objects.create_user(
            email="phase2-owner@example.com",
            password="changeme123",
            first_name="Owner",
        )
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Acme Co",
            industry="Retail",
            status="active",
        )
        with tenant_context(self.business.id):
            connection = VoiceProviderConnection(
                business_profile=self.business,
                created_by=self.user,
                provider=VoiceProviderConnection.Provider.TWILIO,
                enabled=True,
            )
            connection.credentials = {
                "account_sid": "AC123",
                "auth_token": "secret",
                "webhook_base_url": "https://example.com",
                "from_number": "+15551234567",
            }
            connection.save()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env_before)
        super().tearDown()

    def test_twilio_twiml_uses_arabic_prompt_for_ar_language(self) -> None:
        session = CallSession.objects.create(
            business_profile=self.business,
            objective="اختبار",
            to_phone_number="+201234567890",
            from_phone_number="+15551234567",
            status=CallStatus.RINGING,
            language="ar",
            country="EG",
        )
        rf = RequestFactory()
        path = f"/voice/twilio/twiml/{session.id}/"
        request = rf.post(path, data={"CallSid": "CA123"})
        url = f"https://example.com{path}"
        signature = build_twilio_signature(url=url, params={"CallSid": "CA123"}, auth_token="secret")
        request.META["HTTP_X_TWILIO_SIGNATURE"] = signature

        response = twilio_twiml(request, session.id)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn('language="ar-EG"', body)
        self.assertIn('voice="Polly.Zeina"', body)
        self.assertIn("سيتم تسجيل هذه المكالمة", body)

    def test_twilio_consent_thanks_is_arabic_for_ar_language(self) -> None:
        session = CallSession.objects.create(
            business_profile=self.business,
            objective="اختبار",
            to_phone_number="+201234567890",
            from_phone_number="+15551234567",
            status=CallStatus.IN_PROGRESS,
            language="ar",
            country="EG",
        )
        rf = RequestFactory()
        path = f"/voice/twilio/consent/{session.id}/"
        request = rf.post(path, data={"Digits": "1"})
        url = f"https://example.com{path}"
        signature = build_twilio_signature(url=url, params={"Digits": "1"}, auth_token="secret")
        request.META["HTTP_X_TWILIO_SIGNATURE"] = signature

        response = twilio_consent(request, session.id)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("شكراً. الرجاء الانتظار.", body)

    def test_runtime_stt_language_tags_for_arabic_include_dual_stream(self) -> None:
        self.assertEqual(
            _stt_language_tags_for_session(language="ar", country="EG", dual_stream_for_arabic=True),
            ["ar-EG", "en"],
        )
        self.assertEqual(
            _stt_language_tags_for_session(language="ar", country="EG", dual_stream_for_arabic=False),
            ["ar-EG"],
        )

    def test_runtime_detect_text_language_prefers_latin_for_english(self) -> None:
        self.assertEqual(_detect_text_language("مرحبا"), "ar")
        self.assertEqual(_detect_text_language("Hello", fallback="ar"), "en")
        self.assertEqual(_detect_text_language("123", fallback="ar"), "ar")
