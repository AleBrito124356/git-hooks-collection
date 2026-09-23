"""Shared fixtures: isolated git environments and throw-away repositories.

Every git process the tests start -- and every hook git starts in turn -- sees a
private global config and no system config, so the developer's own settings
(core.hooksPath, commit templates, autocrlf, signing) can never leak in.
"""

from __future__ import annotations

import os
import sys

import pytest
from _helpers import _SCRUB, git, write


@pytest.fixture
def git_env(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("home")
    gitconfig = write(
        home / ".gitconfig",
        "[user]\n"
        "\tname = Test User\n"
        "\temail = test@example.com\n"
        "[init]\n"
        "\tdefaultBranch = main\n"
        "[core]\n"
        "\tautocrlf = false\n"
        "[commit]\n"
        "\tgpgsign = false\n"
        "[advice]\n"
        "\tdetachedHead = false\n",
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for var in _SCRUB:
        monkeypatch.delenv(var, raising=False)
    # Hooks run with the interpreter running the tests (it has PyYAML when the
    # dev extra is installed; GITHOOKS_FORCE_MINIYAML=1 switches the parser).
    monkeypatch.setenv("GITHOOKS_PYTHON", sys.executable)
    monkeypatch.setenv("NO_COLOR", "1")
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
