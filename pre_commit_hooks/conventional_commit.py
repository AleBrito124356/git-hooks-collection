"""Validate a commit message against the Conventional Commits specification.

Runs at the ``commit-msg`` stage. Git passes the path to the file holding the
proposed message as the first argument. A valid header looks like:

    type(optional scope)!: subject

Examples that pass:
    feat: add retry to the upload client
    fix(api): reject empty tenant ids
    refactor(db)!: drop the legacy migrations table
    chore: bump ruff to 0.6
    [ABC-123] feat: add retry      <- ticket prefix, see ignore_prefix_regex

Merge, revert, fixup!/squash! and comment-only messages are skipped, because git
generates those and rebasing rewrites them anyway.

``conventional_commit.ignore_prefix_regex`` strips a leading ticket prefix
before the header is checked, so teams that use ``issue_prefix.mode: prefix``
(``[ABC-123] feat: x``) are not rejected on every commit. The header length
limit still counts the whole header, prefix included, because that is what
``git log --oneline`` shows.
"""

from __future__ import annotations

import re
import sys
from typing import List, Optional

from . import _core

CHECK_NAME = "conventional-commit"

_SCISSORS = "# ------------------------ >8 ------------------------"
_HEADER_RX = re.compile(
    r"^(?P<type>[a-zA-Z]+)"
    r"(?:\((?P<scope>[^()\n]+)\))?"
    r"(?P<breaking>!)?"
    r": (?P<subject>.+)$"
)
_TRAILER_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*:\s")


def _meaningful_lines(message: str) -> List[str]:
    lines = []
    for raw in message.splitlines():
        if raw.strip() == _SCISSORS:
            break
        if raw.startswith("#") or raw.strip() == "":
            continue
        lines.append(raw.rstrip())
    return lines


def _first_meaningful_line(message: str) -> str:
    lines = _meaningful_lines(message)
    return lines[0] if lines else ""


def _should_skip(header: str) -> bool:
    lowered = header.lower()
    if header.startswith(("Merge ", "Revert ")):
        return True
    return lowered.startswith(("fixup!", "squash!", "amend!"))


def strip_ignored_prefix(header: str, config: dict) -> str:
    """Remove a leading ticket prefix matched by ``ignore_prefix_regex``."""
    pattern = _core.cfg(config, "conventional_commit.ignore_prefix_regex", "")
    if not pattern:
        return header
    try:
        rx = re.compile(str(pattern))
    except re.error:
        _core.warn(f"ignoring invalid conventional_commit.ignore_prefix_regex: {pattern!r}")
        return header
    m = rx.match(header)
    if m and m.end() > 0:
        return header[m.end() :]
    return header


def validate(message: str, config: Optional[dict] = None) -> List[str]:
    """Return a list of error strings; empty means the message is valid."""
    config = config or _core.load_config()
    types = [str(t) for t in _core.cfg_list(config, "conventional_commit.types")]
    max_len = int(_core.cfg(config, "conventional_commit.max_header_length", 72))
    min_subject = int(_core.cfg(config, "conventional_commit.min_subject_length", 1))
    require_scope = bool(_core.cfg(config, "conventional_commit.require_scope", False))
    allow_breaking = bool(_core.cfg(config, "conventional_commit.allow_breaking", True))

    lines = _meaningful_lines(message)
    header = lines[0] if lines else ""
    if not header:
        return ["commit message is empty"]

    # A message that is nothing but the issue trailer added by issue-prefix
    # (the author saved the editor without typing a subject) is empty too.
    trailer_key = str(_core.cfg(config, "issue_prefix.trailer_key", "Refs") or "Refs")
    if header.startswith(f"{trailer_key}: ") and all(_TRAILER_RX.match(ln) for ln in lines):
        return ["commit message has no subject line, only trailers"]

    checked = strip_ignored_prefix(header, config)
    if _should_skip(checked):
        return []

    errors: List[str] = []
    m = _HEADER_RX.match(checked)
    if not m:
        errors.append(
            "header does not match 'type(scope): subject' (note the space after the colon)"
        )
        return errors

    ctype = m.group("type")
    scope = m.group("scope")
    breaking = m.group("breaking")
    subject = m.group("subject").strip()

    if types and ctype not in types:
        errors.append(f"type '{ctype}' is not allowed; use one of: {', '.join(types)}")
    if require_scope and not scope:
        errors.append("a scope is required, e.g. 'feat(api): ...'")
    if breaking and not allow_breaking:
        errors.append("breaking-change marker '!' is not allowed by this repo")
    if len(subject) < min_subject:
        errors.append(f"subject is too short (min {min_subject} characters)")
    if subject.endswith("."):
        errors.append("subject should not end with a period")
    if len(header) > max_len:
        errors.append(f"header is {len(header)} chars; keep it under {max_len}")
    return errors


def _print_help(errors: List[str], header: str, config: dict) -> None:
    types = " ".join(str(t) for t in _core.cfg_list(config, "conventional_commit.types"))
    _core.header("\nCommit message rejected (Conventional Commits):\n")
    _core.info(f"header: {header!r}")
    for err in errors:
        _core.error(err)
    print(
        "\n  Format:  type(optional scope)!: subject\n"
        f"  Types:   {types}\n"
        "  Example: feat(auth): add refresh-token rotation\n"
        "\n  Bypass once (you own the risk): git commit --no-verify\n",
        file=sys.stderr,
    )


def main(argv: Optional[List[str]] = None, stdin_data: Optional[str] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if _core.skip_requested(CHECK_NAME):
        return 0
    args = [a for a in argv if not a.startswith("-")]
    if not args:
        _core.error("commit-msg hook expects the message file path as an argument")
        return 1
    msg_path = args[0]
    try:
        with open(msg_path, encoding="utf-8", errors="replace") as fh:
            message = fh.read()
    except OSError as exc:
        _core.error(f"cannot read commit message file: {exc}")
        return 1

    config = _core.load_config()
    errors = validate(message, config)
    if not errors:
        return 0
    _print_help(errors, _first_meaningful_line(message), config)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
