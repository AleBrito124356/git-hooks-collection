"""Helpers shared by the unit and integration tests: git runners, isolation.

Every git process the tests start -- and every hook git starts in turn -- sees a
private global config and no system config, so the developer's own settings
(core.hooksPath, commit templates, autocrlf, signing) can never leak in.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional

ROOT = Path(__file__).resolve().parents[1]

# Variables that would change hook behaviour if inherited from the outer shell
# (including from a git hook that happens to be running this test suite).
_SCRUB = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_PREFIX",
    "GITHOOKS_SKIP",
    "GITHOOKS_CONFIG",
    "GITHOOKS_PROBE",
    "GITHOOKS_DEBUG",
    "GITHOOKS_MAX_FILE_BYTES",
    "ALLOW_COMMIT_TO_PROTECTED",
    "SKIP_TESTS",
    "PRE_COMMIT",
    "PRE_COMMIT_FROM_REF",
    "PRE_COMMIT_TO_REF",
    "PRE_COMMIT_ORIGIN",
    "PRE_COMMIT_SOURCE",
    "PRE_COMMIT_LOCAL_BRANCH",
    "PRE_COMMIT_REMOTE_BRANCH",
    "PRE_COMMIT_REMOTE_NAME",
    "PRE_COMMIT_REMOTE_URL",
    "PRE_COMMIT_COMMIT_MSG_SOURCE",
    "PYTHONPATH",
)


def git(
    cwd: Path,
    *args: str,
    check: bool = True,
    env: Optional[Dict[str, str]] = None,
    input: Optional[str] = None,  # noqa: A002 - mirrors subprocess.run
) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        env=env,
        input=input,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if check and proc.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed ({proc.returncode}):\n{proc.stdout}\n{proc.stderr}"
        )
    return proc


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path
