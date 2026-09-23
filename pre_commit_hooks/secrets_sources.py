"""Where the secret scanner gets its lines from.

Every source yields ``Chunk(path, lines, commit)`` where ``lines`` are
``(line number, text)`` pairs in the *new* version of the file:

``staged_diff``
    Only the lines this commit adds (``git diff --cached -U0``). This is what
    "scan the staged diff" means: text already in the repository is not
    re-reported on every commit that touches the file.
``files``
    Whole files as staged in the index (or on disk when not tracked).
``commit_range``
    The lines each commit in a range adds (``git log -p``), with the commit
    id attached -- the same view GitHub push protection has of a push.
``all_files``
    Every tracked file, for an audit of the whole tree.

The patch parser streams git's output, so scanning a long history never holds
it all in memory.
"""

from __future__ import annotations

import re
import subprocess
from typing import IO, Iterable, Iterator, NamedTuple

from . import _core

# Stable patch output regardless of the user's diff settings: fixed a/ b/
# prefixes, no colour, no external diff or textconv, raw UTF-8 paths, no
# context lines, renames detected, only files that have new content.
_PATCH_ARGS = [
    "--no-color",
    "--no-ext-diff",
    "--no-textconv",
    "-U0",
    "--src-prefix=a/",
    "--dst-prefix=b/",
    "--find-renames",
    "--diff-filter=ACMR",
    "--ignore-submodules",
]
_COMMIT_MARK = b"\x01"
_HUNK_RX = re.compile(rb"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


class Chunk(NamedTuple):
    path: str
    lines: list[tuple[int, str]]
    commit: str | None = None


# --------------------------------------------------------------------------- #
# Patch parsing
# --------------------------------------------------------------------------- #
_ESCAPES = {
    ord("a"): 7,
    ord("b"): 8,
    ord("t"): 9,
    ord("n"): 10,
    ord("v"): 11,
    ord("f"): 12,
    ord("r"): 13,
    ord('"'): 34,
    ord("\\"): 92,
}


def unquote_path(raw: bytes) -> str:
    """Undo git's C-style quoting of a path ("a\\tb", "caf\\303\\251")."""
    if not (len(raw) >= 2 and raw[:1] == b'"' and raw[-1:] == b'"'):
        return _core.decode_path(raw)
    out = bytearray()
    body = raw[1:-1]
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == 92 and i + 1 < len(body):  # backslash
            nxt = body[i + 1]
            if 48 <= nxt <= 55 and i + 3 < len(body):  # octal \ooo
                out.append(int(body[i + 1 : i + 4], 8))
                i += 4
                continue
            out.append(_ESCAPES.get(nxt, nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    return _core.decode_path(bytes(out))


def _target_path(raw: bytes) -> str | None:
    """The new-side path from a ``+++ b/<path>`` line; None for /dev/null."""
    target = raw[4:]
    if target.endswith(b"\t"):  # git appends a TAB after names with spaces
        target = target[:-1]
    if target == b"/dev/null":
        return None
    path = unquote_path(target)
    return path[2:] if path.startswith("b/") else path


def parse_patch(stream: Iterable[bytes], max_bytes: int | None = None) -> Iterator[Chunk]:
    """Turn ``git diff`` / ``git log -p`` output into chunks of added lines.

    Commit boundaries are lines that start with ``\\x01<sha>`` (from
    ``--format=%x01%H``). A file whose added text exceeds ``max_bytes`` is
    skipped with a warning.
    """
    commit: str | None = None
    path: str | None = None
    lines: list[tuple[int, str]] = []
    size = 0
    oversized = False
    new_line = 0

    def flush() -> Iterator[Chunk]:
        if path is not None and lines and not oversized:
            yield Chunk(path, lines, commit)

    for raw in stream:
        raw = raw.rstrip(b"\n")
        if raw.startswith(_COMMIT_MARK):
            yield from flush()
            commit, path, lines, size, oversized = raw[1:].decode().strip(), None, [], 0, False
            continue
        if raw.startswith(b"diff --git "):
            yield from flush()
            path, lines, size, oversized = None, [], 0, False
            continue
        if path is None:
            # File header: only the new-side name matters.
            if raw.startswith(b"+++ "):
                path = _target_path(raw)
            continue
        if raw.startswith(b"@@"):
            m = _HUNK_RX.match(raw)
            new_line = int(m.group(1)) if m else 0
        elif raw.startswith(b"+"):
            if not oversized:
                text = raw[1:].rstrip(b"\r")
                size += len(text) + 1
                if max_bytes is not None and size > max_bytes:
                    oversized = True
                    _core.warn(
                        f"not scanning {path}: its new content is over secrets.max_file_bytes"
                    )
                else:
                    lines.append((new_line, text.decode("utf-8", errors="replace")))
            new_line += 1
        elif raw.startswith(b" "):
            new_line += 1
        # "-" (removed) and "\ No newline at end of file" lines are ignored.
    yield from flush()


def _git_stream(args: list[str]) -> subprocess.Popen:
    return subprocess.Popen(
        ["git", "-c", "core.quotePath=false", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_core._git_env(),
    )


def _stream_chunks(args: list[str], max_bytes: int | None) -> Iterator[Chunk]:
    proc = _git_stream(args)
    assert proc.stdout is not None
    stdout: IO[bytes] = proc.stdout
    try:
        yield from parse_patch(stdout, max_bytes)
    finally:
        stdout.close()
        err = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        if proc.wait() not in (0, None) and err.strip():
            _core.warn(f"git {args[0]} failed: {err.strip()}")


# --------------------------------------------------------------------------- #
# Sources
# --------------------------------------------------------------------------- #
def staged_diff(max_bytes: int | None = None) -> Iterator[Chunk]:
    """Lines added by the staged changes (index vs HEAD, or vs empty tree)."""
    yield from _stream_chunks(["diff", "--cached", *_PATCH_ARGS], max_bytes)


def commit_range(rev_args: list[str], max_bytes: int | None = None) -> Iterator[Chunk]:
    """Lines added by each commit in a range, tagged with the commit id.

    Merge commits contribute no diff of their own (their parents' commits are
    scanned instead).
    """
    args = ["log", "-p", "--format=%x01%H", *_PATCH_ARGS, *rev_args, "--"]
    yield from _stream_chunks(args, max_bytes)


def _whole(path: str, data: bytes, max_bytes: int | None) -> Chunk | None:
    if max_bytes is not None and len(data) > max_bytes:
        _core.warn(
            f"not scanning {path}: {_core.human_size(len(data))} is over secrets.max_file_bytes"
        )
        return None
    if _core.is_probably_binary(data):
        return None
    text = data.decode("utf-8", errors="replace")
    return Chunk(path, list(enumerate(text.splitlines(), start=1)))


def files(paths: list[str], max_bytes: int | None = None) -> Iterator[Chunk]:
    """Whole files as staged in the index (on-disk content for untracked paths)."""
    contents = _core.read_staged(paths)
    for path in paths:
        data = contents.get(path)
        if data is None:
            continue
        chunk = _whole(path, data, max_bytes)
        if chunk is not None:
            yield chunk


def all_files(max_bytes: int | None = None, keep=lambda path: True) -> Iterator[Chunk]:
    """Every tracked file (index version), streamed through one cat-file."""
    entries = {
        path: entry
        for path, entry in _core.index_entries().items()
        if entry[0] != _core.GITLINK_MODE and keep(path)
    }
    by_sha: dict[str, list[str]] = {}
    for path, (_mode, sha) in entries.items():
        by_sha.setdefault(sha, []).append(path)
    for sha, data in _core.iter_blobs(by_sha):
        for path in by_sha.get(sha, []):
            chunk = _whole(path, data, max_bytes)
            if chunk is not None:
                yield chunk
