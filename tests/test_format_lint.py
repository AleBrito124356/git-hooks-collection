"""Unit tests for the format and lint hooks' tool planning and staging rules."""

from __future__ import annotations

import os
from copy import deepcopy

import pytest
from _helpers import FAIL_ALWAYS, SQUASH_SPACES, fake_tool, git, write

from pre_commit_hooks import _core, format_code, lint

CONFIG = deepcopy(_core.DEFAULTS)


@pytest.fixture
def tools(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    return bin_dir


def test_web_files_are_not_sent_to_eslint(tools):
    fake_tool(tools, "eslint", FAIL_ALWAYS)
    plan = format_code.plan_tools(["README.md", "a.yaml", "app.js"], CONFIG, "lint")
    assert [(lang, files) for lang, _tool, files in plan] == [("javascript", ["app.js"])]


def test_tool_resolves_to_full_path(tools):
    # Windows npm shims are .cmd files that CreateProcess cannot find by name.
    fake_tool(tools, "fakefmt", SQUASH_SPACES)
    resolved = _core.resolve_tool("fakefmt --flag")
    assert resolved is not None
    assert os.path.isabs(resolved[0]) and resolved[1:] == ["--flag"]
    assert _core.resolve_tool("definitely-not-installed-xyz") is None


def _config(repo, text):
    write(repo / ".githooks.yaml", text)
    _core._CONFIG_CACHE = None


def test_format_never_stages_unstaged_hunks(repo, tools):
    # Regression: the formatter's `git add` swept unstaged lines (including an
    # unscanned secret) into the commit.
    fake_tool(tools, "fakefmt", SQUASH_SPACES)
    _config(repo, "format:\n  tools:\n    python: [fakefmt]\n")
    write(repo / "s.py", "z   =   1\n")
    git(repo, "add", "s.py")
    write(repo / "s.py", "z   =   1\nDEBUG_PASSWORD_NOT_READY = 3\n")
    assert format_code.main([]) == 0
    assert git(repo, "show", ":s.py").stdout == "z   =   1\n"
    assert "DEBUG_PASSWORD_NOT_READY" in (repo / "s.py").read_text(encoding="utf-8")


def test_format_restages_fully_staged_files(repo, tools):
    fake_tool(tools, "fakefmt", SQUASH_SPACES)
    _config(repo, "format:\n  tools:\n    python: [fakefmt]\n")
    write(repo / "t.py", "y   =   2\n")
    git(repo, "add", "t.py")
    assert format_code.main([]) == 0
    assert git(repo, "show", ":t.py").stdout == "y = 2\n"
    assert git(repo, "diff", "--name-only").stdout == ""


def test_format_without_autostage_fails_only_on_real_changes(repo, tools):
    fake_tool(tools, "fakefmt", SQUASH_SPACES)
    _config(repo, "format:\n  autostage: false\n  tools:\n    python: [fakefmt]\n")
    # Partially staged but already formatted: must pass (the old check compared
    # worktree with index and failed on any unstaged hunk).
    write(repo / "c.py", "a = 1\n")
    git(repo, "add", "c.py")
    write(repo / "c.py", "a = 1\nb = 2\n")
    assert format_code.main([]) == 0
    write(repo / "d.py", "d   =   4\n")
    git(repo, "add", "d.py")
    assert format_code.main([]) == 1
    assert git(repo, "show", ":d.py").stdout == "d   =   4\n"


def test_format_under_pre_commit_framework_does_not_stage(repo, tools, monkeypatch):
    fake_tool(tools, "fakefmt", SQUASH_SPACES)
    _config(repo, "format:\n  tools:\n    python: [fakefmt]\n")
    monkeypatch.setenv("PRE_COMMIT", "1")
    write(repo / "t.py", "y   =   2\n")
    git(repo, "add", "t.py")
    assert format_code.main(["t.py"]) == 1
    assert git(repo, "show", ":t.py").stdout == "y   =   2\n"


def test_lint_blocks_only_on_its_language(repo, tools):
    fake_tool(tools, "fakelint", FAIL_ALWAYS)
    _config(repo, "lint:\n  tools:\n    javascript: [fakelint]\n")
    write(repo / "README.md", "# docs\n")
    assert lint.main(["README.md"]) == 0
    write(repo / "app.js", "var x\n")
    assert lint.main(["app.js"]) == 1
