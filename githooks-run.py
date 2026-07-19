#!/usr/bin/env python3
"""Dispatch the checks configured for a git stage.

This is the single entry point the installed git hooks call. Each generated hook
in ``.githooks/`` is a two-line shell wrapper that runs:

    python3 githooks-run.py <stage> "$@"

``<stage>`` is one of pre-commit, commit-msg, prepare-commit-msg, pre-push. The
runner reads ``.githooks.yaml`` to find which checks are enabled for that stage
and calls them in-process (so, for pre-push, the refs on stdin are read once and
shared across checks). It aggregates results and exits non-zero if any check
failed.

Enabling / disabling a check is a config edit, not a reinstall: change the lists
under ``hooks:`` in ``.githooks.yaml`` and the next commit picks it up.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Make the bundled package importable whether we run from the repo or from a
# vendored .githooks/ directory that install.py populated.
_HERE = Path(__file__).resolve().parent
for candidate in (_HERE, _HERE.parent):
    if (candidate / "pre_commit_hooks" / "__init__.py").is_file():
        sys.path.insert(0, str(candidate))
        break

from pre_commit_hooks import _core  # noqa: E402
from pre_commit_hooks import branch_protect  # noqa: E402
from pre_commit_hooks import conventional_commit  # noqa: E402
from pre_commit_hooks import format_code  # noqa: E402
from pre_commit_hooks import large_files  # noqa: E402
from pre_commit_hooks import lint  # noqa: E402
from pre_commit_hooks import no_fixup  # noqa: E402
from pre_commit_hooks import prepare_commit_msg  # noqa: E402
from pre_commit_hooks import run_tests  # noqa: E402
from pre_commit_hooks import secrets as secrets_mod  # noqa: E402


# Config check-name -> callable(argv, stdin_data) -> int
REGISTRY = {
    "secrets": lambda argv, stdin: secrets_mod.main(argv),
    "large-files": lambda argv, stdin: large_files.main(argv, stdin_data=stdin),
    "branch-protect": lambda argv, stdin: branch_protect.main(argv, stdin_data=stdin),
    "format": lambda argv, stdin: format_code.main(argv, stdin_data=stdin),
    "lint": lambda argv, stdin: lint.main(argv, stdin_data=stdin),
    "conventional-commit": lambda argv, stdin: conventional_commit.main(argv, stdin_data=stdin),
    "issue-prefix": lambda argv, stdin: prepare_commit_msg.main(argv, stdin_data=stdin),
    "no-fixup": lambda argv, stdin: no_fixup.main(argv, stdin_data=stdin),
    "tests": lambda argv, stdin: run_tests.main(argv, stdin_data=stdin),
}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        _core.error("githooks-run.py expects a stage name (e.g. pre-commit)")
        return 2
    stage = argv[0]
    rest = argv[1:]

    config = _core.load_config()
    checks = list(_core.cfg(config, f"hooks.{stage}", []) or [])
    skip = _core.skipped_checks()

    stdin_data = None
    if stage == "pre-push" and not sys.stdin.isatty():
        try:
            stdin_data = sys.stdin.read()
        except (OSError, ValueError):
            stdin_data = ""

    failures = []
    for name in checks:
        if name in skip:
            _core.warn(f"skipping '{name}' (GITHOOKS_SKIP)")
            continue
        fn = REGISTRY.get(name)
        if fn is None:
            _core.warn(f"unknown check '{name}' in hooks.{stage}; skipping")
            continue
        try:
            rc = fn(rest, stdin_data)
        except Exception as exc:  # noqa: BLE001 - a buggy check must not wedge git
            _core.error(f"check '{name}' crashed: {exc}")
            rc = 1
        if rc != 0:
            failures.append(name)

    if failures:
        _core.header(
            f"\n{len(failures)} check(s) failed at {stage}: {', '.join(failures)}"
        )
        return 1
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    raise SystemExit(main())
