"""Registro de prompts versionados (T0.5)."""

from pathlib import Path

import pytest

from brujula.core.llm.prompts import PromptError, PromptRegistry

VALID = """\
descripcion: Clasifica una noticia.
sistema: Sos un clasificador.
usuario: |
  Título: ${titulo}
  Esquema de ejemplo: {"importancia": "alta"}
parametros:
  max_tokens: 256
"""


@pytest.fixture
def registry(tmp_path: Path) -> PromptRegistry:
    (tmp_path / "clasificar_noticia@1.yaml").write_text(VALID, encoding="utf-8")
    (tmp_path / "roto@1.yaml").write_text("descripcion: falta todo\n", encoding="utf-8")
    return PromptRegistry(tmp_path)


def test_carga_y_renderiza_un_prompt(registry: PromptRegistry) -> None:
    prompt = registry.get("clasificar_noticia@1")

    assert prompt.ref == "clasificar_noticia@1"
    assert prompt.params == {"max_tokens": 256}
    # Las llaves de un JSON de ejemplo no se confunden con variables.
    assert prompt.render_user({"titulo": "YPF"}) == (
        'Título: YPF\nEsquema de ejemplo: {"importancia": "alta"}\n'
    )


def test_variable_faltante(registry: PromptRegistry) -> None:
    with pytest.raises(PromptError, match="titulo"):
        registry.get("clasificar_noticia@1").render_user({})


@pytest.mark.parametrize("ref", ["clasificar_noticia", "clasificar_noticia@v1", "../x@1"])
def test_referencia_invalida(registry: PromptRegistry, ref: str) -> None:
    with pytest.raises(PromptError, match="referencia inválida"):
        registry.get(ref)


def test_prompt_inexistente(registry: PromptRegistry) -> None:
    with pytest.raises(PromptError, match="no existe"):
        registry.get("clasificar_noticia@2")


def test_prompt_mal_formado(registry: PromptRegistry) -> None:
    with pytest.raises(PromptError, match="mal formado"):
        registry.get("roto@1")
