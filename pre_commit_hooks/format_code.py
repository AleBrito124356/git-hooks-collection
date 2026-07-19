"""Format only the staged files, using whatever formatters are installed.

Runs at ``pre-commit``. Formatters are optional: if ``ruff``/``black``/
``prettier`` are not on PATH the corresponding language is skipped, so this hook
never forces a dependency on anyone. By default reformatted files are re-staged
(``format.autostage: true``); set it to false to fail instead and let the author
review and re-add the changes.
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


def _changed_files(candidates: List[str]) -> List[str]:
    """Which of the given files differ between working tree and index now."""
    if not candidates:
        return []
    out = _core.git("diff", "--name-only", "--", *candidates)
    changed = {line for line in out.splitlines() if line}
    return [c for c in candidates if c in changed]


def main(argv: Optional[List[str]] = None, stdin_data: Optional[str] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    files = [a for a in argv if not a.startswith("-")]
    if not files:
        files = _core.staged_files()
    if not files:
        return 0

    config = _core.load_config()
    autostage = bool(_core.cfg(config, "format.autostage", True))
    tools_cfg = _core.cfg(config, "format.tools", {}) or {}
    groups = _group_by_language(files)

    ran_any = False
    for lang, lang_files in groups.items():
        specs = list(tools_cfg.get(lang, []))
        tool = _first_available_tool(specs)
        if not tool:
            continue
        ran_any = True
        proc = _core.run(tool + lang_files)
        if proc.returncode != 0:
            _core.warn(f"{tool[0]} exited {proc.returncode} while formatting {lang} files")
            if proc.stderr:
                print(proc.stderr, file=sys.stderr)

    if not ran_any:
        return 0

    changed = _changed_files(files)
    if not changed:
        _core.ok("formatting clean")
        return 0

    if autostage:
        _core.run(["git", "add", "--"] + changed)
        _core.ok(f"reformatted and re-staged {len(changed)} file(s):")
        for path in changed:
            _core.info(f"  {path}")
        return 0

    _core.header("\nFormatting changed files. Review and re-stage them:\n")
    for path in changed:
        _core.error(path)
    print(
        "\n  Re-stage with:  git add " + " ".join(changed) + "\n"
        "  Or enable format.autostage in .githooks.yaml to do this automatically.\n",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
