from __future__ import annotations

from django.contrib.auth.models import AnonymousUser
from django.urls import reverse


class FrontendAuthBoundaryMiddleware:
    """
    Keep Django admin authentication isolated from the Pocket app session.

    When a browser session is authenticated via the admin console we mark it
    with ``auth_entrypoint = "admin"``. For all non-admin requests we surface an
    anonymous user so the frontend never thinks the admin session is a logged-in
    tenant.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        admin_index = reverse("admin:index")
        # Ensure we include trailing slash for prefix comparisons.
        self.admin_prefix = admin_index if admin_index.endswith("/") else f"{admin_index}/"

    def __call__(self, request):
        entrypoint = request.session.get("auth_entrypoint")
        if request.path.startswith(self.admin_prefix):
            return self.get_response(request)

        if getattr(request, "user", None) and request.user.is_authenticated and entrypoint == "admin":
            request.user = AnonymousUser()

        return self.get_response(request)
