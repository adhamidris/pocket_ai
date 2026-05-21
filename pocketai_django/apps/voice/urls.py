from __future__ import annotations

from django.urls import path

from apps.voice.webhooks import telnyx as views_telnyx
from apps.voice.webhooks import twilio as views_twilio

app_name = "voice"

urlpatterns = [
    # Phase 1 Twilio webhooks (production-shaped)
    path("twilio/twiml/<uuid:session_id>/", views_twilio.twilio_twiml, name="twilio-twiml"),
    path("twilio/consent/<uuid:session_id>/", views_twilio.twilio_consent, name="twilio-consent"),
    path("twilio/status/<uuid:session_id>/", views_twilio.twilio_status, name="twilio-status"),
    path("twilio/recording/<uuid:session_id>/", views_twilio.twilio_recording_callback, name="twilio-recording"),
    # Telnyx TeXML webhooks
    path("telnyx/twiml/<uuid:session_id>/", views_telnyx.telnyx_twiml, name="telnyx-twiml"),
    path("telnyx/consent/<uuid:session_id>/", views_telnyx.telnyx_consent, name="telnyx-consent"),
    path("telnyx/status/<uuid:session_id>/", views_telnyx.telnyx_status, name="telnyx-status"),
    path("telnyx/recording/<uuid:session_id>/", views_telnyx.telnyx_recording_callback, name="telnyx-recording"),
]
