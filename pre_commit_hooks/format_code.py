"""Format the staged files with whatever formatters are installed.

Runs at ``pre-commit``. Formatters are optional: if ``ruff``/``black``/
``prettier`` are not on PATH the corresponding language is skipped, so this hook
never forces a dependency on anyone.

With ``format.autostage: true`` (the default) reformatted files are re-staged,
but only files that were *fully* staged. A formatter rewrites the working-tree
file, and ``git add`` stages the whole of it: for a partially staged file that
would sweep unstaged hunks into the commit -- hunks no other pre-commit check
(the secret scanner included) ever looked at. So a file that also has unstaged
changes is left untouched, with a warning that says how to format it.

With ``format.autostage: false`` the hook formats and fails when anything
changed, so the author reviews and re-stages. That is also the behaviour under
the pre-commit framework, whose convention is "a hook that modifies files
fails".
"""

from __future__ import annotations

import hashlib
import os
import sys
from collections import defaultdict
from pathlib import Path

from . import _core

CHECK_NAME = "format"


def _group_by_language(files: list[str]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for path in files:
        lang = _core.language_of(path)
        if lang:
            groups[lang].append(path)
    return groups


def _first_available_tool(tool_specs: list[str]) -> list[str] | None:
    for spec in tool_specs:
        tool = _core.resolve_tool(str(spec))
        if tool:
            return tool
    return None


def _digest(path: str) -> str | None:
    try:
        return hashlib.sha1(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def plan_tools(
    files: list[str], config: dict, section: str
) -> list[tuple[str, list[str], list[str]]]:
    """(language, resolved tool argv, files) for every language with a tool."""
    tools_cfg = _core.cfg(config, f"{section}.tools", {}) or {}
    plan = []
    for lang, lang_files in _group_by_language(files).items():
        specs = tools_cfg.get(lang) or []
        if isinstance(specs, str):
            specs = [specs]
        tool = _first_available_tool(list(specs))
        if tool:
            plan.append((lang, tool, lang_files))
    return plan


def main(argv: list[str] | None = None, stdin_data: str | None = None) -> int:
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
    autostage = bool(_core.cfg(config, "format.autostage", True))
    under_framework = bool(os.environ.get("PRE_COMMIT"))
    if under_framework:
        autostage = False

    plan = plan_tools(files, config, "format")
    if not plan:
        return 0
    candidates = [f for _lang, _tool, lang_files in plan for f in lang_files]

    # Decide *before* touching anything which files may be re-staged.
    partial = set(_core.unstaged_files(candidates)) if autostage else set()
    in_index = set(_core.index_entries(candidates)) if autostage else set()
    held_back = [f for f in candidates if f in partial]
    to_format = [f for f in candidates if f not in partial]
    before = {f: _digest(f) for f in to_format}

    for lang, tool, lang_files in plan:
        targets = [f for f in lang_files if f in before]
        if not targets:
            continue
        proc = _core.run(tool + targets)
        if proc.returncode != 0:
            _core.warn(
                f"{_core.tool_name(tool[0])} exited {proc.returncode} while formatting {lang} files"
            )
            if proc.stderr:
                print(proc.stderr, file=sys.stderr)

    changed = [f for f in to_format if _digest(f) != before[f]]

    if held_back:
        _core.warn(
            f"not formatted: {len(held_back)} file(s) also have unstaged changes, and "
            "re-staging them would commit those changes too:"
        )
        for path in held_back:
            _core.info(f"  {path}")
        print(
            "  Stage the whole file (or `git stash --keep-index` the rest) and commit again\n"
            "  to have it formatted.\n",
            file=sys.stderr,
        )

    if not changed:
        if not held_back:
            _core.ok("formatting clean")
        return 0

    if autostage:
        stage = [f for f in changed if f in in_index]
        if stage:
            _core.git_run("add", "--", *stage)
            _core.invalidate_memo()
        _core.ok(f"reformatted and re-staged {len(stage)} file(s):")
        for path in stage:
            _core.info(f"  {path}")
        return 0

    _core.header("\nFormatting changed files. Review and re-stage them:\n")
    for path in changed:
        _core.error(path)
    print(
        "\n  Re-stage with:  git add " + " ".join(changed) + "\n"
        "  Or set format.autostage: true in .githooks.yaml to do this automatically.\n",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
