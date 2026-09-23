"""Scanner v2 end to end: staged-diff scanning, the secrets-push check, and the
`githooks-secrets` CLI (--range, --all-files, JSON / SARIF)."""

from __future__ import annotations

import json
import subprocess
import sys

from _helpers import GH_TOKEN, ROOT

LINE = f'token = "{GH_TOKEN}"\n'
PUSH_CONFIG = "hooks:\n  pre-push: [secrets-push]\n"


def secrets_cli(hooked, *args):
    proc = subprocess.run(
        [sys.executable, str(ROOT / "githooks-run.py"), "run", "secrets", *args],
        cwd=str(hooked.path),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return proc


# --------------------------------------------------------------------------- #
# pre-commit: the staged diff
# --------------------------------------------------------------------------- #
def test_diff_mode_reports_the_new_line_number(hooked):
    hooked.quiet_commit("app.py", "".join(f"x{i} = {i}\n" for i in range(1, 8)), "feat: app")
    text = (hooked.path / "app.py").read_text(encoding="utf-8").splitlines(keepends=True)
    text.insert(4, LINE)  # becomes line 5
    result = hooked.commit_file("app.py", "".join(text), "feat: add token")
    assert result.rc != 0
    assert "app.py:5:10" in result.out


def test_diff_mode_does_not_rereport_existing_content(hooked):
    # A token that is already committed is not what *this* commit adds.
    hooked.quiet_commit("legacy.py", LINE + "a = 1\n", "chore: legacy")
    result = hooked.commit_file("legacy.py", LINE + "a = 2\n", "fix: bump a")
    assert result.rc == 0, result.out


def test_file_mode_scans_whole_staged_files(hooked):
    hooked.config(
        "hooks:\n  pre-commit: [secrets]\n  commit-msg: []\n  prepare-commit-msg: []\n"
        "secrets:\n  scan: file\n"
    )
    hooked.quiet_commit("legacy.py", LINE + "a = 1\n", "chore: legacy")
    result = hooked.commit_file("legacy.py", LINE + "a = 2\n", "fix: bump a")
    assert result.rc != 0
    assert "legacy.py:1:10" in result.out


# --------------------------------------------------------------------------- #
# pre-push: every pushed commit
# --------------------------------------------------------------------------- #
def test_no_verify_commit_is_caught_at_push(hooked):
    hooked.config(PUSH_CONFIG)
    sha = hooked.quiet_commit("leak.py", "a = 1\n" + LINE, "feat: sneaky")  # --no-verify
    hooked.quiet_commit("other.py", "b = 2\n", "feat: unrelated")
    result = hooked.push("-u", "feature/work")
    assert result.rc != 0, result.out
    assert f"{sha[:10]} leak.py:2:10" in result.out
    assert "[github-token]" in result.out
    assert "Rewrite the commits that add it" in result.out
    assert GH_TOKEN not in result.out
    assert "feature/work" not in hooked.ok("ls-remote", "--heads", str(hooked.remote))


def test_secret_removed_in_a_later_commit_is_still_blocked(hooked):
    # Deleting the line afterwards does not help: the old commit still has it.
    hooked.config(PUSH_CONFIG)
    hooked.quiet_commit("leak.py", LINE, "feat: oops")
    hooked.quiet_commit("leak.py", "clean = True\n", "fix: remove token")
    result = hooked.push("-u", "feature/work")
    assert result.rc != 0
    assert "leak.py:1:10" in result.out


def test_existing_branch_only_new_commits_are_scanned(hooked):
    hooked.config(PUSH_CONFIG)
    hooked.quiet_commit("a.py", "a = 1\n", "feat: a")
    assert hooked.push("-u", "feature/work").rc == 0
    sha = hooked.quiet_commit("b.py", LINE, "feat: b")
    result = hooked.push()
    assert result.rc != 0
    assert f"{sha[:10]} b.py:1:10" in result.out


def test_secrets_push_can_be_skipped_once(hooked):
    hooked.config(PUSH_CONFIG)
    hooked.quiet_commit("leak.py", LINE, "feat: oops")
    result = hooked.push("-u", "feature/work", GITHOOKS_SKIP="secrets-push")
    assert result.rc == 0, result.out


def test_clean_push_passes(hooked):
    hooked.config(PUSH_CONFIG)
    hooked.quiet_commit(".env.example", "API_KEY=your_api_key_here\n", "docs: env")
    assert hooked.push("-u", "feature/work").rc == 0


# --------------------------------------------------------------------------- #
# CLI: --range, --all-files, formats
# --------------------------------------------------------------------------- #
def test_cli_range(hooked):
    base = hooked.head()
    sha = hooked.quiet_commit("r.py", LINE, "feat: r")
    proc = secrets_cli(hooked, "--range", f"{base}..HEAD")
    assert proc.returncode == 1
    assert f"{sha[:10]} r.py:1:10" in proc.stderr


def test_cli_all_files_sarif(hooked):
    hooked.quiet_commit("src/client.py", "x = 1\n" + LINE, "feat: client")
    hooked.quiet_commit(".env.example", "NVIDIA_API_KEY=nvapi-" + "X" * 24 + "\n", "docs: env")
    out = hooked.path / "secrets.sarif"
    proc = secrets_cli(hooked, "--all-files", "--format", "sarif", "--output", str(out))
    assert proc.returncode == 1, proc.stderr
    log = json.loads(out.read_text(encoding="utf-8"))
    results = log["runs"][0]["results"]
    assert [
        (
            r["ruleId"],
            r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"],
            r["locations"][0]["physicalLocation"]["region"]["startLine"],
        )
        for r in results
    ] == [("github-token", "src/client.py", 2)]
    assert GH_TOKEN not in out.read_text(encoding="utf-8")


def test_cli_json_exit_zero(hooked):
    hooked.quiet_commit("j.py", LINE, "feat: j")
    proc = secrets_cli(hooked, "--all-files", "--format", "json", "--exit-zero")
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert [f["path"] for f in data["findings"]] == ["j.py"]
