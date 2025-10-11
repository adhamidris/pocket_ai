from __future__ import annotations

"""FastAPI application factory."""

import logging
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.api.v1.routers import auth_router, registration_router
from app.core.logging import REQUEST_ID_CTX_VAR, configure_logging
from app.core.settings import Settings, get_settings

logger = logging.getLogger(__name__)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Ensure every request has an `X-Request-ID` header."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        token = REQUEST_ID_CTX_VAR.set(request_id)
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        finally:
            REQUEST_ID_CTX_VAR.reset(token)
        response.headers["X-Request-ID"] = request_id
        return response


def create_app() -> FastAPI:
    """Application factory."""

    settings = get_settings()
    configure_logging(settings)

    app = FastAPI(title="Pocket AI Backend", version="0.1.0")
    app.state.settings = settings

    _configure_middleware(app, settings)
    _configure_routes(app)
    _configure_exception_handlers(app)

    return app


def _configure_middleware(app: FastAPI, settings: Settings) -> None:
    # Allow both the configured origins AND local development origins
    allowed_origins = list(settings.ALLOWED_ORIGINS) if settings.ALLOWED_ORIGINS else []
    development_origins = [
        "http://localhost:3000",  # Vite default
        "http://127.0.0.1:3000",  # Localhost alternative
        "http://localhost:5173",   # Vite alternate port
        "http://localhost:8080",   # Project dev server
        "http://127.0.0.1:8080",   # Project dev server (explicit IPv4)
    ]
    
    # Combine and deduplicate origins
    final_origins = list(set(allowed_origins + development_origins))
    
    # For debugging, log the final origins
    logger.info("Configuring CORS with origins: %s", final_origins)
    
    # Add CORS middleware first to ensure it processes all requests
    app.add_middleware(
        CORSMiddleware,
        allow_origins=final_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Then add other middleware
    app.add_middleware(RequestIdMiddleware)


def _configure_routes(app: FastAPI) -> None:
    app.include_router(registration_router, prefix="/v1")
    app.include_router(auth_router, prefix="/v1")


def _configure_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def custom_http_exception_handler(request: Request, exc: HTTPException) -> Response:
        return await http_exception_handler(request, exc)

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled application error", extra={"path": request.url.path})
        request_id = getattr(request.state, "request_id", None)
        response = JSONResponse(
            status_code=500,
            content={"detail": "Internal Server Error", "request_id": request_id},
        )
        if request_id:
            response.headers["X-Request-ID"] = request_id
        return response


app = create_app()
