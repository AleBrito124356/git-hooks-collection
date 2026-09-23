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

ROOT = Path(__file__).resolve().parents[1]

# A GitHub classic PAT shape, assembled at runtime so no test file ever
# contains a scannable secret (the repository's push-safety convention).
GH_TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"

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
    env: dict[str, str] | None = None,
    input: str | None = None,
) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        env=env,
        input=input,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if check and proc.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed ({proc.returncode}):\n{proc.stdout}\n{proc.stderr}"
        )
    return proc


GITCONFIG = (
    "[user]\n"
    "\tname = Test User\n"
    "\temail = test@example.com\n"
    "[init]\n"
    "\tdefaultBranch = main\n"
    "[core]\n"
    "\tautocrlf = false\n"
    "[commit]\n"
    "\tgpgsign = false\n"
    "[advice]\n"
    "\tdetachedHead = false\n"
)


def isolated_env(home: Path, base: dict[str, str] | None = None) -> dict[str, str]:
    """An environment with a private git config and no inherited hook settings."""
    write(home / ".gitconfig", GITCONFIG)
    env = dict(os.environ if base is None else base)
    for var in _SCRUB:
        env.pop(var, None)
    env["GIT_CONFIG_GLOBAL"] = str(home / ".gitconfig")
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    # Hooks run with the interpreter running the tests (it has PyYAML when the
    # dev extra is installed; GITHOOKS_FORCE_MINIYAML=1 switches the parser).
    env["GITHOOKS_PYTHON"] = sys.executable
    env["NO_COLOR"] = "1"
    return env


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def fake_tool(bin_dir: Path, name: str, script: str) -> None:
    """Put an executable ``name`` on a PATH dir that runs ``script`` with this Python.

    On Windows it is a ``.cmd`` shim -- the same shape as npm's prettier/eslint
    shims, which the hooks must be able to launch.
    """
    body = write(bin_dir / f"{name}_impl.py", script)
    if os.name == "nt":
        write(bin_dir / f"{name}.cmd", f'@"{sys.executable}" "{body}" %*\n')
    else:
        launcher = write(bin_dir / name, f'#!/bin/sh\nexec "{sys.executable}" "{body}" "$@"\n')
        launcher.chmod(0o755)


# A deterministic "formatter": collapses runs of spaces ("z   =   1" -> "z = 1").
SQUASH_SPACES = (
    "import re, sys\n"
    "for p in sys.argv[1:]:\n"
    "    s = open(p, encoding='utf-8').read()\n"
    "    open(p, 'w', encoding='utf-8', newline='').write(re.sub(' +', ' ', s))\n"
)

# A "linter" that always complains.
FAIL_ALWAYS = "import sys\nprint('lint failed for', sys.argv[1:])\nsys.exit(1)\n"


def find_sh() -> str | None:
    """A POSIX sh: on PATH, or the one bundled with Git for Windows."""
    import shutil

    found = shutil.which("sh")
    if found:
        return found
    exec_path = subprocess.run(
        ["git", "--exec-path"], stdout=subprocess.PIPE, encoding="utf-8", check=False
    ).stdout.strip()
    if exec_path:
        base = Path(exec_path)
        for up in range(1, 5):
            root = base.parents[up - 1] if up <= len(base.parents) else None
            if root is None:
                break
            for rel in ("bin/sh.exe", "usr/bin/sh.exe"):
                if (root / rel).is_file():
                    return str(root / rel)
    return None
