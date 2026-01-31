import os

from django.test import TestCase

from apps.voice.models import CallSession


class VoiceSpikeTwiMLTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        os.environ["TWILIO_WEBHOOK_BASE_URL"] = "https://example.com"
        os.environ["VOICE_SPIKE_WS_BASE_URL"] = "wss://example.com"

    def test_twiml_requires_consent(self) -> None:
        session = CallSession.objects.create(
            objective="Test objective",
            to_phone_number="+201234567890",
            from_phone_number="+15555555555",
        )
        resp = self.client.post(f"/voice/spike/twiml/{session.id}/")
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode("utf-8")
        self.assertIn("<Gather", body)
        self.assertIn("This call will be recorded", body)

    def test_consent_granted_starts_recording_and_stream(self) -> None:
        session = CallSession.objects.create(
            objective="Test objective",
            to_phone_number="+201234567890",
            from_phone_number="+15555555555",
        )
        resp = self.client.post(f"/voice/spike/consent/{session.id}/", data={"Digits": "1"})
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode("utf-8")
        self.assertIn("<Start>", body)
        self.assertIn("<Record", body)
        self.assertIn(f'<Stream url="wss://example.com/voice/spike/stream/{session.id}"', body)

    def test_consent_denied_hangs_up(self) -> None:
        session = CallSession.objects.create(
            objective="Test objective",
            to_phone_number="+201234567890",
            from_phone_number="+15555555555",
        )
        resp = self.client.post(f"/voice/spike/consent/{session.id}/", data={"Digits": "9"})
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode("utf-8")
        self.assertIn("<Hangup", body)

