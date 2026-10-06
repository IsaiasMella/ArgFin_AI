"""Fábrica de la aplicación FastAPI.

Se ejecuta con `uvicorn brujula.main:create_app --factory`. La configuración se valida al
crear la app: si falta una variable obligatoria, el proceso no arranca.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from brujula.core.config import Settings, get_settings
from brujula.core.db import create_engine

# La cola vive en la misma base: está disponible si su esquema existe.
QUEUE_READY_QUERY = text("SELECT to_regclass('procrastinate_jobs') IS NOT NULL")


class HealthResponse(BaseModel):
    status: Literal["ok"]


class ReadyResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    database: bool
    queue: bool


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    is_production = settings.app_env == "production"

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.engine = create_engine(settings)
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

    return app
