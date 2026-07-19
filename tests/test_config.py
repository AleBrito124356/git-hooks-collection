"""Verify the bundled mini-YAML parser reads the shipped .githooks.yaml.

This is the parser the standalone hooks rely on when PyYAML is not installed, so
it must round-trip the real config file exactly.
"""

from __future__ import annotations

from pathlib import Path

from pre_commit_hooks import _miniyaml

CONFIG_PATH = Path(__file__).resolve().parents[1] / ".githooks.yaml"


def load():
    return _miniyaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def test_version_is_int():
    data = load()
    assert data["version"] == 1
    assert isinstance(data["version"], int)


def test_pre_commit_stage_order():
    data = load()
    assert data["hooks"]["pre-commit"] == [
        "secrets",
        "large-files",
        "branch-protect",
        "format",
        "lint",
    ]


def test_nested_thresholds_are_typed():
    data = load()
    assert data["secrets"]["max_file_bytes"] == 1_000_000
    assert isinstance(data["secrets"]["max_file_bytes"], int)
    assert data["secrets"]["entropy_threshold"] == 3.2
    assert data["large_files"]["max_bytes"] == 5_242_880


def test_block_lists_of_strings():
    data = load()
    assert data["branch_protect"]["protected"] == ["main", "master"]
    assert data["format"]["tools"]["python"] == ["ruff format", "black"]
    assert "feat" in data["conventional_commit"]["types"]


def test_empty_and_quoted_scalars():
    data = load()
    assert data["tests"]["command"] == ""
    assert data["issue_prefix"]["template"] == "[{issue}] "
    assert data["issue_prefix"]["branch_regex"] == r"([A-Z][A-Z0-9]+-\d+)"


def test_bool_scalars():
    data = load()
    assert data["format"]["autostage"] is True
    assert data["lint"]["fail_on_error"] is True
    assert data["conventional_commit"]["require_scope"] is False


def test_empty_flow_list():
    data = load()
    assert data["secrets"]["allow_regex"] == []
