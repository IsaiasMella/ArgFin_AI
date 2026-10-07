"""Fábrica de la aplicación FastAPI.

Se ejecuta con `uvicorn brujula.main:create_app --factory`. La configuración se valida al
crear la app: si falta una variable obligatoria, el proceso no arranca.
"""

import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Literal
from uuid import uuid4

import httpx2
import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from brujula.core.config import Settings, get_settings
from brujula.core.db import create_engine, create_session_factory
from brujula.core.logging import configure_logging
from brujula.core.security.csrf import CSRF_HEADER, origin_of
from brujula.core.security.encrypted_types import cipher_from_settings, configure_field_cipher
from brujula.core.security.rate_limit import RateLimiter
from brujula.features.auth.oidc import GoogleOIDC
from brujula.features.auth.router import router as auth_router
from brujula.features.auth.service import AuthService

# La cola vive en la misma base: está disponible si su esquema existe.
QUEUE_READY_QUERY = text("SELECT to_regclass('procrastinate_jobs') IS NOT NULL")

REQUEST_ID_HEADER = "X-Request-ID"
# Solo se acepta un request id entrante con formato seguro (evita inyección en los logs).
VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
# Pedidos salientes (Google): si el proveedor no responde, se corta rápido.
OUTBOUND_TIMEOUT_SECONDS = 10.0


class HealthResponse(BaseModel):
    status: Literal["ok"]


class ReadyResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    database: bool
    queue: bool


def create_app(
    settings: Settings | None = None, *, http_transport: httpx2.AsyncBaseTransport | None = None
) -> FastAPI:
    """`http_transport` permite simular proveedores externos (Google) en los tests."""
    settings = settings or get_settings()
    configure_logging(settings)
    configure_field_cipher(cipher_from_settings(settings))
    is_production = settings.app_env == "production"

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.engine = create_engine(settings)
        async with httpx2.AsyncClient(
            transport=http_transport, timeout=OUTBOUND_TIMEOUT_SECONDS
        ) as http:
            app.state.auth_service = AuthService(
                settings, create_session_factory(app.state.engine), GoogleOIDC(settings, http)
            )
            yield
        await app.state.engine.dispose()

    app = FastAPI(
        title="Brújula API",
        lifespan=lifespan,
        # La documentación interactiva no se expone en producción (superficie mínima).
        docs_url=None if is_production else "/docs",
        redoc_url=None,
        openapi_url=None if is_production else "/openapi.json",
    )
    app.state.settings = settings
    app.state.auth_rate_limiter = RateLimiter(settings.rate_limit_auth_per_minute, 60)

    # El frontend llama con cookies desde otro origen (app.): solo ese origen.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin_of(str(settings.web_base_url))],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", CSRF_HEADER],
    )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = incoming if VALID_REQUEST_ID.match(incoming) else uuid4().hex
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response

    @app.get("/health", tags=["operación"])
    async def health() -> HealthResponse:
        """El proceso está vivo. No consulta dependencias."""
        return HealthResponse(status="ok")

    @app.get("/ready", tags=["operación"], responses={503: {"model": ReadyResponse}})
    async def ready(request: Request) -> JSONResponse:
        """La base de datos y la cola de tareas están disponibles."""
        engine: AsyncEngine = request.app.state.engine
        database = queue = False
        try:
            async with engine.connect() as connection:
                database = True
                queue = bool(await connection.scalar(QUEUE_READY_QUERY))
        except (SQLAlchemyError, OSError):
            pass
        body = ReadyResponse(
            status="ready" if database and queue else "not_ready", database=database, queue=queue
        )
        return JSONResponse(body.model_dump(), status_code=200 if body.status == "ready" else 503)

    app.include_router(auth_router)
    return app
