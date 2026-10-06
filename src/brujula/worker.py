"""Entrada del proceso worker: ejecuta las tareas de la cola (Procrastinate).

Procrastinate instala sus propios manejadores de SIGTERM/SIGINT: al recibir la señal
termina las tareas en curso y sale.
"""

import asyncio
import logging
import sys

from brujula.core.config import Settings, get_settings
from brujula.core.queue import create_queue_app

logger = logging.getLogger("brujula.worker")


async def run(settings: Settings) -> None:
    app = create_queue_app(settings)
    async with app.open_async():
        logger.info("worker iniciado (entorno: %s)", settings.app_env)
        await app.run_worker_async()
    logger.info("worker detenido")


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    # psycopg asíncrono no funciona con el event loop por defecto de Windows (Proactor).
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    asyncio.run(run(settings), loop_factory=loop_factory)


if __name__ == "__main__":
    main()
