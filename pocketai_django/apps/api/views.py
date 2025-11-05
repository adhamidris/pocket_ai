from __future__ import annotations

from django.http import JsonResponse


def placeholder(_request):
    """Placeholder endpoint to be fleshed out in backend migration."""
    return JsonResponse({"status": "ok", "message": "API scaffold ready"}, status=200)
