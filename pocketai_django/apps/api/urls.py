from django.urls import path

from . import views
from .chat_portal import events, send_message, stream_send, submit_csat

app_name = "api"

urlpatterns = [
    path("health/", views.placeholder, name="health"),
    path("register/sessions/", views.start_registration, name="register-start"),
    path(
        "register/sessions/<uuid:session_id>/business/",
        views.update_business_profile,
        name="register-business",
    ),
    path(
        "register/businesses/<uuid:business_id>/agent/",
        views.configure_agent,
        name="register-agent",
    ),
    path(
        "register/businesses/<uuid:business_id>/uploads/",
        views.finalize_uploads,
        name="register-uploads",
    ),
    path("chat/messages/", send_message, name="chat-messages"),
    path("chat/stream/send/", stream_send, name="chat-stream-send"),
    path("chat/events/", events, name="chat-events"),
    path("chat/csat/", submit_csat, name="chat-csat"),
    path("cases/", views.cases_collection, name="cases-collection"),
    path("cases/<uuid:case_id>/", views.case_detail_view, name="cases-detail"),
    path("cases/<uuid:case_id>/history/", views.case_history_view, name="cases-history"),
    path("cases/<uuid:case_id>/messages/", views.case_messages_view, name="cases-messages"),
    path("cases/<uuid:case_id>/notes/", views.case_notes_view, name="cases-notes"),
]
