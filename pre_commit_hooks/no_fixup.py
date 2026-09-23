"""Block pushing work-in-progress commits (fixup! / squash! / WIP).

Runs at ``pre-push``. Git feeds the hook one line per ref being pushed on stdin:

    <local ref> <local sha> <remote ref> <remote sha>

For each pushed range this hook lists the commit subjects and refuses the push if
any of them are autosquash markers (``fixup!``, ``squash!``, ``amend!``) or match
a WIP pattern. Those are meant to be folded in with ``git rebase -i
--autosquash`` before the branch goes public.

Under the pre-commit framework there is no stdin: the pushed range arrives in
``PRE_COMMIT_FROM_REF`` / ``PRE_COMMIT_TO_REF`` and is read from there. A new
branch is checked against everything not yet on any remote.

Patterns ending in ``!`` are anchored to the start of the subject; the others
match as whole words anywhere, case-insensitively, so ``WIP`` blocks
``WIP: half done`` and ``[wip] try`` but not ``fix: swipe gesture``.
"""

from __future__ import annotations

import re
import sys
from typing import Any, List, Optional, Pattern, Tuple

from . import _core

CHECK_NAME = "no-fixup"


def _coerce_pattern(pat: Any) -> Optional[str]:
    """Normalise one configured pattern to a string.

    PyYAML reads an unquoted ``- wip:`` list item as the mapping
    ``{"wip": None}``; the bundled parser reads it as ``"wip:"``. Accept both so
    an old, unquoted config can never crash the push.
    """
    if pat is None:
        return None
    if isinstance(pat, dict):
        if len(pat) == 1:
            key, value = next(iter(pat.items()))
            return f"{key}:" if value is None else f"{key}: {value}"
        return None
    text = str(pat)
    return text if text.strip() else None


def _compile_patterns(patterns: List[Any]) -> List[Pattern[str]]:
    compiled = []
    for raw in patterns:
        pat = _coerce_pattern(raw)
        if pat is None:
            continue
        if pat.endswith("!"):
            # Autosquash markers are anchored to the start of the subject.
            compiled.append(re.compile(r"^" + re.escape(pat)))
            continue
        body = re.escape(pat)
        # Whole-word match: "WIP" must not fire on "swipe" or "wipe".
        prefix = r"(?<![0-9A-Za-z_])" if pat[0].isalnum() or pat[0] == "_" else ""
        suffix = r"(?![0-9A-Za-z_])" if pat[-1].isalnum() or pat[-1] == "_" else ""
        compiled.append(re.compile(prefix + body + suffix, re.IGNORECASE))
    return compiled


def _commit_subjects(rev_args: List[str]) -> List[Tuple[str, str]]:
    out = _core.git("log", "--format=%h%x1f%s", *rev_args)
    result = []
    for line in out.splitlines():
        if "\x1f" in line:
            sha, subject = line.split("\x1f", 1)
            result.append((sha, subject))
    return result


def find_offenders(ranges: List[List[str]], patterns: List[Any]) -> List[Tuple[str, str]]:
    matchers = _compile_patterns(patterns)
    offenders: List[Tuple[str, str]] = []
    seen = set()
    for rev_args in ranges:
        for sha, subject in _commit_subjects(rev_args):
            if sha in seen:
                continue
            seen.add(sha)
            if any(m.search(subject) for m in matchers):
                offenders.append((sha, subject))
    return offenders


def main(argv: Optional[List[str]] = None, stdin_data: Optional[str] = None) -> int:
    if _core.skip_requested(CHECK_NAME):
        return 0
    config = _core.load_config()
    patterns = _core.cfg_list(config, "no_fixup.block_patterns")

    if stdin_data is None:
        stdin_data = _core.read_hook_stdin()
    ranges = _core.pushed_ranges(stdin_data)
    offenders = find_offenders(ranges, patterns)
    if not offenders:
        return 0

    _core.header("\nPush blocked: work-in-progress commits detected.\n")
    for sha, subject in offenders:
        _core.error(f"{sha}  {subject}")
    print(
        "\n  Fold these in before pushing:\n"
        "    git rebase -i --autosquash <base>   (e.g. origin/main)\n"
        "\n  Bypass once (you own the risk): git push --no-verify\n",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
