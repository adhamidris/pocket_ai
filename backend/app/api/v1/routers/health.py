"""Health check endpoint."""

from fastapi import APIRouter, Depends

from app.api.v1.deps import get_settings
from app.core.settings import Settings

router = APIRouter(tags=["health"])


@router.get("/healthz", summary="Service health probe")
def health_check(settings: Settings = Depends(get_settings)) -> dict[str, str]:
    """Return service health metadata."""

    return {"status": "ok", "environment": settings.ENV}
