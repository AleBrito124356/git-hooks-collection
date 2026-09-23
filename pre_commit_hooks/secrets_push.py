"""Scan every commit being pushed for secrets -- what GitHub push protection sees.

Runs at ``pre-push``. The pre-commit ``secrets`` check only ever sees the index,
so a token committed with ``git commit --no-verify``, before the hooks were
installed, or on another machine still reaches the remote -- where GitHub
rejects the whole push with GH013 once the secret is already in history. This
check scans the lines added by each commit in the pushed range (native pre-push
stdin, or ``PRE_COMMIT_FROM_REF``/``PRE_COMMIT_TO_REF`` under the pre-commit
framework; a new branch is compared with everything already on a remote) and
reports the commit, file and line.

It uses the ``secrets:`` settings (exclude, allow_regex, entropy threshold).
Skip one push with ``GITHOOKS_SKIP=secrets-push``.
"""

from __future__ import annotations

import sys

from . import _core
from .secrets import report, scan_ranges, settings_from_config

CHECK_NAME = "secrets-push"


def main(argv: list[str] | None = None, stdin_data: str | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if _core.skip_requested(CHECK_NAME):
        return 0
    settings = settings_from_config(_core.load_config())
    if stdin_data is None:
        stdin_data = _core.read_hook_stdin()
    findings = scan_ranges(_core.pushed_ranges(stdin_data), settings)
    if not findings:
        return 0
    report(findings, "range")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
