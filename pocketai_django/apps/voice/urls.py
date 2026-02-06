from __future__ import annotations

from django.urls import path

from apps.voice import views_twilio

app_name = "voice"

urlpatterns = [
    # Phase 1 Twilio webhooks (production-shaped)
    path("twilio/twiml/<uuid:session_id>/", views_twilio.twilio_twiml, name="twilio-twiml"),
    path("twilio/consent/<uuid:session_id>/", views_twilio.twilio_consent, name="twilio-consent"),
    path("twilio/status/<uuid:session_id>/", views_twilio.twilio_status, name="twilio-status"),
    path("twilio/recording/<uuid:session_id>/", views_twilio.twilio_recording_callback, name="twilio-recording"),
]
