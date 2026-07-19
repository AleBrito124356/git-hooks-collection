"""Refuse commits made directly on a protected branch (main / master).

Runs at ``pre-commit``. The point is muscle memory: it is easy to forget you are
on ``main`` and commit straight to it. This nudges you onto a feature branch. Set
the escape-hatch environment variable (``ALLOW_COMMIT_TO_PROTECTED=1`` by
default) when you really do mean to commit on the protected branch.
"""

from __future__ import annotations

import os
import sys
from typing import List, Optional

from . import _core


def is_blocked(branch: str, config: Optional[dict] = None) -> bool:
    config = config or _core.load_config()
    protected = list(_core.cfg(config, "branch_protect.protected", []))
    allow_env = _core.cfg(config, "branch_protect.allow_env", "ALLOW_COMMIT_TO_PROTECTED")
    if allow_env and os.environ.get(str(allow_env)):
        return False
    return branch in protected


def main(argv: Optional[List[str]] = None, stdin_data: Optional[str] = None) -> int:
    config = _core.load_config()
    branch = _core.current_branch()
    if not is_blocked(branch, config):
        return 0

    allow_env = _core.cfg(config, "branch_protect.allow_env", "ALLOW_COMMIT_TO_PROTECTED")
    _core.header(f"\nDirect commit to protected branch '{branch}' blocked.\n")
    print(
        "  Work on a feature branch instead:\n"
        f"    git switch -c feature/your-change\n"
        f"    git commit ...\n"
        "\n  If you really meant to commit here:\n"
        f"    {allow_env}=1 git commit ...     # one-off override\n"
        "    or  git commit --no-verify\n",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
