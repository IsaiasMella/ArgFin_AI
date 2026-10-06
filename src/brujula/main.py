"""Fábrica de la aplicación FastAPI.

Se ejecuta con `uvicorn brujula.main:create_app --factory`. La configuración se valida al
crear la app: si falta una variable obligatoria, el proceso no arranca.
"""

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

from brujula.core.config import Settings, get_settings


class HealthResponse(BaseModel):
    status: Literal["ok"]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    is_production = settings.app_env == "production"

    app = FastAPI(
        title="Brújula API",
        # La documentación interactiva no se expone en producción (superficie mínima).
        docs_url=None if is_production else "/docs",
        redoc_url=None,
        openapi_url=None if is_production else "/openapi.json",
    )
    app.state.settings = settings

    @app.get("/health", tags=["operación"])
    async def health() -> HealthResponse:
        """El proceso está vivo. No consulta dependencias (eso es `/ready`, en T0.4)."""
        return HealthResponse(status="ok")

    return app
