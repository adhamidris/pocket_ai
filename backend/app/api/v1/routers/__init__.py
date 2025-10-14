"""API v1 routers."""

from .auth import router as auth_router
from .customers import router as customers_router
from .registration import router as registration_router
from .portal import router as portal_router
from .agents import router as agents_router

__all__ = ["auth_router", "customers_router", "registration_router", "portal_router", "agents_router"]
