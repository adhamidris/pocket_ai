"""Top-level URL configuration."""

from django.conf import settings
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include(("apps.accounts.urls", "accounts"), namespace="accounts")),
    path("", include("frontend.urls")),
    path("api/", include("apps.api.urls")),
]

if settings.DEBUG:
    # Place Silk ahead of the frontend catch-all slug route so /silk/** does not hit chat_portal.
    urlpatterns = [path("silk/", include("silk.urls", namespace="silk"))] + urlpatterns
