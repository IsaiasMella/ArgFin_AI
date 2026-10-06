"""Fixtures compartidas."""

import pytest

from brujula.core.config import Settings, load_settings
from tests.support.env import VALID_ENV


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Entorno controlado: sin variables heredadas de la máquina y sin leer ningún .env."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    for name, value in VALID_ENV.items():
        monkeypatch.setenv(name, value)
    return monkeypatch


@pytest.fixture
def settings(env: pytest.MonkeyPatch) -> Settings:
    return load_settings(env_file=None)
