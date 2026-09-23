"""The catalogue of checks: one place that the dispatcher, the installer, the
CLI and ``githooks doctor`` all read, so they can never disagree about which
checks exist, which git stage they belong to or which pre-commit hook id they
map to."""

from __future__ import annotations

import importlib
from typing import Callable, NamedTuple

# Git stages this project installs wrappers for, in the order git runs them.
STAGES = ("pre-commit", "prepare-commit-msg", "commit-msg", "pre-push")


class Check(NamedTuple):
    name: str  # name used in .githooks.yaml and GITHOOKS_SKIP
    stage: str  # git stage it is designed for
    module: str  # module in pre_commit_hooks exposing main(argv, stdin_data)
    description: str
    hook_id: str  # id in .pre-commit-hooks.yaml
    takes_files: bool  # accepts file paths (and so `githooks run <check> --all-files`)
    section: str | None  # its settings section in .githooks.yaml


CHECKS: list[Check] = [
    Check(
        "secrets",
        "pre-commit",
        "secrets",
        "Scan staged changes for secrets",
        "githooks-secrets",
        True,
        "secrets",
    ),
    Check(
        "large-files",
        "pre-commit",
        "large_files",
        "Block oversized files, suggest Git LFS",
        "githooks-large-files",
        True,
        "large_files",
    ),
    Check(
        "branch-protect",
        "pre-commit",
        "branch_protect",
        "Block direct commits to main/master",
        "githooks-branch-protect",
        False,
        "branch_protect",
    ),
    Check(
        "format",
        "pre-commit",
        "format_code",
        "Format staged files (ruff/black/prettier)",
        "githooks-format",
        True,
        "format",
    ),
    Check(
        "lint",
        "pre-commit",
        "lint",
        "Lint staged files",
        "githooks-lint",
        True,
        "lint",
    ),
    Check(
        "conventional-commit",
        "commit-msg",
        "conventional_commit",
        "Validate Conventional Commits",
        "githooks-conventional-commit",
        False,
        "conventional_commit",
    ),
    Check(
        "issue-prefix",
        "prepare-commit-msg",
        "prepare_commit_msg",
        "Link the commit to the branch issue id",
        "githooks-issue-prefix",
        False,
        "issue_prefix",
    ),
    Check(
        "secrets-push",
        "pre-push",
        "secrets_push",
        "Scan every pushed commit for secrets (GH013 parity)",
        "githooks-secrets-push",
        False,
        "secrets",
    ),
    Check(
        "no-fixup",
        "pre-push",
        "no_fixup",
        "Block pushing fixup!/squash!/WIP",
        "githooks-no-fixup",
        False,
        "no_fixup",
    ),
    Check(
        "tests",
        "pre-push",
        "run_tests",
        "Run a fast test subset before push",
        "githooks-tests",
        False,
        "tests",
    ),
]

BY_NAME: dict[str, Check] = {c.name: c for c in CHECKS}
NAMES: list[str] = [c.name for c in CHECKS]


def get(name: str) -> Check | None:
    return BY_NAME.get(name)


def entry_point(name: str) -> Callable[..., int]:
    """The ``main(argv, stdin_data=None)`` callable of a check."""
    check = BY_NAME[name]
    module = importlib.import_module(f"{__package__}.{check.module}")
    return module.main


def suggest(name: str, candidates: list[str] | None = None) -> str | None:
    """Closest known name, for "did you mean" messages."""
    import difflib

    matches = difflib.get_close_matches(name, candidates or NAMES, n=1, cutoff=0.6)
    return matches[0] if matches else None
