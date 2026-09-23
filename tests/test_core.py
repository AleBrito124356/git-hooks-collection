"""Tests for the shared plumbing in _core: git decoding, content sniffing,
push ranges and config helpers."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from _helpers import ROOT, git, write

from pre_commit_hooks import _core, _miniyaml


# --------------------------------------------------------------------------- #
# Binary detection
# --------------------------------------------------------------------------- #
def test_cjk_source_is_text():
    # Regression: every byte >0x7F used to count as "non-text", so a file with
    # Chinese comments was classed as binary and never scanned.
    data = ("# 这是一个中文注释，用于测试扫描器\n" * 3 + 'token = "x"\n').encode()
    assert not _core.is_probably_binary(data)


def test_accented_text_is_text():
    assert not _core.is_probably_binary("configuración = 'ñandú'\n".encode() * 50)


def test_multibyte_char_cut_at_sample_boundary_is_text():
    data = b"a" * 7999 + "é".encode() + b"rest"
    assert not _core.is_probably_binary(data)


def test_nul_byte_is_binary():
    assert _core.is_probably_binary(b"PK\x03\x04\x00\x00binary")


def test_random_high_bytes_are_binary():
    data = bytes((i * 37 + 11) % 256 for i in range(4000)).replace(b"\0", b"\x01")
    assert _core.is_probably_binary(data)


# --------------------------------------------------------------------------- #
# Git plumbing against a real repository
# --------------------------------------------------------------------------- #
def test_staged_files_decodes_non_ascii_names(repo):
    # Regression: git's UTF-8 output was decoded as cp1252 on Windows.
    write(repo / "configuración.py", "x = 1\n")
    write(repo / "日本語.txt", "y\n")
    git(repo, "add", ".")
    assert sorted(_core.staged_files()) == ["configuración.py", "日本語.txt"]
    blobs = _core.read_staged(["configuración.py"])
    assert blobs["configuración.py"] == b"x = 1\n"


def test_staged_files_include_renames(repo):
    # Regression: --diff-filter=ACM left renamed files out of every check.
    write(repo / "settings.py", "".join(f"x{i} = {i}\n" for i in range(20)))
    git(repo, "add", "settings.py")
    git(repo, "commit", "-q", "-m", "feat: settings")
    git(repo, "mv", "settings.py", "settings_prod.py")
    with open(repo / "settings_prod.py", "a", encoding="utf-8") as fh:
        fh.write("extra = 1\n")
    git(repo, "add", "settings_prod.py")
    status = git(repo, "diff", "--cached", "--name-status").stdout
    assert status.startswith("R")
    assert _core.staged_files() == ["settings_prod.py"]


def test_read_staged_prefers_index_over_worktree(repo):
    write(repo / "a.py", "staged\n")
    git(repo, "add", "a.py")
    write(repo / "a.py", "staged\nunstaged\n")
    assert _core.read_staged(["a.py"])["a.py"] == b"staged\n"
    assert _core.unstaged_files(["a.py"]) == ["a.py"]


def test_read_staged_falls_back_to_disk_for_untracked(repo, tmp_path):
    outside = write(tmp_path / "outside.txt", "disk\n")
    assert _core.read_staged([str(outside)])[str(outside)] == b"disk\n"


def test_file_sizes_batch(repo):
    write(repo / "big.bin", "x" * 1234)
    git(repo, "add", "big.bin")
    sizes = _core.file_sizes(["big.bin", "README.md", "missing"])
    assert sizes == {"big.bin": 1234, "README.md": 6}


def test_literal_pathspecs(repo):
    # A file literally named "*.py" must not be treated as a glob.
    write(repo / "a.py", "a\n")
    write(repo / "b.py", "b\n")
    git(repo, "add", "a.py", "b.py")
    git(repo, "commit", "-q", "-m", "feat: files")
    assert _core.index_entries(["*.py"]) == {}


# --------------------------------------------------------------------------- #
# Pushed ranges
# --------------------------------------------------------------------------- #
def _commit(repo: Path, name: str, msg: str) -> str:
    write(repo / name, msg + "\n")
    git(repo, "add", name)
    git(repo, "commit", "-q", "--no-verify", "-m", msg)
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def _subjects(ranges):
    out = []
    for rev_args in ranges:
        out += git(Path.cwd(), "log", "--format=%s", *rev_args).stdout.split("\n")
    return [s for s in out if s]


def test_new_branch_range_is_argv_list(repo):
    # Regression: "sha --not --remotes" was passed as ONE argument; git
    # rejected it and new-branch pushes were never checked.
    base = git(repo, "rev-parse", "HEAD").stdout.strip()
    tip = _commit(repo, "a.txt", "WIP: half done")
    stdin = f"refs/heads/x {tip} refs/heads/x {_core.ZERO_SHA}\n"
    ranges = _core.pushed_ranges(stdin)
    assert ranges == [[tip, "--not", "--remotes"]]
    # No remotes at all: the whole history is new.
    assert _subjects(ranges) == ["WIP: half done", "chore: init"]
    assert base


def test_existing_branch_range(repo):
    base = git(repo, "rev-parse", "HEAD").stdout.strip()
    tip = _commit(repo, "a.txt", "feat: a")
    ranges = _core.pushed_ranges(f"refs/heads/main {tip} refs/heads/main {base}\n")
    assert ranges == [[f"{base}..{tip}"]]
    assert _subjects(ranges) == ["feat: a"]


def test_unknown_remote_sha_falls_back_to_not_remotes(repo):
    tip = _commit(repo, "a.txt", "feat: a")
    ranges = _core.pushed_ranges(f"refs/heads/main {tip} refs/heads/main {'1' * 40}\n")
    assert ranges == [[tip, "--not", "--remotes"]]


def test_branch_deletion_is_ignored(repo):
    ranges = _core.pushed_ranges(f"(delete) {_core.ZERO_SHA} refs/heads/x {'a' * 40}\n")
    assert ranges == []


def test_pre_commit_framework_env(repo, monkeypatch):
    # Regression: pre-commit does not forward stdin; it exports the range.
    base = git(repo, "rev-parse", "HEAD").stdout.strip()
    tip = _commit(repo, "a.txt", "fixup! feat: a")
    monkeypatch.setenv("PRE_COMMIT_FROM_REF", base)
    monkeypatch.setenv("PRE_COMMIT_TO_REF", tip)
    assert _core.pushed_ranges("") == [[f"{base}..{tip}"]]


def test_pre_commit_framework_root_push(repo, monkeypatch):
    monkeypatch.setenv("PRE_COMMIT", "1")
    monkeypatch.setenv("PRE_COMMIT_LOCAL_BRANCH", "refs/heads/main")
    assert _core.pushed_ranges("") == [["refs/heads/main", "--not", "--remotes"]]


def test_manual_run_without_upstream(repo):
    assert _core.pushed_ranges("") == [["HEAD", "--not", "--remotes"]]


# --------------------------------------------------------------------------- #
# Config helpers
# --------------------------------------------------------------------------- #
def test_defaults_are_the_shipped_template():
    template = (ROOT / "pre_commit_hooks" / "templates" / "githooks.yaml").read_text("utf-8")
    assert _core.DEFAULTS == _miniyaml.safe_load(template)


def test_repo_config_is_the_template():
    # The documented .githooks.yaml and the installer's template must not drift.
    shipped = (ROOT / ".githooks.yaml").read_text(encoding="utf-8")
    template = (ROOT / "pre_commit_hooks" / "templates" / "githooks.yaml").read_text("utf-8")
    assert shipped == template


def test_force_miniyaml_switch(monkeypatch):
    monkeypatch.setenv("GITHOOKS_FORCE_MINIYAML", "1")
    assert _core.yaml_backend() == "miniyaml"


def test_cfg_list_tolerates_null_and_scalars():
    config = {"a": {"none": None, "one": "x", "many": ["x", "y"]}}
    assert _core.cfg_list(config, "a.none") == []
    assert _core.cfg_list(config, "a.one") == ["x"]
    assert _core.cfg_list(config, "a.many") == ["x", "y"]
    assert _core.cfg_list(config, "a.missing") == []


def test_skip_requested_reads_env(monkeypatch):
    monkeypatch.setenv("GITHOOKS_SKIP", "lint, tests")
    assert _core.skip_requested("lint")
    assert _core.skip_requested("tests")
    assert not _core.skip_requested("secrets")


def test_language_buckets():
    # Regression: Markdown/YAML/JSON/CSS/HTML were routed to eslint.
    assert _core.language_of("src/app.ts") == "javascript"
    assert _core.language_of("README.md") == "web"
    assert _core.language_of("config.yaml") == "web"
    assert _core.language_of("tool.py") == "python"
    assert _core.language_of("Makefile") is None


@pytest.mark.skipif(os.name != "nt", reason="Windows command-line splitting")
def test_split_command_keeps_windows_paths():
    cmd = r'"C:\Program Files\Py\python.exe" -m pytest C:\repo\tests'
    assert _core.split_command(cmd) == [
        r"C:\Program Files\Py\python.exe",
        "-m",
        "pytest",
        r"C:\repo\tests",
    ]


@pytest.mark.skipif(os.name == "nt", reason="POSIX command-line splitting")
def test_split_command_posix():
    assert _core.split_command("pytest -q 'tests/a b'") == ["pytest", "-q", "tests/a b"]
