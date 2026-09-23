"""The standalone hooks/* wrappers, run through a real POSIX sh.

On Windows this is Git for Windows' sh, which is exactly where the 0.1.0
wrappers failed with ``ModuleNotFoundError: No module named 'pre_commit_hooks'``.
"""

from __future__ import annotations

import subprocess

import pytest
from _helpers import GH_TOKEN, ROOT, find_sh, git, write

SH = find_sh()
pytestmark = pytest.mark.skipif(SH is None, reason="no POSIX sh available")

WRAPPERS = {
    "pre-commit-secrets": "secrets",
    "pre-commit-format": "format",
    "pre-commit-lint": "lint",
    "pre-commit-largefiles": "large-files",
    "pre-commit-branch-protect": "branch-protect",
    "commit-msg-conventional": "conventional-commit",
    "pre-push-tests": "tests",
    "pre-push-nofixup": "no-fixup",
    "prepare-commit-msg-issue": "issue-prefix",
}


def run_wrapper(repo, name, *args, env=None):
    return subprocess.run(
        [SH, str(ROOT / "hooks" / name), *args],
        cwd=str(repo),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        check=False,
    )


@pytest.mark.parametrize("wrapper", sorted(WRAPPERS))
def test_wrapper_targets_a_real_check(wrapper):
    text = (ROOT / "hooks" / wrapper).read_text(encoding="utf-8")
    assert f'githooks-run.py" run {WRAPPERS[wrapper]} "$@"' in text
    assert "export PYTHONPATH" not in text
    syntax = subprocess.run([SH, "-n", str(ROOT / "hooks" / wrapper)], check=False)
    assert syntax.returncode == 0


def test_executable_bit_is_committed():
    modes = git(ROOT, "ls-files", "-s", "hooks", "githooks-run.py", "install.py").stdout
    for line in modes.splitlines():
        assert line.startswith("100755"), line


@pytest.mark.parametrize("pythonpath", [None, "", "C:\\nowhere;D:\\else", "/tmp/a:/tmp/b"])
def test_secrets_wrapper_runs_and_blocks(repo, pythonpath, monkeypatch):
    # Regression: PYTHONPATH="$here/..:" was never translated by MSYS, so the
    # module could not be found; any inherited PYTHONPATH must not matter.
    if pythonpath is not None:
        monkeypatch.setenv("PYTHONPATH", pythonpath)
    write(repo / "leak.py", f'token = "{GH_TOKEN}"\n')
    git(repo, "add", "leak.py")
    proc = run_wrapper(repo, "pre-commit-secrets")
    assert proc.returncode == 1, proc.stdout
    assert "ModuleNotFoundError" not in proc.stdout
    assert "leak.py:1:10" in proc.stdout


def test_wrappers_honour_githooks_skip(repo, monkeypatch):
    write(repo / "leak.py", f'token = "{GH_TOKEN}"\n')
    git(repo, "add", "leak.py")
    monkeypatch.setenv("GITHOOKS_SKIP", "secrets")
    proc = run_wrapper(repo, "pre-commit-secrets")
    assert proc.returncode == 0, proc.stdout
    assert "skipping 'secrets'" in proc.stdout


def test_branch_protect_and_commit_msg_wrappers(repo):
    assert run_wrapper(repo, "pre-commit-branch-protect").returncode == 1
    msg = write(repo / "MSG", "add stuff\n")
    proc = run_wrapper(repo, "commit-msg-conventional", str(msg))
    assert proc.returncode == 1 and "Conventional Commits" in proc.stdout
    write(msg, "feat: add stuff\n")
    assert run_wrapper(repo, "commit-msg-conventional", str(msg)).returncode == 0


def test_wrapper_uses_githooks_python(repo, monkeypatch, tmp_path):
    fake = write(tmp_path / "fake-python", "#!/bin/sh\necho FAKE-PYTHON-RAN\nexit 7\n")
    fake.chmod(0o755)
    monkeypatch.setenv("GITHOOKS_PYTHON", str(fake))
    proc = run_wrapper(repo, "pre-commit-secrets")
    assert proc.returncode == 7 and "FAKE-PYTHON-RAN" in proc.stdout
