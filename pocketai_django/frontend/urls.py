from django.urls import path

from . import views
from apps.crm import frontend_views as crm_frontend_views


app_name = "frontend"

urlpatterns = [
    path("", views.landing, name="landing"),
    path("register/", views.register, name="register"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("dashboard/chat/", views.dashboard_chat, name="dashboard-chat"),
    path("dashboard/crm/", crm_frontend_views.dashboard_crm_overview, name="dashboard-crm-overview"),
    path("dashboard/crm/contacts/", crm_frontend_views.dashboard_crm_contacts, name="dashboard-crm-contacts"),
    path("dashboard/crm/contacts/<uuid:contact_id>/", crm_frontend_views.dashboard_crm_contact_detail, name="dashboard-crm-contact-detail"),
    path("dashboard/crm/companies/", crm_frontend_views.dashboard_crm_companies, name="dashboard-crm-companies"),
    path("dashboard/crm/companies/<uuid:company_id>/", crm_frontend_views.dashboard_crm_company_detail, name="dashboard-crm-company-detail"),
    path("dashboard/crm/imports/", crm_frontend_views.dashboard_crm_imports, name="dashboard-crm-imports"),
    path("dashboard/crm/duplicates/", crm_frontend_views.dashboard_crm_duplicates, name="dashboard-crm-duplicates"),
    path("dashboard/crm/fields/", crm_frontend_views.dashboard_crm_fields, name="dashboard-crm-fields"),
    path("dashboard/agents/", views.dashboard_agents, name="dashboard-agents"),
    path("dashboard/knowledge/", views.dashboard_knowledge, name="dashboard-knowledge"),
    path("dashboard/integrations/", views.dashboard_integrations, name="dashboard-integrations"),
    path("dashboard/mcp/", views.dashboard_mcp, name="dashboard-mcp"),
    path("dashboard/controls/", views.dashboard_controls, name="dashboard-controls"),
    path("dashboard/voice/", views.dashboard_voice, name="dashboard-voice"),
    path("dashboard/internal/rag-analytics/", views.dashboard_rag_analytics, name="dashboard-rag-analytics"),
    path("dashboard/knowledge/visualizer/", views.dashboard_knowledge_visualizer, name="dashboard-knowledge-visualizer"),
    path("dashboard/knowledge/upload/", views.dashboard_knowledge_upload, name="dashboard-knowledge-upload"),
    path("dashboard/knowledge/dump/", views.dashboard_knowledge_dump, name="dashboard-knowledge-dump"),
    path(
        "dashboard/knowledge/integrations/google/start/",
        views.dashboard_knowledge_integrations_connect,
        name="dashboard-knowledge-integrations-connect",
    ),
    path("privacy/", views.privacy_policy, name="privacy"),
    path("terms/", views.terms_of_service, name="terms"),
    path("<slug:business_slug>/<slug:agent_slug>/", views.chat_portal, name="chat-portal"),
]
