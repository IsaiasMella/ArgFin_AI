"""Logs estructurados en JSON (structlog), también para los loggers de la librería estándar.

Cada línea incluye el contexto ligado con `bind_contextvars` (`request_id` en la API,
`job_id` en el worker), así una traza se sigue de punta a punta.
"""

import logging
import sys

import structlog
from structlog.typing import Processor

from brujula.core.config import Settings

# Librerías muy verbosas: solo advertencias para arriba.
QUIET_LOGGERS = ("LiteLLM", "httpx", "httpcore", "langfuse")


def configure_logging(settings: Settings) -> None:
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(ensure_ascii=False),
            ],
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level)

    # Uvicorn y Procrastinate traen sus propios handlers: que pasen por el raíz (JSON).
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "procrastinate"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(max(logging.WARNING, root.level))
