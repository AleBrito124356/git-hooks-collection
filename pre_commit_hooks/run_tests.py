"""Run a fast test subset before a push.

Runs at ``pre-push``. The idea is a quick smoke test, not the full CI suite:
enough to catch an obvious break before it reaches the remote. The command is
configurable (``tests.command``). Left empty, it auto-detects a runner:

    - pytest, if pytest is installed and a tests/ directory exists
    - npm test, if a package.json is present and npm is installed

If nothing is detected, the hook passes quietly. Set ``SKIP_TESTS=1`` to skip a
single push.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from . import _core


def _detect_command(config: dict) -> Optional[List[str]]:
    explicit = str(_core.cfg(config, "tests.command", "") or "").strip()
    if explicit:
        return shlex.split(explicit)
    if not bool(_core.cfg(config, "tests.auto_detect", True)):
        return None
    root = _core.repo_root() or Path.cwd()
    if shutil.which("pytest") and (root / "tests").is_dir():
        return ["pytest", "-q", "-x", "-p", "no:cacheprovider"]
    if (root / "package.json").is_file() and shutil.which("npm"):
        return ["npm", "test", "--silent"]
    return None


def main(argv: Optional[List[str]] = None, stdin_data: Optional[str] = None) -> int:
    if os.environ.get("SKIP_TESTS"):
        _core.warn("SKIP_TESTS set; skipping pre-push tests")
        return 0

    config = _core.load_config()
    command = _detect_command(config)
    if not command:
        return 0

    timeout = int(_core.cfg(config, "tests.timeout_seconds", 120))
    _core.info(f"running pre-push tests: {' '.join(command)}")
    try:
        proc = subprocess.run(command, timeout=timeout)
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
