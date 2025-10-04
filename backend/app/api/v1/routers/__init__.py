"""API v1 routers."""

from .health import router as health_router
from .registration import router as registration_router

__all__ = ["health_router", "registration_router"]
