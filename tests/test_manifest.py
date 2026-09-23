"""The three public surfaces -- the check registry, .pre-commit-hooks.yaml and the
console scripts in pyproject.toml -- must describe the same set of hooks."""

from __future__ import annotations

import pytest
from _helpers import ROOT

from pre_commit_hooks import _miniyaml, registry

tomllib = pytest.importorskip("tomllib")  # Python 3.11+


def _manifest():
    text = (ROOT / ".pre-commit-hooks.yaml").read_text(encoding="utf-8")
    try:
        import yaml

        return yaml.safe_load(text)
    except ImportError:  # pragma: no cover - the dev extra installs PyYAML
        pytest.skip("PyYAML is needed to read the list-of-mappings manifest")


def _scripts():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["scripts"]


def test_every_check_has_a_pre_commit_hook_with_the_right_stage():
    by_id = {hook["id"]: hook for hook in _manifest()}
    assert set(by_id) == {c.hook_id for c in registry.CHECKS}
    for check in registry.CHECKS:
        hook = by_id[check.hook_id]
        assert hook["stages"] == [check.stage], check.name
        # Message stages get the message file as their "filename".
        expects_files = check.takes_files or check.stage in ("commit-msg", "prepare-commit-msg")
        assert hook["pass_filenames"] is expects_files, check.name
        assert hook["entry"] == check.hook_id


def test_every_hook_entry_is_a_console_script_for_its_module():
    scripts = _scripts()
    for check in registry.CHECKS:
        assert scripts[check.hook_id] == f"pre_commit_hooks.{check.module}:main"
    assert scripts["githooks"] == "pre_commit_hooks.cli:main"


def test_manifest_is_accepted_by_pre_commit():
    clientlib = pytest.importorskip("pre_commit.clientlib")
    assert len(clientlib.load_manifest(str(ROOT / ".pre-commit-hooks.yaml"))) == len(
        registry.CHECKS
    )


def test_template_hooks_block_lists_every_check_in_its_stage():
    data = _miniyaml.safe_load(
        (ROOT / "pre_commit_hooks" / "templates" / "githooks.yaml").read_text(encoding="utf-8")
    )
    for check in registry.CHECKS:
        assert check.name in data["hooks"][check.stage]
