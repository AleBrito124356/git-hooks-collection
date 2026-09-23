"""Shared fixtures: isolated git environments and throw-away repositories.

Every git process the tests start -- and every hook git starts in turn -- sees a
private global config and no system config, so the developer's own settings
(core.hooksPath, commit templates, autocrlf, signing) can never leak in.
"""

from __future__ import annotations

import os

import pytest
from _helpers import _SCRUB, git, isolated_env, write


@pytest.fixture
def git_env(tmp_path_factory, monkeypatch):
    env = isolated_env(tmp_path_factory.mktemp("home"))
    for var in _SCRUB:
        monkeypatch.delenv(var, raising=False)
    for key, value in env.items():
        if os.environ.get(key) != value:
            monkeypatch.setenv(key, value)
    return os.environ


@pytest.fixture
def repo(tmp_path, git_env, monkeypatch):
    """A fresh repository on ``main`` with one commit; cwd is set to it."""
    from pre_commit_hooks import _core

    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    write(path / "README.md", "hello\n")
    git(path, "add", "README.md")
    git(path, "commit", "-q", "-m", "chore: init")
    monkeypatch.chdir(path)
    monkeypatch.setattr(_core, "_CONFIG_CACHE", None)
    return path
