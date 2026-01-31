from __future__ import annotations

from django.urls import path

from apps.voice import views_spike
from apps.voice import views_twilio

app_name = "voice"

urlpatterns = [
    # Phase 1 Twilio webhooks (production-shaped)
    path("twilio/twiml/<uuid:session_id>/", views_twilio.twilio_twiml, name="twilio-twiml"),
    path("twilio/consent/<uuid:session_id>/", views_twilio.twilio_consent, name="twilio-consent"),
    path("twilio/status/<uuid:session_id>/", views_twilio.twilio_status, name="twilio-status"),
    path("twilio/recording/<uuid:session_id>/", views_twilio.twilio_recording_callback, name="twilio-recording"),

    # Phase 0 spike endpoints (Twilio webhooks + helper actions)
    path("spike/start/", views_spike.spike_start_call, name="spike-start"),
    path("spike/twiml/<uuid:session_id>/", views_spike.spike_twiml, name="spike-twiml"),
    path("spike/consent/<uuid:session_id>/", views_spike.spike_consent, name="spike-consent"),
    path("spike/status/<uuid:session_id>/", views_spike.spike_status, name="spike-status"),
    path("spike/recording/<uuid:session_id>/", views_spike.spike_recording_callback, name="spike-recording"),
    path("spike/hangup/<uuid:session_id>/", views_spike.spike_hangup, name="spike-hangup"),
]
