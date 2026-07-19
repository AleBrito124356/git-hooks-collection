"""Prefix the commit message with the issue id taken from the branch name.

Runs at ``prepare-commit-msg``. Git passes the message file path plus an optional
source (``message``, ``template``, ``merge``, ``squash``, ``commit``). If the
branch name carries an issue id (``feature/ABC-123-add-thing`` -> ``ABC-123``)
and the message does not already mention it, the id is prepended so every commit
is traceable back to its ticket.

This hook never fails a commit: at worst it leaves the message untouched.
"""

from __future__ import annotations

import re
import sys
from typing import List, Optional

from . import _core


def extract_issue(branch: str, pattern: str) -> Optional[str]:
    try:
        rx = re.compile(pattern)
    except re.error:
        return None
    m = rx.search(branch)
    if m:
        return m.group(1) if m.groups() else m.group(0)
    return None


def _already_present(message: str, issue: str) -> bool:
    first_line = ""
    for line in message.splitlines():
        if line.strip() and not line.startswith("#"):
            first_line = line
            break
    return issue in first_line or issue in message.split("\n\n")[0]


def main(argv: Optional[List[str]] = None, stdin_data: Optional[str] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = [a for a in argv if not a.startswith("-")]
    if not args:
        return 0
    msg_path = args[0]
    source = args[1] if len(args) > 1 else ""

    config = _core.load_config()
    skip_sources = list(_core.cfg(config, "issue_prefix.skip_sources", []))
    if source in skip_sources:
        return 0

    branch = _core.current_branch()
    pattern = str(_core.cfg(config, "issue_prefix.branch_regex", r"([A-Z][A-Z0-9]+-\d+)"))
    template = str(_core.cfg(config, "issue_prefix.template", "[{issue}] "))

    issue = extract_issue(branch, pattern)
    if not issue:
        return 0

    try:
        with open(msg_path, "r", encoding="utf-8") as fh:
            message = fh.read()
    except OSError:
        return 0

    if _already_present(message, issue):
        return 0

    prefix = template.format(issue=issue)
    new_message = prefix + message
    try:
        with open(msg_path, "w", encoding="utf-8") as fh:
            fh.write(new_message)
    except OSError:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
