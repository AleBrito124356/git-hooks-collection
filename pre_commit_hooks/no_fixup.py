"""Block pushing work-in-progress commits (fixup! / squash! / WIP).

Runs at ``pre-push``. Git feeds the hook one line per ref being pushed on stdin:

    <local ref> <local sha> <remote ref> <remote sha>

For each pushed range this hook lists the commit subjects and refuses the push if
any of them are autosquash markers (``fixup!``, ``squash!``, ``amend!``) or match
a WIP pattern. Those are meant to be folded in with ``git rebase -i
--autosquash`` before the branch goes public.
"""

from __future__ import annotations

import re
import sys
from typing import List, Optional, Tuple

from . import _core

_ZERO = "0000000000000000000000000000000000000000"


def _compile_patterns(patterns: List[str]) -> List[re.Pattern]:
    compiled = []
    for pat in patterns:
        # Anchor autosquash markers to the start; match WIP anywhere.
        if pat.endswith("!"):
            compiled.append(re.compile(r"^" + re.escape(pat)))
        else:
            compiled.append(re.compile(re.escape(pat), re.IGNORECASE))
    return compiled


def _commit_subjects(rev_range: str) -> List[Tuple[str, str]]:
    out = _core.git("log", "--format=%h\x1f%s", rev_range)
    result = []
    for line in out.splitlines():
        if "\x1f" in line:
            sha, subject = line.split("\x1f", 1)
            result.append((sha, subject))
    return result


def _ranges_from_stdin(stdin_data: str) -> List[str]:
    ranges: List[str] = []
    for line in stdin_data.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        _local_ref, local_sha, _remote_ref, remote_sha = parts[:4]
        if local_sha == _ZERO:
            continue  # branch deletion
        if remote_sha == _ZERO:
            # New branch: everything on it not already on a remote.
            ranges.append(f"{local_sha} --not --remotes")
        else:
            ranges.append(f"{remote_sha}..{local_sha}")
    return ranges


def _fallback_range() -> Optional[str]:
    upstream = _core.git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if upstream:
        return f"{upstream}..HEAD"
    return None


def main(argv: Optional[List[str]] = None, stdin_data: Optional[str] = None) -> int:
    config = _core.load_config()
    patterns = list(_core.cfg(config, "no_fixup.block_patterns", []))
    matchers = _compile_patterns(patterns)

    if stdin_data is None and not sys.stdin.isatty():
        try:
            stdin_data = sys.stdin.read()
        except (OSError, ValueError):
            stdin_data = ""

    if stdin_data:
        ranges = _ranges_from_stdin(stdin_data)
    else:
        fb = _fallback_range()
        ranges = [fb] if fb else []

    offenders: List[Tuple[str, str]] = []
    seen = set()
    for rev_range in ranges:
        for sha, subject in _commit_subjects(rev_range):
            if sha in seen:
                continue
            seen.add(sha)
            if any(m.search(subject) for m in matchers):
                offenders.append((sha, subject))

    if not offenders:
        return 0

    _core.header("\nPush blocked: work-in-progress commits detected.\n")
    for sha, subject in offenders:
        _core.error(f"{sha}  {subject}")
    print(
        "\n  Fold these in before pushing:\n"
        "    git rebase -i --autosquash @{u}\n"
        "\n  Bypass once (you own the risk): git push --no-verify\n",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
