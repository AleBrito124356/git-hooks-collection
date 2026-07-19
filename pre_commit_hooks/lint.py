"""Run the configured linters on staged files only.

Runs at ``pre-commit``. Like the formatter hook, linters are optional: a linter
that is not installed is skipped. Linting only the staged files keeps the hook
fast on large repositories. A non-zero linter exit blocks the commit when
``lint.fail_on_error`` is true (the default).
"""

from __future__ import annotations

import shutil
import sys
from collections import defaultdict
from typing import Dict, List, Optional

from . import _core


def _group_by_language(files: List[str]) -> Dict[str, List[str]]:
    groups: Dict[str, List[str]] = defaultdict(list)
    for path in files:
        lang = _core.language_of(path)
        if lang:
            groups[lang].append(path)
    return groups


def _first_available_tool(tool_specs: List[str]):
    for spec in tool_specs:
        parts = spec.split()
        if parts and shutil.which(parts[0]):
            return parts
    return None


def main(argv: Optional[List[str]] = None, stdin_data: Optional[str] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    files = [a for a in argv if not a.startswith("-")]
    if not files:
        files = _core.staged_files()
    if not files:
        return 0

    config = _core.load_config()
    fail_on_error = bool(_core.cfg(config, "lint.fail_on_error", True))
    tools_cfg = _core.cfg(config, "lint.tools", {}) or {}
    groups = _group_by_language(files)

    failed = False
    for lang, lang_files in groups.items():
        specs = list(tools_cfg.get(lang, []))
        tool = _first_available_tool(specs)
        if not tool:
            continue
        proc = _core.run(tool + lang_files)
        if proc.returncode != 0:
            failed = True
            _core.header(f"\n{tool[0]} reported problems in staged {lang} files:\n")
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
