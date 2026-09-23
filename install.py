#!/usr/bin/env python3
"""Install the hooks into a repository.

Two delivery paths, one command:

    python install.py                 # interactive, native install into .githooks/
    python install.py --all           # enable every hook, no prompts
    python install.py --hooks secrets,branch-protect,no-fixup
    python install.py --mode pre-commit   # generate a .pre-commit-config.yaml
    python install.py --uninstall

This file is a thin shim so the installer runs straight from a clone with no pip
install; the logic lives in ``pre_commit_hooks/installer.py`` (also reachable as
``githooks install`` once the package is installed).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pre_commit_hooks.installer import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
