"""prepare-commit-msg + commit-msg through a real `git commit`."""

from __future__ import annotations

import sys

from _helpers import write


def test_conventional_message_accepted(hooked):
    result = hooked.commit_file("a.txt", "a\n", "feat(api): add a")
    assert result.rc == 0, result.out


def test_non_conventional_message_rejected(hooked):
    result = hooked.commit_file("a.txt", "a\n", "add stuff")
    assert result.rc != 0
    assert "Commit message rejected (Conventional Commits)" in result.out
    assert "header does not match" in result.out


def test_ticket_branch_commit_passes_with_refs_trailer(hooked):
    # The audit's repro: the default config rejected every commit here.
    hooked.git("switch", "-q", "-c", "feature/ABC-123-thing")
    result = hooked.commit_file("b.txt", "b\n", "feat: add b")
    assert result.rc == 0, result.out
    assert hooked.ok("log", "-1", "--format=%B").strip() == "feat: add b\n\nRefs: ABC-123"
    assert hooked.ok("log", "-1", "--format=%(trailers:key=Refs,valueonly)").strip() == "ABC-123"


def test_prefix_mode_still_passes_conventional_commit(hooked):
    hooked.config(
        "hooks:\n  pre-commit: []\n  prepare-commit-msg: [issue-prefix]\n"
        "  commit-msg: [conventional-commit]\nissue_prefix:\n  mode: prefix\n"
    )
    hooked.git("switch", "-q", "-c", "feature/ABC-123-thing")
    result = hooked.commit_file("b.txt", "b\n", "feat: add b")
    assert result.rc == 0, result.out
    assert hooked.log()[0] == "[ABC-123] feat: add b"


def test_editor_flow_gets_trailer_below_the_typed_subject(hooked, tmp_path):
    editor = write(
        tmp_path / "editor.py",
        "import sys\n"
        "p = sys.argv[1]\n"
        "s = open(p, encoding='utf-8').read()\n"
        "open(p, 'w', encoding='utf-8', newline='').write('feat: typed in editor' + s)\n",
    )
    hooked.git("switch", "-q", "-c", "feature/ABC-7-editor")
    hooked.write("e.txt", "e\n")
    hooked.git("add", "e.txt")
    result = hooked.git("commit", GIT_EDITOR=f'"{sys.executable}" "{editor}"')
    assert result.rc == 0, result.out
    assert hooked.ok("log", "-1", "--format=%s").strip() == "feat: typed in editor"
    assert hooked.ok("log", "-1", "--format=%(trailers:key=Refs,valueonly)").strip() == "ABC-7"


def test_fixup_revert_and_merge_are_skipped(hooked):
    hooked.git("switch", "-q", "-c", "feature/ABC-9-x")
    assert hooked.commit_file("f.txt", "1\n", "feat: base").rc == 0
    hooked.write("f.txt", "2\n")
    hooked.git("add", "f.txt")
    fixup = hooked.git("commit", "--fixup", "HEAD")
    assert fixup.rc == 0, fixup.out
    assert hooked.ok("log", "-1", "--format=%B").strip() == "fixup! feat: base"

    revert = hooked.git("revert", "--no-edit", "HEAD")
    assert revert.rc == 0, revert.out
    assert hooked.log()[0].startswith('Revert "fixup! feat: base"')

    hooked.git("switch", "-q", "-c", "side", "main")
    hooked.quiet_commit("side.txt", "s\n", "feat: side")
    hooked.git("switch", "-q", "feature/ABC-9-x")
    merge = hooked.git("merge", "--no-ff", "--no-edit", "side")
    assert merge.rc == 0, merge.out
    assert hooked.log()[0] == "Merge branch 'side' into feature/ABC-9-x"
    assert "Refs:" not in hooked.ok("log", "-1", "--format=%B")
