"""Helpers that drive real `git commit` / `git push` through installed hooks.

Each test gets its own copy of a repository with a local bare remote, the
hooks installed natively by ``install.py --all`` (so the vendored copy, the
stage wrappers and core.hooksPath are the real thing), ``main`` already pushed
and a feature branch checked out. Git itself runs the hooks through ``sh``;
the wrappers pick up the test interpreter through GITHOOKS_PYTHON.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

from _helpers import ROOT, git, write


class Result(NamedTuple):
    rc: int
    out: str  # stdout + stderr


class HookedRepo:
    def __init__(self, path: Path, remote: Path):
        self.path = path
        self.remote = remote

    # -- plumbing ---------------------------------------------------------- #
    def env(self, **extra: str) -> dict[str, str]:
        env = dict(os.environ)
        env.update(extra)
        return env

    def run(
        self, *args: str, env: dict[str, str] | None = None, input: str | None = None
    ) -> Result:
        proc = subprocess.run(
            list(args),
            cwd=str(self.path),
            env=env or dict(os.environ),
            input=input,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        return Result(proc.returncode, proc.stdout)

    def git(self, *args: str, **env: str) -> Result:
        return self.run("git", *args, env=self.env(**env) if env else None)

    def ok(self, *args: str) -> str:
        return git(self.path, *args).stdout

    def write(self, rel: str, text: str) -> Path:
        return write(self.path / rel, text)

    def config(self, text: str) -> None:
        """Replace .githooks.yaml (the hooks read it on every run)."""
        self.write(".githooks.yaml", text)

    # -- porcelain ------------------------------------------------------------ #
    def commit(self, message: str, *files: str, **env: str) -> Result:
        if files:
            git(self.path, "add", "--", *files)
        return self.git("commit", "-m", message, **env)

    def commit_file(self, rel: str, text: str, message: str, **env: str) -> Result:
        self.write(rel, text)
        return self.commit(message, rel, **env)

    def quiet_commit(self, rel: str, text: str, message: str) -> str:
        """Commit bypassing the hooks (test setup); returns the new sha."""
        self.write(rel, text)
        git(self.path, "add", "--", rel)
        git(self.path, "commit", "-q", "--no-verify", "-m", message)
        return self.head()

    def push(self, *args: str, **env: str) -> Result:
        return self.git("push", "origin", *args, **env)

    def head(self) -> str:
        return self.ok("rev-parse", "HEAD").strip()

    def show(self, spec: str) -> str:
        return self.ok("show", spec)

    def log(self, fmt: str = "%s", *revs: str) -> list[str]:
        out = self.ok("log", f"--format={fmt}", *(revs or ("HEAD",)))
        return [line for line in out.split("\n") if line]


def install(path: Path, *args: str, env: dict[str, str] | None = None) -> Result:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "install.py"), "--target", str(path), *args],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        check=False,
    )
    return Result(proc.returncode, proc.stdout)


# The integration config: only the checks under test run, so each test is
# deterministic whatever formatters/linters happen to be on PATH.
BASE_CONFIG = """\
version: 1
hooks:
  pre-commit: [secrets, large-files, branch-protect]
  prepare-commit-msg: [issue-prefix]
  commit-msg: [conventional-commit]
  pre-push: [no-fixup]
"""
