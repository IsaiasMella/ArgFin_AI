"""Entrada del proceso worker.

Por ahora solo valida la configuración y espera una señal de parada: la cola de tareas
(Procrastinate) necesita su esquema en la base, que se crea con las migraciones de T0.4.
"""

import logging
import signal
import threading
from types import FrameType

from brujula.core.config import get_settings

logger = logging.getLogger("brujula.worker")


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)

    stop = threading.Event()

    def _request_stop(signum: int, _frame: FrameType | None) -> None:
        logger.info("señal %s recibida, deteniendo el worker", signal.Signals(signum).name)
        stop.set()

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    logger.info("worker iniciado (entorno: %s), sin tareas registradas todavía", settings.app_env)
    stop.wait()
    logger.info("worker detenido")


if __name__ == "__main__":
    main()
