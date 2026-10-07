"""Entrada del proceso worker: ejecuta las tareas de la cola (Procrastinate).

Procrastinate instala sus propios manejadores de SIGTERM/SIGINT: al recibir la señal
termina las tareas en curso y sale.
"""

import asyncio
import sys

import structlog

# Registran sus tareas en la cola al importarse.
import brujula.features.documents.tasks
import brujula.features.prices.tasks  # noqa: F401
from brujula.core.config import Settings, get_settings
from brujula.core.logging import configure_logging
from brujula.core.queue import create_queue_app
from brujula.core.security.encrypted_types import cipher_from_settings, configure_field_cipher

logger = structlog.get_logger(__name__)


async def run(settings: Settings) -> None:
    app = create_queue_app(settings)
    async with app.open_async():
        logger.info("worker_iniciado", entorno=settings.app_env)
        await app.run_worker_async()
    logger.info("worker_detenido")


def main() -> None:
    settings = get_settings()
    configure_logging(settings)
    configure_field_cipher(cipher_from_settings(settings))
    # psycopg asíncrono no funciona con el event loop por defecto de Windows (Proactor).
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    asyncio.run(run(settings), loop_factory=loop_factory)


if __name__ == "__main__":
    main()
