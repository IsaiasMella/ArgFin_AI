"""El hook de pre-commit debe impedir commitear archivos .env (criterio de T0.1)."""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
PRECOMMIT_CONFIG = ROOT / ".pre-commit-config.yaml"
HOOK_ID = "block-env-files"


def _block_env_hook() -> dict[str, Any]:
    config = yaml.safe_load(PRECOMMIT_CONFIG.read_text(encoding="utf-8"))
    for repo in config["repos"]:
        for hook in repo["hooks"]:
            if hook["id"] == HOOK_ID:
                assert repo["repo"] == "local"
                return dict(hook)
    pytest.fail(f"falta el hook {HOOK_ID!r} en .pre-commit-config.yaml")


@pytest.mark.parametrize(
    "path", [".env", ".env.local", ".env.production", "docker/.env", "a/b/.env.test"]
)
def test_patron_bloquea_archivos_env(path: str) -> None:
    assert re.search(_block_env_hook()["files"], path)


@pytest.mark.parametrize(
    "path", [".env.example", "docker/.env.example", "env.py", "config/.envrc/x.py", "a.env"]
)
def test_patron_permite_otros_archivos(path: str) -> None:
    assert not re.search(_block_env_hook()["files"], path)


def _git(
    repo: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, env=env, check=False
    )


@pytest.mark.skipif(shutil.which("git") is None, reason="git no está instalado")
@pytest.mark.parametrize(
    ("path", "ignorado"), [(".env", True), (".env.local", True), (".env.example", False)]
)
def test_gitignore_excluye_env(path: str, ignorado: bool) -> None:
    # --no-index: evalúa las reglas aunque el archivo no exista ni esté trackeado.
    result = _git(ROOT, "check-ignore", "-q", "--no-index", path)
    assert (result.returncode == 0) is ignorado


@pytest.mark.skipif(shutil.which("git") is None, reason="git no está instalado")
def test_commit_con_env_es_rechazado(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    # Repo aislado de la configuración global/sistema de git del desarrollador.
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "PRE_COMMIT_HOME": str(tmp_path / "pre-commit-cache"),
    }
    config = {"repos": [{"repo": "local", "hooks": [_block_env_hook()]}]}
    (repo / ".pre-commit-config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")

    assert _git(repo, "init", "-q", env=env).returncode == 0
    _git(repo, "config", "user.name", "test", env=env)
    _git(repo, "config", "user.email", "test@example.invalid", env=env)
    install = subprocess.run(
        [sys.executable, "-m", "pre_commit", "install"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert install.returncode == 0, install.stdout + install.stderr

    # Control positivo: .env.example sí se puede commitear.
    (repo / ".env.example").write_text("SECRET=\n", encoding="utf-8")
    _git(repo, "add", ".pre-commit-config.yaml", ".env.example", env=env)
    ok = _git(repo, "commit", "-q", "-m", "ejemplo", env=env)
    assert ok.returncode == 0, ok.stdout + ok.stderr

    # Un .env (aunque se fuerce con `git add -f`) debe ser rechazado.
    (repo / ".env").write_text("SECRET=no-real\n", encoding="utf-8")
    _git(repo, "add", "-f", ".env", env=env)
    blocked = _git(repo, "commit", "-q", "-m", "secreto", env=env)
    assert blocked.returncode != 0
    assert "bloquear archivos .env" in blocked.stdout + blocked.stderr
