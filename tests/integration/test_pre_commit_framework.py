"""The pre-commit framework path ("Path 2" in the README), end to end.

A snapshot of this working tree is committed to a scratch repository and
referenced as ``repo: <path>, rev: <sha>``, exactly as a user would reference
the GitHub repository. pre-commit then builds its own virtualenv and runs the
hooks from ``.pre-commit-hooks.yaml``.

Skipped unless ``pre-commit`` is importable, and skipped (not failed) if the
hook environment cannot be built -- the first build pip-installs this package,
which needs setuptools from the package index or pip's cache. The snapshot is
committed with fixed dates into pytest's cache directory, so an unchanged tree
gets the same SHA and pre-commit reuses the environment on the next run.
Deselect with ``-m "not framework"``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _helpers import GH_TOKEN, ROOT, git, isolated_env, write

pytest.importorskip("pre_commit")
pytestmark = pytest.mark.framework

FIXED_DATE = "2026-01-01T00:00:00+00:00"

HOOK_IDS = [
    "githooks-secrets",
    "githooks-issue-prefix",
    "githooks-conventional-commit",
    "githooks-no-fixup",
    "githooks-secrets-push",
]


def _force_remove(func, path, _exc):
    os.chmod(path, 0o700)  # git marks object files read-only on Windows
    func(path)


def _run(cmd, cwd, env):
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        check=False,
    )


@pytest.fixture(scope="module")
def framework(tmp_path_factory, request):
    base = tmp_path_factory.mktemp("framework")
    env = isolated_env(base / "home")
    cache = Path(request.config.cache.mkdir("githooks-framework"))
    env["PRE_COMMIT_HOME"] = str(cache / "pre-commit-home")
    env.pop("GITHOOKS_PYTHON", None)

    # Snapshot the working tree (tracked + new, not ignored) into a repo at a
    # stable path, with fixed dates: same tree -> same SHA -> cached env.
    src = cache / "src"
    if src.exists():
        shutil.rmtree(src, onerror=_force_remove)
    files = git(ROOT, "ls-files", "-co", "--exclude-standard", "-z").stdout.split("\0")
    for rel in filter(None, files):
        if (ROOT / rel).is_file():
            (src / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / rel, src / rel)
    git(src, "init", "-q", "-b", "main", env=env)
    git(src, "add", "-A", env=env)
    dated = {**env, "GIT_AUTHOR_DATE": FIXED_DATE, "GIT_COMMITTER_DATE": FIXED_DATE}
    git(src, "commit", "-q", "--no-verify", "-m", "snapshot", env=dated)
    rev = git(src, "rev-parse", "HEAD", env=env).stdout.strip()

    work, remote = base / "work", base / "remote.git"
    work.mkdir()
    git(base, "init", "-q", "--bare", "-b", "main", str(remote), env=env)
    git(work, "init", "-q", "-b", "main", env=env)
    git(work, "remote", "add", "origin", str(remote), env=env)
    hooks = "\n".join(f"      - id: {hook_id}" for hook_id in HOOK_IDS)
    write(
        work / ".pre-commit-config.yaml",
        f"repos:\n  - repo: {src.as_posix()}\n    rev: {rev}\n    hooks:\n{hooks}\n",
    )
    pre_commit = [sys.executable, "-m", "pre_commit"]
    install = _run(
        [
            *pre_commit,
            "install",
            "--hook-type",
            "pre-commit",
            "--hook-type",
            "prepare-commit-msg",
            "--hook-type",
            "commit-msg",
            "--hook-type",
            "pre-push",
        ],
        work,
        env,
    )
    assert install.returncode == 0, install.stdout
    build = _run([*pre_commit, "install-hooks"], work, env)
    if build.returncode != 0:
        pytest.skip(f"pre-commit could not build the hook environment:\n{build.stdout[-800:]}")
    git(work, "add", ".pre-commit-config.yaml", env=env)
    git(work, "commit", "-q", "--no-verify", "-m", "chore: pre-commit config", env=env)
    git(work, "push", "-q", "--no-verify", "-u", "origin", "main", env=env)
    return work, env


def _git(work, env, *args, **extra):
    return _run(["git", *args], work, {**env, **extra})


def test_secret_blocked_through_the_framework(framework):
    work, env = framework
    _git(work, env, "switch", "-q", "-c", "feature/leak")
    write(work / "leak.py", f'token = "{GH_TOKEN}"\n')
    _git(work, env, "add", "leak.py")
    result = _git(work, env, "commit", "-m", "feat: leak")
    assert result.returncode != 0, result.stdout
    assert "leak.py:1:10" in result.stdout
    _git(work, env, "reset", "-q", "--hard")


def test_ticket_branch_commit_gets_trailer_and_passes(framework):
    work, env = framework
    _git(work, env, "switch", "-q", "-c", "feature/ABC-42-framework", "main")
    write(work / "a.txt", "a\n")
    _git(work, env, "add", "a.txt")
    result = _git(work, env, "commit", "-m", "feat: through the framework")
    assert result.returncode == 0, result.stdout
    body = _git(work, env, "log", "-1", "--format=%B").stdout.strip()
    assert body == "feat: through the framework\n\nRefs: ABC-42"


def test_amend_skips_trailer_via_pre_commit_env(framework):
    # pre-commit only exposes the prepare-commit-msg source in an env var.
    work, env = framework
    _git(work, env, "switch", "-q", "-c", "feature/XYZ-7-amend", "main")
    write(work / "b.txt", "b\n")
    _git(work, env, "add", "b.txt")
    # prepare-commit-msg is not skipped by --no-verify; pre-commit's SKIP is.
    first = _git(work, env, "commit", "-m", "feat: first", SKIP="githooks-issue-prefix")
    assert first.returncode == 0, first.stdout
    assert "Refs:" not in _git(work, env, "log", "-1", "--format=%B").stdout
    result = _git(work, env, "commit", "--amend", "-C", "HEAD")
    assert result.returncode == 0, result.stdout
    assert "Refs:" not in _git(work, env, "log", "-1", "--format=%B").stdout


def test_wip_push_blocked_through_the_framework(framework):
    # Regression: pre-commit forwards no stdin, so no-fixup never blocked.
    work, env = framework
    _git(work, env, "switch", "-q", "-c", "feature/wip", "main")
    for i, msg in enumerate(["WIP: nope", "fixup! feat: one"]):
        write(work / f"w{i}.txt", msg)
        _git(work, env, "add", f"w{i}.txt")
        assert _git(work, env, "commit", "-q", "--no-verify", "-m", msg).returncode == 0
    result = _git(work, env, "push", "-u", "origin", "feature/wip")
    assert result.returncode != 0, result.stdout
    assert "WIP: nope" in result.stdout
    remote_heads = _git(work, env, "ls-remote", "--heads", "origin").stdout
    assert "feature/wip" not in remote_heads


def test_secret_committed_with_no_verify_is_blocked_at_push(framework):
    work, env = framework
    _git(work, env, "switch", "-q", "-c", "feature/sneaky", "main")
    write(work / "sneaky.py", f'token = "{GH_TOKEN}"\n')
    _git(work, env, "add", "sneaky.py")
    assert _git(work, env, "commit", "-q", "--no-verify", "-m", "feat: sneaky").returncode == 0
    sha = _git(work, env, "rev-parse", "HEAD").stdout.strip()
    result = _git(work, env, "push", "-u", "origin", "feature/sneaky")
    assert result.returncode != 0, result.stdout
    assert f"{sha[:10]} sneaky.py:1:10" in result.stdout


def test_config_file_is_honoured_by_framework_hooks(framework):
    work, env = framework
    _git(work, env, "switch", "-q", "-c", "feature/cfg", "main")
    write(work / ".githooks.yaml", "secrets:\n  exclude: ['fixtures/*']\n")
    write(work / "fixtures" / "tok.txt", f"{GH_TOKEN}\n")
    _git(work, env, "add", ".githooks.yaml", "fixtures/tok.txt")
    result = _git(work, env, "commit", "-m", "test: fixtures")
    assert result.returncode == 0, result.stdout
    assert Path(work / "fixtures" / "tok.txt").is_file()
