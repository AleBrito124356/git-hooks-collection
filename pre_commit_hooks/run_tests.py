"""Run a fast test subset before a push.

Runs at ``pre-push``. The idea is a quick smoke test, not the full CI suite:
enough to catch an obvious break before it reaches the remote. The command is
configurable (``tests.command``). Left empty, it auto-detects a runner:

    - pytest from the project's own virtualenv (.venv/ or venv/), if present
    - pytest on PATH, if a tests/ (or test/) directory exists
    - npm test, if a package.json is present and npm is installed

If nothing is detected, the hook passes quietly. Set ``SKIP_TESTS=1`` (or
``GITHOOKS_SKIP=tests``) to skip a single push. The command runs from the
repository root. On Windows, configured commands keep backslash paths intact and
npm's ``.cmd`` shim is resolved, so ``npm test`` actually runs.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import _core

CHECK_NAME = "tests"

_PYTEST_ARGS = ["-q", "-x", "-p", "no:cacheprovider"]


def _venv_pytest(root: Path) -> str | None:
    for venv in (".venv", "venv"):
        for rel in ("Scripts/pytest.exe", "bin/pytest"):
            cand = root / venv / rel
            if cand.is_file():
                return str(cand)
    return None


def _detect_command(config: dict) -> list[str] | None:
    explicit = str(_core.cfg(config, "tests.command", "") or "").strip()
    if explicit:
        return _core.split_command(explicit)
    if not bool(_core.cfg(config, "tests.auto_detect", True)):
        return None
    root = _core.repo_root() or Path.cwd()
    has_tests = any((root / d).is_dir() for d in ("tests", "test"))
    if has_tests:
        venv_pytest = _venv_pytest(root)
        if venv_pytest:
            return [venv_pytest, *_PYTEST_ARGS]
        if shutil.which("pytest"):
            return ["pytest", *_PYTEST_ARGS]
    if (root / "package.json").is_file() and shutil.which("npm"):
        return ["npm", "test", "--silent"]
    return None


def main(argv: list[str] | None = None, stdin_data: str | None = None) -> int:
    if _core.skip_requested(CHECK_NAME):
        return 0
    if os.environ.get("SKIP_TESTS"):
        _core.warn("SKIP_TESTS set; skipping pre-push tests")
        return 0

    config = _core.load_config()
    command = _detect_command(config)
    if not command:
        return 0

    resolved = shutil.which(command[0])
    if resolved:
        command = [resolved, *command[1:]]
    timeout = int(_core.cfg(config, "tests.timeout_seconds", 120))
    root = _core.repo_root() or Path.cwd()
    _core.info(f"running pre-push tests: {' '.join(command)}")
    try:
        proc = subprocess.run(command, timeout=timeout, cwd=str(root), check=False)
    except FileNotFoundError:
        _core.warn(f"test runner not found: {command[0]}; skipping")
        return 0
    except subprocess.TimeoutExpired:
        _core.error(f"tests exceeded {timeout}s; push blocked")
        print(
            "\n  Speed up the subset or raise tests.timeout_seconds in .githooks.yaml.\n"
            "  Bypass once: git push --no-verify\n",
            file=sys.stderr,
        )
        return 1

    if proc.returncode != 0:
        _core.error("tests failed; push blocked")
        print(
            "\n  Fix the failing tests, or bypass once: git push --no-verify\n",
            file=sys.stderr,
        )
        return 1
    _core.ok("tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
