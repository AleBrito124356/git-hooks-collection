"""Run the configured linters on staged files only.

Runs at ``pre-commit``. Like the formatter hook, linters are optional: a linter
that is not installed is skipped. Linting only the staged files keeps the hook
fast on large repositories. A non-zero linter exit blocks the commit when
``lint.fail_on_error`` is true (the default).

Files are grouped by language bucket (``python``, ``javascript``, ``web``).
The ``web`` bucket -- Markdown, YAML, JSON, CSS, HTML -- has no default linter:
sending those to eslint only produced parse errors. Add one under
``lint.tools.web`` if you have a linter that understands them.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List, Optional

from . import _core
from .format_code import plan_tools

CHECK_NAME = "lint"


def main(argv: Optional[List[str]] = None, stdin_data: Optional[str] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if _core.skip_requested(CHECK_NAME):
        return 0
    files = [a for a in argv if not a.startswith("-")]
    if not files:
        files = _core.staged_files()
    files = [f for f in files if os.path.isfile(f)]
    if not files:
        return 0

    config = _core.load_config()
    fail_on_error = bool(_core.cfg(config, "lint.fail_on_error", True))

    failed = False
    for lang, tool, lang_files in plan_tools(files, config, "lint"):
        proc = _core.run(tool + lang_files)
        if proc.returncode != 0:
            failed = True
            _core.header(f"\n{Path(tool[0]).name} reported problems in staged {lang} files:\n")
            if proc.stdout:
                print(proc.stdout, file=sys.stderr)
            if proc.stderr:
                print(proc.stderr, file=sys.stderr)

    if failed and fail_on_error:
        print(
            "\n  Fix the issues above, or bypass once with: git commit --no-verify\n",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
