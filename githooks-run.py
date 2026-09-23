#!/usr/bin/env python3
"""Entry point for the git hooks: ``python githooks-run.py <stage> [args]``.

This file only makes the bundled ``pre_commit_hooks`` package importable -- from
this checkout or from a vendored ``.githooks/`` directory -- and hands over to
``pre_commit_hooks.dispatch``, which reads ``.githooks.yaml`` and runs the checks
enabled for the stage. ``python githooks-run.py run <check> [args]`` runs one
check on its own; the standalone ``hooks/*`` wrappers use that form.

The package directory is put on ``sys.path`` from Python rather than through
``PYTHONPATH`` in the shell wrapper: Git Bash on Windows does not translate a
POSIX-style ``PYTHONPATH`` for a native Python, which broke every wrapper there.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _candidate in (_HERE, _HERE.parent):
    if (_candidate / "pre_commit_hooks" / "__init__.py").is_file():
        sys.path.insert(0, str(_candidate))
        break

from pre_commit_hooks.dispatch import main

if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    raise SystemExit(main())
