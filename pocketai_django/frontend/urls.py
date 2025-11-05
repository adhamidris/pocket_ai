from django.urls import path

from . import views


app_name = "frontend"

urlpatterns = [
    path("", views.landing, name="landing"),
    path("register/", views.register, name="register"),
    path("privacy/", views.privacy_policy, name="privacy"),
    path("terms/", views.terms_of_service, name="terms"),
    path("<slug:business_slug>/<slug:agent_slug>/", views.chat_portal, name="chat-portal"),
]
