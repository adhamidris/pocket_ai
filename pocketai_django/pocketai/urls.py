"""Top-level URL configuration."""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include(("apps.accounts.urls", "accounts"), namespace="accounts")),
    path("", include("frontend.urls")),
    path("api/", include("apps.api.urls")),
]
