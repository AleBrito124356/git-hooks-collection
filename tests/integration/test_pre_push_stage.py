"""pre-push stage through a real `git push` to a local bare remote."""

from __future__ import annotations

import sys


def remote_branches(hooked):
    return hooked.ok("ls-remote", "--heads", str(hooked.remote))


def test_new_branch_with_wip_and_fixup_is_blocked(hooked, yaml_backend):
    # The audit's repro: pushing a brand-new branch was never checked.
    hooked.quiet_commit("w.txt", "1\n", "WIP: half done")
    hooked.quiet_commit("w.txt", "2\n", "fixup! feat: add b")
    result = hooked.push("-u", "feature/work")
    assert result.rc != 0, result.out
    assert "WIP: half done" in result.out and "fixup! feat: add b" in result.out
    assert "feature/work" not in remote_branches(hooked)


def test_existing_branch_push_is_blocked(hooked):
    hooked.quiet_commit("a.txt", "1\n", "feat: clean")
    assert hooked.push("-u", "feature/work").rc == 0
    hooked.quiet_commit("a.txt", "2\n", "squash! feat: clean")
    result = hooked.push()
    assert result.rc != 0
    assert "squash! feat: clean" in result.out
    assert "feat: clean" not in result.out.replace("squash! feat: clean", "")


def test_clean_push_passes_and_swipe_is_not_wip(hooked):
    hooked.quiet_commit("a.txt", "1\n", "fix: swipe gesture on mobile")
    result = hooked.push("-u", "feature/work")
    assert result.rc == 0, result.out
    assert "feature/work" in remote_branches(hooked)


def test_unquoted_wip_pattern_from_old_configs_does_not_crash(hooked, yaml_backend):
    # 0.1.0 shipped `- wip:` unquoted; PyYAML reads it as {"wip": None} and the
    # check crashed, blocking every push.
    hooked.config(
        "hooks:\n  pre-push: [no-fixup]\n"
        "no_fixup:\n  block_patterns:\n    - fixup!\n    - WIP\n    - wip:\n"
    )
    hooked.quiet_commit("a.txt", "1\n", "feat: fine")
    result = hooked.push("-u", "feature/work")
    assert result.rc == 0, result.out
    assert "crashed" not in result.out
    hooked.quiet_commit("a.txt", "2\n", "chore: wip: nearly")
    result = hooked.push()
    assert result.rc != 0 and "chore: wip: nearly" in result.out


def _tests_config(command: str) -> str:
    return f"hooks:\n  pre-push: [tests]\ntests:\n  command: '{command}'\n  timeout_seconds: 120\n"


def test_tests_hook_blocks_a_failing_suite(hooked):
    hooked.config(_tests_config(f'"{sys.executable}" -m pytest -q -x -p no:cacheprovider'))
    hooked.quiet_commit("tests/test_it.py", "def test_it():\n    assert 1 == 2\n", "test: red")
    result = hooked.push("-u", "feature/work")
    assert result.rc != 0, result.out
    assert "tests failed; push blocked" in result.out

    assert hooked.push("-u", "feature/work", SKIP_TESTS="1").rc == 0


def test_tests_hook_passes_a_green_suite_and_honours_githooks_skip(hooked):
    hooked.config(_tests_config(f'"{sys.executable}" -m pytest -q -p no:cacheprovider'))
    hooked.quiet_commit("tests/test_it.py", "def test_it():\n    assert True\n", "test: green")
    result = hooked.push("-u", "feature/work")
    assert result.rc == 0, result.out
    assert "tests passed" in result.out
    hooked.quiet_commit("tests/test_it.py", "def test_it():\n    assert False\n", "test: red")
    result = hooked.push(GITHOOKS_SKIP="tests")
    assert result.rc == 0, result.out
    assert "skipping 'tests' (GITHOOKS_SKIP)" in result.out
