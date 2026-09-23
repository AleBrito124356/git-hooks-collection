"""Tests for the Conventional Commits validator."""

from __future__ import annotations

from copy import deepcopy

from pre_commit_hooks._core import DEFAULTS
from pre_commit_hooks.conventional_commit import validate

CONFIG = deepcopy(DEFAULTS)


def is_valid(message: str) -> bool:
    return validate(message, CONFIG) == []


# --------------------------------------------------------------------------- #
# Valid messages
# --------------------------------------------------------------------------- #
def test_simple_feat():
    assert is_valid("feat: add retry to the upload client")


def test_fix_with_scope():
    assert is_valid("fix(api): reject empty tenant ids")


def test_breaking_change_marker():
    assert is_valid("refactor(db)!: drop the legacy migrations table")


def test_chore():
    assert is_valid("chore: bump ruff to 0.6")


def test_body_is_ignored():
    message = "feat(auth): add refresh-token rotation\n\nLonger explanation here.\n"
    assert is_valid(message)


def test_comment_lines_are_skipped():
    message = "# this is a git comment\nfix: handle empty input\n"
    assert is_valid(message)


# --------------------------------------------------------------------------- #
# Skipped (auto-generated) messages
# --------------------------------------------------------------------------- #
def test_merge_message_skipped():
    assert is_valid("Merge branch 'main' into feature/x")


def test_revert_message_skipped():
    assert is_valid('Revert "feat: something"')


def test_fixup_message_skipped():
    assert is_valid("fixup! feat: add retry")


# --------------------------------------------------------------------------- #
# Invalid messages
# --------------------------------------------------------------------------- #
def test_empty_message_rejected():
    assert not is_valid("")


def test_unknown_type_rejected():
    errors = validate("wibble: do a thing", CONFIG)
    assert any("not allowed" in e for e in errors)


def test_missing_colon_rejected():
    errors = validate("add a new thing", CONFIG)
    assert any("does not match" in e for e in errors)


def test_missing_space_after_colon_rejected():
    errors = validate("feat:no space", CONFIG)
    assert errors


def test_trailing_period_rejected():
    errors = validate("feat: add a thing.", CONFIG)
    assert any("period" in e for e in errors)


def test_header_too_long_rejected():
    long_subject = "x" * 80
    errors = validate(f"feat: {long_subject}", CONFIG)
    assert any("chars" in e for e in errors)


def test_require_scope_option():
    cfg = deepcopy(DEFAULTS)
    cfg["conventional_commit"]["require_scope"] = True
    errors = validate("feat: no scope here", cfg)
    assert any("scope is required" in e for e in errors)
