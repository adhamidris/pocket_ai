from django.urls import path

from . import views


app_name = "frontend"

urlpatterns = [
    path("", views.landing, name="landing"),
    path("register/", views.register, name="register"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("dashboard/customers/", views.dashboard_customers, name="dashboard-customers"),
    path("dashboard/agents/", views.dashboard_agents, name="dashboard-agents"),
    path("dashboard/leads/", views.dashboard_leads, name="dashboard-leads"),
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
    path("dashboard/cases/", views.dashboard_cases, name="dashboard-cases"),
    path("dashboard/cases/<uuid:case_id>/", views.dashboard_case_detail, name="dashboard-case-detail"),
    path("privacy/", views.privacy_policy, name="privacy"),
    path("terms/", views.terms_of_service, name="terms"),
    path("<slug:business_slug>/<slug:agent_slug>/", views.chat_portal, name="chat-portal"),
]
