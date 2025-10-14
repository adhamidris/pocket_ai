from __future__ import annotations

"""FastAPI application factory."""

import logging
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response
from app.repositories.errors import RepositoryError

from app.api.v1.routers import auth_router, customers_router, registration_router, portal_router, agents_router
from app.core.logging import REQUEST_ID_CTX_VAR, configure_logging
from app.core.settings import Settings, get_settings
from app.db.session import get_session

logger = logging.getLogger(__name__)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Ensure every request has an `X-Request-ID` header."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Get or create a request id
        incoming = request.headers.get("X-Request-ID")
        request_id = incoming or uuid4().hex
        # Bind to context var for log correlation
        token = REQUEST_ID_CTX_VAR.set(request_id)
        try:
            # Expose on request.state for handlers
            request.state.request_id = request_id
            response = await call_next(request)
            # Mirror request id on the response header
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            # Restore context var
            REQUEST_ID_CTX_VAR.reset(token)

class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log basic request lifecycle information."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start = perf_counter()
        path = request.url.path
        method = request.method
        logger.info("request start", extra={"path": path, "method": method})
        try:
            response = await call_next(request)
            duration_ms = int((perf_counter() - start) * 1000)
            logger.info(
                "request finish",
                extra={"path": path, "method": method, "status_code": response.status_code, "duration_ms": duration_ms},
            )
            return response
        except Exception:
            duration_ms = int((perf_counter() - start) * 1000)
            logger.exception("request error", extra={"path": path, "method": method, "duration_ms": duration_ms})
            raise

class DBTransactionMiddleware(BaseHTTPMiddleware):
    """Manage a DB session per request with commit/rollback semantics."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        session = get_session()
        request.state.db_session = session
        try:
            response = await call_next(request)
            try:
                session.commit()
            except Exception:
                session.rollback()
                raise
            return response
        except Exception:
            try:
                session.rollback()
            except Exception:
                pass
            raise
        finally:
            try:
                session.close()
            except Exception:
                pass
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
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(DBTransactionMiddleware)


def _configure_routes(app: FastAPI) -> None:
    app.include_router(registration_router, prefix="/v1")
    app.include_router(auth_router, prefix="/v1")
    app.include_router(customers_router, prefix="/v1")
    app.include_router(agents_router, prefix="/v1")
    app.include_router(portal_router, prefix="/v1")


def _configure_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def custom_http_exception_handler(request: Request, exc: HTTPException) -> Response:
        return await http_exception_handler(request, exc)

    @app.exception_handler(RepositoryError)
    async def repository_error_handler(request: Request, exc: RepositoryError) -> JSONResponse:
        # Map known repo errors to appropriate HTTP codes (db_timeout -> 503)
        status = 503 if getattr(exc, "code", "") == "db_timeout" else 500
        payload = exc.to_payload() if hasattr(exc, "to_payload") else {"code": "repository_error", "message": str(exc)}
        request_id = getattr(request.state, "request_id", None)
        if request_id:
            payload["request_id"] = request_id
        return JSONResponse(status_code=status, content=payload)

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled application error", extra={"path": request.url.path})
        request_id = getattr(request.state, "request_id", None)
        settings = get_settings()
        payload = {"detail": "Internal Server Error", "request_id": request_id}
        if not settings.is_production:
            payload["error_type"] = exc.__class__.__name__
            payload["error_message"] = str(exc)
        response = JSONResponse(status_code=500, content=payload)
        if request_id:
            response.headers["X-Request-ID"] = request_id
        return response

app = create_app()
