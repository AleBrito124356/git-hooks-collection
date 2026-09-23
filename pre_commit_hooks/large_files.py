"""Block oversized files from entering history and point at Git LFS.

Runs at ``pre-commit``. A big binary committed once lives in history forever and
bloats every clone. This catches it before the commit. Sizes are read from the
staged blobs (one ``git cat-file --batch-check`` for all files) so a partially
staged file is measured as it will actually be committed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from . import _core

CHECK_NAME = "large-files"


def _lfs_pattern(path: str) -> str:
    suffix = Path(path).suffix
    return f"*{suffix}" if suffix else path


def check_files(
    files: List[str], config: Optional[dict] = None
) -> Tuple[List[Tuple[str, int]], List[Tuple[str, int]], int]:
    config = config or _core.load_config()
    max_bytes = int(_core.cfg(config, "large_files.max_bytes", 5_242_880))
    warn_bytes = int(_core.cfg(config, "large_files.warn_bytes", 1_048_576))

    env_override = os.environ.get("GITHOOKS_MAX_FILE_BYTES")
    if env_override:
        try:
            max_bytes = int(env_override)
        except ValueError:
            _core.warn(f"ignoring GITHOOKS_MAX_FILE_BYTES={env_override!r} (not an integer)")

    sizes = _core.file_sizes(files)
    blocked = []
    warned = []
    for path in files:
        size = sizes.get(path)
        if size is None:
            continue
        if size > max_bytes:
            blocked.append((path, size))
        elif size > warn_bytes:
            warned.append((path, size))
    return blocked, warned, max_bytes


def main(argv: Optional[List[str]] = None, stdin_data: Optional[str] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if _core.skip_requested(CHECK_NAME):
        return 0
    files = [a for a in argv if not a.startswith("-")]
    if not files:
        files = _core.staged_files()
    if not files:
        return 0

    config = _core.load_config()
    blocked, warned, max_bytes = check_files(files, config)

    for path, size in warned:
        _core.warn(f"{path} is {_core.human_size(size)} (large, but under the limit)")

    if not blocked:
        return 0

    _core.header("\nFiles exceed the size limit and were blocked:\n")
    for path, size in blocked:
        _core.error(f"{path}  {_core.human_size(size)} (limit {_core.human_size(max_bytes)})")

    if _core.cfg(config, "large_files.lfs_hint", True):
        example = _lfs_pattern(blocked[0][0])
        print(
            "\n  Options:\n"
            f'    - Track large binaries with Git LFS:  git lfs track "{example}"\n'
            "      then re-add the file and commit the updated .gitattributes.\n"
            "    - Or keep the asset out of git entirely and add it to .gitignore.\n"
            "    - Raise the limit in .githooks.yaml (large_files.max_bytes) if this is expected.\n"
            "    - Bypass once: git commit --no-verify\n",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
