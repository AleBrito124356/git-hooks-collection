"""The `githooks` CLI (install / uninstall / run / list / doctor / version),
run as a subprocess exactly as a user would, including through the vendored
copy (`python .githooks/githooks-run.py <command>`) with no pip install."""

from __future__ import annotations

import subprocess
import sys

import pytest
from _helpers import GH_TOKEN, ROOT, git, write

CLI = [sys.executable, "-m", "pre_commit_hooks"]


def run(cwd, *args, cli=None):
    return subprocess.run(
        [*(cli or CLI), *args],
        cwd=str(cwd),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        check=False,
    )


@pytest.fixture
def plain(tmp_path, git_env):
    path = tmp_path / "plain"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    return path


def test_version(plain):
    from pre_commit_hooks import __version__

    assert run(plain, "--version").stdout.strip() == f"git-hooks-collection {__version__}"
    assert run(plain, "version").stdout.strip() == f"git-hooks-collection {__version__}"


def test_install_list_uninstall(plain):
    result = run(plain, "install", "--hooks", "secrets,no-fixup")
    assert result.returncode == 0, result.stderr
    listing = run(plain, "list").stdout
    rows = {line.split()[0]: line.split()[2] for line in listing.splitlines()[4:] if line.strip()}
    assert rows["secrets"] == "yes" and rows["no-fixup"] == "yes"
    assert rows["lint"] == "-" and rows["tests"] == "-"
    assert "install: native (.githooks/)" in listing

    result = run(plain, "uninstall")
    assert result.returncode == 0
    assert not (plain / ".githooks").exists()


def test_list_flags_a_missing_wrapper_and_unknown_names(plain):
    assert run(plain, "install", "--hooks", "secrets").returncode == 0
    write(plain / ".githooks.yaml", "hooks:\n  pre-push: [no-fixup, nofixup]\n")
    listing = run(plain, "list").stdout
    assert any(line.startswith("no-fixup") and "no hook" in line for line in listing.splitlines())
    assert "unknown check 'nofixup' under hooks.pre-push; did you mean 'no-fixup'?" in listing


def test_run_single_check_from_a_subdirectory(plain):
    write(plain / "src" / "leak.py", f'token = "{GH_TOKEN}"\n')
    git(plain, "add", "src/leak.py")
    result = run(plain / "src", "run", "secrets")
    assert result.returncode == 1
    assert "src/leak.py:1:10" in result.stderr
    # Relative file arguments are resolved from where you are.
    result = run(plain / "src", "run", "secrets", "leak.py")
    assert result.returncode == 1 and "src/leak.py:1:10" in result.stderr


def test_run_all_files(plain):
    git(plain, "commit", "-q", "--allow-empty", "-m", "chore: root")
    write(plain / "old.py", f'token = "{GH_TOKEN}"\n')
    write(plain / "big.bin", "x" * 2000)
    write(plain / ".githooks.yaml", "large_files:\n  max_bytes: 1000\n")
    git(plain, "add", ".")
    git(plain, "commit", "-q", "--no-verify", "-m", "chore: legacy")
    # Nothing is staged, so a normal run finds nothing...
    assert run(plain, "run", "secrets").returncode == 0
    # ...but an audit of the tracked tree does.
    audit = run(plain, "run", "secrets", "--all-files")
    assert audit.returncode == 1 and "old.py:1:10" in audit.stderr
    sizes = run(plain, "run", "large-files", "--all-files")
    assert sizes.returncode == 1 and "big.bin" in sizes.stderr
    assert run(plain, "run", "branch-protect", "--all-files").returncode == 2
    assert run(plain, "run", "format", "--all-files").returncode == 2


def test_run_a_whole_stage(plain):
    write(plain / ".githooks.yaml", "hooks:\n  pre-commit: [branch-protect]\n")
    result = run(plain, "run", "pre-commit")
    assert result.returncode == 1 and "protected branch 'main'" in result.stderr


def test_unknown_check_suggestion(plain):
    result = run(plain, "run", "secret")
    assert result.returncode == 2 and "did you mean 'secrets'" in result.stderr


def test_vendored_copy_runs_the_cli_without_pip(plain):
    assert run(plain, "install", "--all").returncode == 0
    vendored = [sys.executable, str(plain / ".githooks" / "githooks-run.py")]
    result = run(plain, "doctor", cli=vendored)
    assert "git-hooks-collection" in result.stdout and "doctor:" in result.stdout
    assert "vendored copy matches" in result.stdout
    assert run(plain, "list", cli=vendored).returncode == 0


def test_root_dispatcher_script_is_the_same_cli(plain):
    root_script = [sys.executable, str(ROOT / "githooks-run.py")]
    assert run(plain, "version", cli=root_script).returncode == 0
