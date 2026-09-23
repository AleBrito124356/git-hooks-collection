"""Unit tests for the installer's config rendering and rev pinning."""

from __future__ import annotations

from pre_commit_hooks import _core, _miniyaml, installer, registry

TEMPLATE = _core.TEMPLATE_PATH.read_text(encoding="utf-8")


def _parse(text):
    return _miniyaml.safe_load(text)


def test_full_selection_renders_the_template_verbatim():
    assert installer.render_config(list(registry.NAMES)) == TEMPLATE
    assert installer.hooks_block(list(registry.NAMES)) in TEMPLATE


def test_narrow_selection_only_changes_the_hooks_block():
    rendered = installer.render_config(["secrets", "no-fixup"])
    data, template = _parse(rendered), _parse(TEMPLATE)
    assert data["hooks"] == {
        "pre-commit": ["secrets"],
        "prepare-commit-msg": [],
        "commit-msg": [],
        "pre-push": ["no-fixup"],
    }
    data.pop("hooks")
    template.pop("hooks")
    assert data == template


def test_replace_hooks_block_keeps_custom_thresholds_and_comments():
    # Regression: re-installing with --hooks kept the old hooks block, so every
    # previously enabled check stayed active.
    custom = TEMPLATE.replace("max_bytes: 5242880", "max_bytes: 123").replace(
        "# 5 MiB hard limit", "# team override"
    )
    updated = installer.replace_hooks_block(custom, ["secrets"])
    assert "max_bytes: 123" in updated
    assert "# team override" in updated
    assert _parse(updated)["hooks"]["pre-commit"] == ["secrets"]
    assert _parse(updated)["hooks"]["pre-push"] == []


def test_replace_hooks_block_inserts_when_missing():
    updated = installer.replace_hooks_block("version: 1\n\nsecrets:\n  scan: diff\n", ["lint"])
    data = _parse(updated)
    assert data["hooks"]["pre-commit"] == ["lint"]
    assert data["secrets"] == {"scan": "diff"}


def test_wrapper_script_resolves_python_without_pythonpath():
    script = installer.wrapper_script("pre-commit")
    assert "PYTHONPATH" not in script
    assert 'exec "$PY" "$here/githooks-run.py" pre-commit "$@"' in script
    assert "GITHOOKS_PYTHON" in script


def test_pre_commit_snippet_never_pins_a_made_up_tag():
    # Regression: the snippet pinned v0.1.0, a tag that never existed.
    rev, explanation, _warnings = installer.resolve_rev()
    snippet = installer.pre_commit_snippet(["secrets"], rev)
    assert f"rev: {rev}" in snippet
    assert "v0.1.0" not in snippet
    assert explanation


def test_resolve_rev_from_a_checkout_is_its_head(tmp_path):
    import subprocess

    head = subprocess.run(
        ["git", "-C", str(installer.SOURCE_DIR), "rev-parse", "HEAD"],
        stdout=subprocess.PIPE,
        encoding="utf-8",
        check=False,
    ).stdout.strip()
    tag = subprocess.run(
        ["git", "-C", str(installer.SOURCE_DIR), "describe", "--tags", "--exact-match", "HEAD"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        encoding="utf-8",
        check=False,
    ).stdout.strip()
    rev, _explanation, _warnings = installer.resolve_rev()
    assert rev == (tag or head)
