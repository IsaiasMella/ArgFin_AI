"""Logs JSON con contexto (T0.5)."""

import json
import logging

import pytest
import structlog

from brujula.core.config import Settings
from brujula.core.logging import configure_logging


@pytest.fixture(autouse=True)
def _reset_structlog() -> None:
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()


def _json_lines(output: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in output.splitlines() if line.strip()]


def test_structlog_emite_json_con_contexto(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging(settings)
    structlog.contextvars.bind_contextvars(request_id="abc12345")

    structlog.get_logger("brujula.prueba").info("evento_de_prueba", empresa="YPF")

    [line] = _json_lines(capsys.readouterr().out)
    assert line["event"] == "evento_de_prueba"
    assert line["empresa"] == "YPF"
    assert line["request_id"] == "abc12345"
    assert line["level"] == "info"
    assert "timestamp" in line


def test_logs_de_la_libreria_estandar_tambien_salen_en_json(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging(settings)

    logging.getLogger("uvicorn.error").warning("puerto ocupado")

    [line] = _json_lines(capsys.readouterr().out)
    assert line["event"] == "puerto ocupado"
    assert line["logger"] == "uvicorn.error"
