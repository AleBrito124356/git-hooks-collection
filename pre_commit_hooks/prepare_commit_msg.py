"""Link every commit to the issue id taken from the branch name.

Runs at ``prepare-commit-msg``. Git passes the message file path plus an optional
source (``message``, ``template``, ``merge``, ``squash``, ``commit``); under the
pre-commit framework the source arrives in ``PRE_COMMIT_COMMIT_MSG_SOURCE``
instead. If the branch name carries an issue id (``feature/ABC-123-add-thing``
-> ``ABC-123``) and the message does not already mention it, the id is added.

Two modes (``issue_prefix.mode``):

``trailer`` (default)
    Append a ``Refs: ABC-123`` trailer, the footer form the Conventional Commits
    spec itself uses. The header stays ``feat: add thing``, so the
    ``conventional-commit`` check keeps passing.

``prefix``
    Prepend ``issue_prefix.template`` (``[ABC-123] `` by default) to the header.
    ``conventional_commit.ignore_prefix_regex`` must match that prefix, or every
    commit on a ticket branch is rejected; the shipped defaults do.

Autosquash commits (``fixup!`` / ``squash!`` / ``amend!``) are left alone so
``git rebase --autosquash`` can still pair them up. This hook never fails a
commit: at worst it leaves the message untouched.
"""

from __future__ import annotations

import os
import re
import sys

from . import _core

CHECK_NAME = "issue-prefix"

# A git trailer line: "<token>: <value>" where the token has no spaces.
_TRAILER_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*:\s")
_AUTOSQUASH = ("fixup!", "squash!", "amend!")


def extract_issue(branch: str, pattern: str) -> str | None:
    try:
        rx = re.compile(pattern)
    except re.error:
        return None
    m = rx.search(branch)
    if m:
        return m.group(1) if m.groups() else m.group(0)
    return None


def comment_char() -> str:
    """The comment marker git uses in the message file (core.commentChar)."""
    for key in ("core.commentString", "core.commentChar"):
        value = _core.git("config", "--get", key)
        if value and value != "auto":
            return value
    return "#"


def _scissors(comment: str) -> str:
    return f"{comment} ------------------------ >8 ------------------------"


def _split_message(message: str, comment: str):
    """Split into (content lines, trailing comment/blank lines, scissors tail)."""
    lines = message.split("\n")
    cut = len(lines)
    scissors = _scissors(comment)
    for i, line in enumerate(lines):
        if line.rstrip() == scissors:
            cut = i
            break
    body, tail = lines[:cut], lines[cut:]
    end = len(body)
    while end > 0 and (not body[end - 1].strip() or body[end - 1].startswith(comment)):
        end -= 1
    return body[:end], body[end:], tail


def _meaningful(lines: list[str], comment: str) -> list[str]:
    return [line for line in lines if line.strip() and not line.startswith(comment)]


def already_references(message: str, issue: str, comment: str = "#") -> bool:
    content, _trailing, _tail = _split_message(message, comment)
    return any(issue in line for line in _meaningful(content, comment))


def add_trailer(message: str, key: str, value: str, comment: str = "#") -> str:
    """Append ``key: value`` as a git trailer, above git's comment block.

    Joins an existing trailer block (``Signed-off-by:`` ...) when there is one,
    otherwise starts a new paragraph. For an empty message (the editor flow)
    it leaves two blank lines on top, so the subject the author types stays a
    paragraph of its own.
    """
    content, trailing, tail = _split_message(message, comment)
    trailer = f"{key}: {value}"
    meaningful = _meaningful(content, comment)
    if not meaningful:
        new_content = ["", "", trailer]
    else:
        last_blank = max((i for i, line in enumerate(content) if not line.strip()), default=-1)
        last_para = _meaningful(content[last_blank + 1 :], comment)
        is_trailer_block = (
            last_blank >= 0 and last_para and all(_TRAILER_RX.match(line) for line in last_para)
        )
        new_content = [*content, trailer] if is_trailer_block else [*content, "", trailer]
    if not trailing and not tail:
        trailing = [""]  # keep the file newline-terminated
    return "\n".join(new_content + trailing + tail)


def add_prefix(message: str, prefix: str) -> str:
    return prefix + message


def _first_line(message: str, comment: str) -> str:
    content, _trailing, _tail = _split_message(message, comment)
    meaningful = _meaningful(content, comment)
    return meaningful[0] if meaningful else ""


def main(argv: list[str] | None = None, stdin_data: str | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = [a for a in argv if not a.startswith("-")]
    if not args:
        return 0
    if _core.skip_requested(CHECK_NAME):
        return 0
    msg_path = args[0]
    source = args[1] if len(args) > 1 else os.environ.get("PRE_COMMIT_COMMIT_MSG_SOURCE", "")

    config = _core.load_config()
    skip_sources = [str(s) for s in _core.cfg_list(config, "issue_prefix.skip_sources")]
    if source in skip_sources:
        return 0

    branch = _core.current_branch()
    pattern = str(_core.cfg(config, "issue_prefix.branch_regex", r"([A-Z][A-Z0-9]+-\d+)"))
    issue = extract_issue(branch, pattern)
    if not issue:
        return 0

    try:
        with open(msg_path, encoding="utf-8") as fh:
            message = fh.read()
    except (OSError, UnicodeDecodeError):
        return 0

    comment = comment_char()
    if already_references(message, issue, comment):
        return 0
    if _first_line(message, comment).startswith(_AUTOSQUASH):
        return 0

    mode = str(_core.cfg(config, "issue_prefix.mode", "trailer")).strip().lower()
    if mode == "prefix":
        template = str(_core.cfg(config, "issue_prefix.template", "[{issue}] "))
        new_message = add_prefix(message, template.format(issue=issue))
    else:
        if mode != "trailer":
            _core.warn(f"unknown issue_prefix.mode {mode!r}; using 'trailer'")
        key = str(_core.cfg(config, "issue_prefix.trailer_key", "Refs")).strip() or "Refs"
        new_message = add_trailer(message, key, issue, comment)

    try:
        with open(msg_path, "w", encoding="utf-8", newline="") as fh:
            fh.write(new_message)
    except OSError:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
