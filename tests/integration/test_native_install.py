"""The native installer against real repositories."""

from __future__ import annotations

import os

from _helpers import ROOT, git, write
from _repo import install

from pre_commit_hooks import _miniyaml

STAGES = ("pre-commit", "prepare-commit-msg", "commit-msg", "pre-push")


def _repo(tmp_path, name="r"):
    path = tmp_path / name
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    return path


def _hooks(path):
    return _miniyaml.safe_load((path / ".githooks.yaml").read_text(encoding="utf-8"))["hooks"]


def test_install_all(tmp_path, git_env):
    path = _repo(tmp_path)
    result = install(path, "--all")
    assert result.rc == 0, result.out
    hooks_dir = path / ".githooks"
    for stage in STAGES:
        wrapper = hooks_dir / stage
        assert wrapper.is_file()
        assert wrapper.read_bytes().startswith(b"#!/usr/bin/env sh\n")
        assert b"\r\n" not in wrapper.read_bytes()
        if os.name != "nt":
            assert os.access(wrapper, os.X_OK)
    assert (hooks_dir / ".gitignore").read_text(encoding="utf-8").startswith("__pycache__/")
    template = (ROOT / "pre_commit_hooks" / "templates" / "githooks.yaml").read_text("utf-8")
    assert (path / ".githooks.yaml").read_text(encoding="utf-8") == template
    assert git(path, "config", "core.hooksPath").stdout.strip() == ".githooks"


def test_vendored_bytecode_never_shows_as_untracked(hooked):
    # Regression: the first hook run left .githooks/pre_commit_hooks/__pycache__
    # untracked in the directory users are told to commit.
    assert hooked.commit_file("a.txt", "a\n", "feat: a").rc == 0
    assert list((hooked.path / ".githooks" / "pre_commit_hooks").glob("__pycache__")), (
        "the hooks should have run and written bytecode"
    )
    status = hooked.ok("status", "--porcelain", "--untracked-files=all")
    assert ".githooks" not in status, status


def test_reinstall_with_narrower_selection(tmp_path, git_env):
    # Regression: "installed 1 hook(s)" while all nine stayed active.
    path = _repo(tmp_path)
    assert install(path, "--all").rc == 0
    cfg = path / ".githooks.yaml"
    write(cfg, cfg.read_text(encoding="utf-8").replace("max_bytes: 5242880", "max_bytes: 777"))
    result = install(path, "--hooks", "secrets")
    assert result.rc == 0, result.out
    assert "installed 1 check(s)" in result.out
    assert _hooks(path) == {
        "pre-commit": ["secrets"],
        "prepare-commit-msg": [],
        "commit-msg": [],
        "pre-push": [],
    }
    assert "max_bytes: 777" in cfg.read_text(encoding="utf-8")
    wrappers = sorted(p.name for p in (path / ".githooks").iterdir() if p.name in STAGES)
    assert wrappers == ["pre-commit"]


def test_reinstall_without_selection_keeps_config_and_restores_wrappers(tmp_path, git_env):
    path = _repo(tmp_path)
    assert install(path, "--hooks", "secrets,no-fixup").rc == 0
    (path / ".githooks" / "pre-push").unlink()
    result = install(path)
    assert result.rc == 0, result.out
    assert "keeping existing .githooks.yaml" in result.out
    assert (path / ".githooks" / "pre-push").is_file()
    assert _hooks(path)["pre-push"] == ["no-fixup"]


def test_foreign_hooks_path_is_not_overwritten(tmp_path, git_env):
    path = _repo(tmp_path)
    git(path, "config", "core.hooksPath", ".husky")
    result = install(path, "--all")
    assert result.rc == 3, result.out
    assert "core.hooksPath is already set to '.husky'" in result.out
    assert git(path, "config", "core.hooksPath").stdout.strip() == ".husky"
    assert not (path / ".githooks").exists()

    result = install(path, "--all", "--force")
    assert result.rc == 0, result.out
    assert git(path, "config", "core.hooksPath").stdout.strip() == ".githooks"


def test_bypassed_git_lfs_hook_is_reported(tmp_path, git_env):
    path = _repo(tmp_path)
    write(path / ".git" / "hooks" / "pre-push", '#!/bin/sh\ngit lfs pre-push "$@"\n')
    result = install(path, "--all")
    assert result.rc == 0
    assert "stop running once core.hooksPath points at .githooks: pre-push" in result.out
    assert "git-lfs" in result.out


def test_uninstall(tmp_path, git_env):
    path = _repo(tmp_path)
    assert install(path, "--all").rc == 0
    result = install(path, "--uninstall")
    assert result.rc == 0, result.out
    assert not (path / ".githooks").exists()
    assert git(path, "config", "core.hooksPath", check=False).returncode == 1
    assert (path / ".githooks.yaml").is_file()


def test_unknown_hook_name_suggests_the_right_one(tmp_path, git_env):
    path = _repo(tmp_path)
    result = install(path, "--hooks", "secret")
    assert result.rc == 2
    assert "did you mean 'secrets'" in result.out


def test_pre_commit_mode_pins_an_existing_revision(tmp_path, git_env):
    path = _repo(tmp_path)
    head = git(ROOT, "rev-parse", "HEAD").stdout.strip()
    tag = git(ROOT, "describe", "--tags", "--exact-match", "HEAD", check=False).stdout.strip()
    result = install(path, "--mode", "pre-commit", "--hooks", "secrets,no-fixup")
    assert result.rc == 0, result.out
    text = (path / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert f"rev: {tag or head}" in text
    assert "--hook-type pre-commit --hook-type pre-push" in result.out
