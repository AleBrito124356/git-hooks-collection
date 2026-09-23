"""pre-commit stage through a real `git commit`: secrets, size, branch, format, lint."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest
from _helpers import FAIL_ALWAYS, GH_TOKEN, SQUASH_SPACES, fake_tool

LINE = f'token = "{GH_TOKEN}"\n'


def assert_blocked(result, *needles):
    assert result.rc != 0, result.out
    for needle in needles:
        assert needle in result.out, result.out


def test_clean_commit_passes(hooked):
    result = hooked.commit_file("app.py", "x = 1\n", "feat: add app")
    assert result.rc == 0, result.out
    assert hooked.log()[0] == "feat: add app"


def test_secret_is_blocked(hooked):
    before = hooked.head()
    result = hooked.commit_file("app.py", LINE, "feat: add app")
    assert_blocked(result, "app.py:1:10", "[github-token]", "GH013")
    assert GH_TOKEN not in result.out  # the report is redacted
    assert hooked.head() == before


def test_secret_in_renamed_file_is_blocked(hooked):
    hooked.quiet_commit("settings.py", "".join(f"x{i} = {i}\n" for i in range(20)), "feat: s")
    hooked.git("mv", "settings.py", "settings_prod.py")
    with open(hooked.path / "settings_prod.py", "a", encoding="utf-8") as fh:
        fh.write(LINE)
    result = hooked.commit("refactor: rename settings", "settings_prod.py")
    assert "R" in hooked.ok("diff", "--cached", "--name-status")[:1]
    assert_blocked(result, "settings_prod.py:21")


def test_secret_in_non_ascii_path_is_blocked(hooked):
    result = hooked.commit_file("configuración.py", LINE, "feat: conf")
    assert_blocked(result, "configuración.py:1:10")


def test_secret_in_cjk_commented_file_is_blocked(hooked):
    text = "# 这是一个中文注释，用于测试扫描器\n# 另一个中文注释行，更多的中文字符\n" * 2 + LINE
    result = hooked.commit_file("cjk.py", text, "feat: cjk")
    assert_blocked(result, "cjk.py:5:10")


def test_stripe_test_key_is_blocked(hooked):
    key = "sk" + "_test_" + "4eC8gH2iJ6kL0mN4oP8qR2sT"
    result = hooked.commit_file("pay.py", f'STRIPE = "{key}"\n', "feat: pay")
    assert_blocked(result, "[stripe-secret-key]")


def test_placeholders_in_env_example_pass(hooked):
    text = (
        "NVIDIA_API_KEY=nvapi-" + "X" * 24 + "\n"
        "OPENAI_API_KEY=your_api_key_here\n"
        "GITHUB_TOKEN=ghp_" + "x" * 36 + "\n"
    )
    result = hooked.commit_file(".env.example", text, "docs: document env vars")
    assert result.rc == 0, result.out


def test_nested_lockfile_is_excluded(hooked):
    result = hooked.commit_file("web/package-lock.json", LINE, "build: lock deps")
    assert result.rc == 0, result.out


def test_pragma_allows_a_line(hooked):
    text = f'token = "{GH_TOKEN}"  # pragma: allowlist secret\n'
    result = hooked.commit_file("fixture.py", text, "test: fixture")
    assert result.rc == 0, result.out


def test_githooks_skip_bypasses_one_check(hooked):
    result = hooked.commit_file("app.py", LINE, "feat: add app", GITHOOKS_SKIP="secrets")
    assert result.rc == 0, result.out
    assert "skipping 'secrets' (GITHOOKS_SKIP)" in result.out


# --------------------------------------------------------------------------- #
# large files / branch protection
# --------------------------------------------------------------------------- #
def test_large_file_is_blocked_and_env_override(hooked):
    hooked.config(
        "hooks:\n  pre-commit: [large-files]\n  commit-msg: []\n  prepare-commit-msg: []\n"
        "large_files:\n  max_bytes: 100\n  warn_bytes: 50\n"
    )
    result = hooked.commit_file("data.bin", "x" * 200, "feat: data")
    assert_blocked(result, "data.bin", "git lfs track", '"*.bin"')
    result = hooked.commit("feat: data", GITHOOKS_MAX_FILE_BYTES="1000")
    assert result.rc == 0, result.out
    assert "large, but under the limit" in result.out


def test_main_is_protected_with_one_off_override(hooked):
    hooked.git("switch", "-q", "main")
    result = hooked.commit_file("a.txt", "a\n", "feat: on main")
    assert_blocked(result, "protected branch 'main'")
    result = hooked.commit("feat: on main", ALLOW_COMMIT_TO_PROTECTED="1")
    assert result.rc == 0, result.out


# --------------------------------------------------------------------------- #
# format / lint
# --------------------------------------------------------------------------- #
@pytest.fixture
def bin_dir(tmp_path, monkeypatch):
    path = tmp_path / "bin"
    path.mkdir()
    monkeypatch.setenv("PATH", str(path) + os.pathsep + os.environ["PATH"])
    return path


def _format_config(tool: str = "fakefmt", autostage: bool = True) -> str:
    return (
        "hooks:\n  pre-commit: [secrets, format]\n  commit-msg: []\n  prepare-commit-msg: []\n"
        f"format:\n  autostage: {'true' if autostage else 'false'}\n"
        f"  tools:\n    python: ['{tool}']\n"
    )


def test_format_does_not_commit_unstaged_hunks(hooked, bin_dir):
    # The audit's repro: only `z   =   1` is staged; the working tree also has
    # an unstaged token. The token must not reach the commit.
    fake_tool(bin_dir, "fakefmt", SQUASH_SPACES)
    hooked.config(_format_config())
    hooked.write("s.py", "z   =   1\n")
    hooked.git("add", "s.py")
    hooked.write("s.py", "z   =   1\n" + LINE)
    result = hooked.commit("feat: add s")
    assert result.rc == 0, result.out
    assert "not formatted: 1 file(s) also have unstaged changes" in result.out
    assert GH_TOKEN not in hooked.show("HEAD:s.py")
    assert GH_TOKEN in (hooked.path / "s.py").read_text(encoding="utf-8")


def test_format_restages_a_fully_staged_file(hooked, bin_dir):
    fake_tool(bin_dir, "fakefmt", SQUASH_SPACES)
    hooked.config(_format_config())
    result = hooked.commit_file("t.py", "y   =   2\n", "feat: add t")
    assert result.rc == 0, result.out
    assert hooked.show("HEAD:t.py") == "y = 2\n"
    assert hooked.ok("status", "--porcelain", "--", "t.py") == ""


def test_format_without_autostage_blocks_until_restaged(hooked, bin_dir):
    fake_tool(bin_dir, "fakefmt", SQUASH_SPACES)
    hooked.config(_format_config(autostage=False))
    result = hooked.commit_file("t.py", "y   =   2\n", "feat: add t")
    assert_blocked(result, "Review and re-stage")
    result = hooked.commit("feat: add t", "t.py")
    assert result.rc == 0, result.out


def _ruff() -> str:
    found = shutil.which("ruff")
    if found:
        return found
    cand = Path(sys.executable).parent / ("ruff.exe" if os.name == "nt" else "ruff")
    return str(cand) if cand.is_file() else ""


@pytest.mark.skipif(not _ruff(), reason="ruff is not installed")
def test_format_with_real_ruff(hooked, bin_dir):
    shutil.copy(_ruff(), bin_dir / Path(_ruff()).name)
    hooked.config(_format_config(tool="ruff format"))
    hooked.write("r.py", "x   =   {'a':1}\n")
    hooked.git("add", "r.py")
    result = hooked.commit("feat: ruff formatted")
    assert result.rc == 0, result.out
    assert hooked.show("HEAD:r.py") == 'x = {"a": 1}\n'


def test_lint_skips_web_files_and_blocks_js(hooked, bin_dir):
    fake_tool(bin_dir, "eslint", FAIL_ALWAYS)
    hooked.config("hooks:\n  pre-commit: [lint]\n  commit-msg: []\n  prepare-commit-msg: []\n")
    result = hooked.commit_file("docs/guide.md", "# Guide\n", "docs: guide")
    assert result.rc == 0, result.out
    result = hooked.commit_file("app.js", "var x\n", "feat: js")
    assert_blocked(result, "eslint reported problems")
