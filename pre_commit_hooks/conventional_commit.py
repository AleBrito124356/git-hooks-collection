"""Validate a commit message against the Conventional Commits specification.

Runs at the ``commit-msg`` stage. Git passes the path to the file holding the
proposed message as the first argument. A valid header looks like:

    type(optional scope)!: subject

Examples that pass:
    feat: add retry to the upload client
    fix(api): reject empty tenant ids
    refactor(db)!: drop the legacy migrations table
    chore: bump ruff to 0.6

Merge, revert, fixup!/squash! and comment-only messages are skipped, because git
generates those and rebasing rewrites them anyway.
"""

from __future__ import annotations

import re
import sys
from typing import List, Optional

from . import _core

_SCISSORS = "# ------------------------ >8 ------------------------"


def _first_meaningful_line(message: str) -> str:
    for raw in message.splitlines():
        if raw.strip() == _SCISSORS:
            break
        if raw.startswith("#"):
            continue
        if raw.strip() == "":
            continue
        return raw.rstrip()
    return ""


def _should_skip(header: str) -> bool:
    lowered = header.lower()
    if header.startswith("Merge ") or header.startswith("Revert "):
        return True
    if lowered.startswith("fixup!") or lowered.startswith("squash!") or lowered.startswith("amend!"):
        return True
    return False


def validate(message: str, config: Optional[dict] = None) -> List[str]:
    """Return a list of error strings; empty means the message is valid."""
    config = config or _core.load_config()
    types = list(_core.cfg(config, "conventional_commit.types", []))
    max_len = int(_core.cfg(config, "conventional_commit.max_header_length", 72))
    min_subject = int(_core.cfg(config, "conventional_commit.min_subject_length", 1))
    require_scope = bool(_core.cfg(config, "conventional_commit.require_scope", False))
    allow_breaking = bool(_core.cfg(config, "conventional_commit.allow_breaking", True))

    header = _first_meaningful_line(message)
    if not header:
        return ["commit message is empty"]
    if _should_skip(header):
        return []

    errors: List[str] = []
    pattern = re.compile(
        r"^(?P<type>[a-zA-Z]+)"
        r"(?:\((?P<scope>[^()\n]+)\))?"
        r"(?P<breaking>!)?"
        r": (?P<subject>.+)$"
    )
    m = pattern.match(header)
    if not m:
        errors.append(
            "header does not match 'type(scope): subject' "
            "(note the space after the colon)"
        )
        return errors

    ctype = m.group("type")
    scope = m.group("scope")
    breaking = m.group("breaking")
    subject = m.group("subject").strip()

    if types and ctype not in types:
        errors.append(
            f"type '{ctype}' is not allowed; use one of: {', '.join(types)}"
        )
    if require_scope and not scope:
        errors.append("a scope is required, e.g. 'feat(api): ...'")
    if breaking and not allow_breaking:
        errors.append("breaking-change marker '!' is not allowed by this repo")
    if len(subject) < min_subject:
        errors.append(f"subject is too short (min {min_subject} characters)")
    if subject and subject[0].isupper() and subject.split()[0].isalpha():
        # Soft rule kept as a warning, not an error, to avoid being annoying.
        pass
    if subject.endswith("."):
        errors.append("subject should not end with a period")
    if len(header) > max_len:
        errors.append(f"header is {len(header)} chars; keep it under {max_len}")
    return errors


def _print_help(errors: List[str], header: str) -> None:
    _core.header("\nCommit message rejected (Conventional Commits):\n")
    _core.info(f"header: {header!r}")
    for err in errors:
        _core.error(err)
    print(
        "\n  Format:  type(optional scope)!: subject\n"
        "  Types:   feat fix docs style refactor perf test build ci chore revert\n"
        "  Example: feat(auth): add refresh-token rotation\n"
        "\n  Bypass once (you own the risk): git commit --no-verify\n",
        file=sys.stderr,
    )


def main(argv: Optional[List[str]] = None, stdin_data: Optional[str] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = [a for a in argv if not a.startswith("-")]
    if not args:
        _core.error("commit-msg hook expects the message file path as an argument")
        return 1
    msg_path = args[0]
    try:
        with open(msg_path, "r", encoding="utf-8") as fh:
            message = fh.read()
    except OSError as exc:
        _core.error(f"cannot read commit message file: {exc}")
        return 1

    errors = validate(message)
    if not errors:
        return 0
    _print_help(errors, _first_meaningful_line(message))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
