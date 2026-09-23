"""Integration fixtures: a hooked repository per test, and a YAML-backend switch."""

from __future__ import annotations

import shutil

import pytest
from _helpers import git, isolated_env, write
from _repo import BASE_CONFIG, HookedRepo, install


@pytest.fixture(scope="session")
def hooked_template(tmp_path_factory):
    """Build the installed repository once; every test works on a copy."""
    base = tmp_path_factory.mktemp("template")
    env = isolated_env(tmp_path_factory.mktemp("template-home"))
    path, remote = base / "work", base / "remote.git"
    path.mkdir()
    git(base, "init", "-q", "--bare", "-b", "main", str(remote), env=env)
    git(path, "init", "-q", "-b", "main", env=env)
    # Relative URL, so a copied work tree talks to its own copied remote.
    git(path, "remote", "add", "origin", "../remote.git", env=env)
    result = install(path, "--all", env=env)
    assert result.rc == 0, result.out
    HookedRepo(path, remote).config(BASE_CONFIG)
    write(path / "README.md", "project\n")
    git(path, "add", "-A", env=env)
    git(path, "commit", "-q", "--no-verify", "-m", "chore: init with hooks", env=env)
    git(path, "push", "-q", "--no-verify", "-u", "origin", "main", env=env)
    git(path, "switch", "-q", "-c", "feature/work", env=env)
    return base


@pytest.fixture
def hooked(tmp_path, git_env, hooked_template) -> HookedRepo:
    shutil.copytree(hooked_template / "remote.git", tmp_path / "remote.git")
    shutil.copytree(hooked_template / "work", tmp_path / "work")
    return HookedRepo(tmp_path / "work", tmp_path / "remote.git")


@pytest.fixture(params=["pyyaml", "miniyaml"])
def yaml_backend(request, monkeypatch):
    """Run a test once per config parser the hooks can end up using."""
    if request.param == "pyyaml":
        pytest.importorskip("yaml")
        monkeypatch.delenv("GITHOOKS_FORCE_MINIYAML", raising=False)
    else:
        monkeypatch.setenv("GITHOOKS_FORCE_MINIYAML", "1")
    return request.param
