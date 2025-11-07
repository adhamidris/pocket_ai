from django.urls import path

from . import views
from .chat_portal import (
    bootstrap_session,
    events,
    messages_endpoint,
    resolve_portal_handle,
    stream_send,
    submit_csat,
)

app_name = "api"

urlpatterns = [
    path("health/", views.placeholder, name="health"),
    path("agents/", views.agents_collection, name="agents-list"),
    path("agents/<uuid:agent_id>/", views.agent_detail_view, name="agents-detail"),
    path("agents/<uuid:agent_id>/actions/", views.agent_action_settings_view, name="agents-actions"),
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
    path("chat/portal/resolve/<slug:business_slug>/<slug:agent_slug>/", resolve_portal_handle, name="chat-portal-resolve"),
    path("chat/portal/sessions/", bootstrap_session, name="chat-portal-session"),
    path("chat/messages/", messages_endpoint, name="chat-messages"),
    path("chat/stream/send/", stream_send, name="chat-stream-send"),
    path("chat/events/", events, name="chat-events"),
    path("chat/csat/", submit_csat, name="chat-csat"),
    path("knowledge/documents/", views.knowledge_documents_collection, name="knowledge-documents-list"),
    path("knowledge/documents/<uuid:document_id>/", views.knowledge_document_detail, name="knowledge-documents-detail"),
    path(
        "knowledge/documents/<uuid:document_id>/download/",
        views.knowledge_document_download,
        name="knowledge-documents-download",
    ),
    path("knowledge/documents/scrape/", views.knowledge_document_scrape, name="knowledge-documents-scrape"),
    path("knowledge/documents/preview-csv/", views.knowledge_document_preview_csv, name="knowledge-documents-preview-csv"),
    path("cases/", views.cases_collection, name="cases-collection"),
    path("cases/<uuid:case_id>/", views.case_detail_view, name="cases-detail"),
    path("cases/<uuid:case_id>/history/", views.case_history_view, name="cases-history"),
    path("cases/<uuid:case_id>/messages/", views.case_messages_view, name="cases-messages"),
    path("cases/<uuid:case_id>/notes/", views.case_notes_view, name="cases-notes"),
    path("customers/<uuid:customer_id>/", views.customer_detail_view, name="customers-detail"),
]
