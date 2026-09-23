"""Tests for the issue-id trailer / prefix hook and its interplay with the
Conventional Commits validator."""

from __future__ import annotations

from copy import deepcopy

import pytest
from _helpers import git, write

from pre_commit_hooks import prepare_commit_msg as pcm
from pre_commit_hooks._core import DEFAULTS
from pre_commit_hooks.conventional_commit import validate

COMMENTS = "# Please enter the commit message for your changes.\n# Lines starting\n"


def test_trailer_after_single_line_message():
    assert pcm.add_trailer("feat: add b\n", "Refs", "ABC-123") == (
        "feat: add b\n\nRefs: ABC-123\n"
    )


def test_trailer_goes_above_the_comment_block():
    out = pcm.add_trailer("feat: x\n\nbody here\n" + COMMENTS, "Refs", "ABC-1")
    assert out == "feat: x\n\nbody here\n\nRefs: ABC-1\n" + COMMENTS


def test_trailer_joins_an_existing_trailer_block():
    msg = "feat: x\n\nbody\n\nSigned-off-by: A <a@b.c>\n"
    out = pcm.add_trailer(msg, "Refs", "ABC-1")
    assert out == "feat: x\n\nbody\n\nSigned-off-by: A <a@b.c>\nRefs: ABC-1\n"


def test_trailer_respects_scissors():
    msg = "feat: x\n# ------------------------ >8 ------------------------\ndiff --git a b\n"
    out = pcm.add_trailer(msg, "Refs", "ABC-1")
    assert out.startswith("feat: x\n\nRefs: ABC-1\n# ------------------------ >8")
    assert out.endswith("diff --git a b\n")


def test_trailer_on_empty_editor_message_keeps_subject_line_free():
    out = pcm.add_trailer("\n" + COMMENTS, "Refs", "ABC-1")
    assert out.startswith("\n\nRefs: ABC-1\n")
    # What the author ends up with after typing a subject on line one:
    final = "feat: typed later" + out
    assert validate(final, deepcopy(DEFAULTS)) == []


def test_already_references_ignores_comments():
    assert pcm.already_references("feat: x\n\nRefs: ABC-1\n", "ABC-1")
    assert not pcm.already_references("feat: x\n# on branch ABC-1\n", "ABC-1")


# --------------------------------------------------------------------------- #
# The default pairing (issue-prefix + conventional-commit) must work
# --------------------------------------------------------------------------- #
def test_trailer_mode_output_passes_conventional_commit():
    msg = pcm.add_trailer("feat: add b\n", "Refs", "ABC-123")
    assert validate(msg, deepcopy(DEFAULTS)) == []


def test_prefix_mode_output_passes_conventional_commit():
    # Regression: '[ABC-123] feat: add b' was rejected by the header regex.
    msg = pcm.add_prefix("feat: add b\n", DEFAULTS["issue_prefix"]["template"].format(issue="ABC-123"))
    assert msg.startswith("[ABC-123] feat: add b")
    assert validate(msg, deepcopy(DEFAULTS)) == []


def test_prefix_still_counts_toward_header_length():
    subject = "x" * 60
    msg = f"[ABC-123] feat: {subject}"
    errors = validate(msg, deepcopy(DEFAULTS))
    assert any("chars" in e for e in errors)


def test_trailer_only_message_is_rejected():
    assert validate("Refs: ABC-123\n", deepcopy(DEFAULTS)) == [
        "commit message has no subject line, only trailers"
    ]


# --------------------------------------------------------------------------- #
# main() against a real repository
# --------------------------------------------------------------------------- #
@pytest.fixture
def ticket_branch(repo):
    git(repo, "switch", "-q", "-c", "feature/ABC-123-thing")
    return repo


def test_main_adds_trailer_on_ticket_branch(ticket_branch):
    path = write(ticket_branch / "MSG", "feat: add b\n")
    assert pcm.main([str(path), "message"]) == 0
    assert path.read_text(encoding="utf-8") == "feat: add b\n\nRefs: ABC-123\n"


def test_main_skips_merge_source(ticket_branch):
    path = write(ticket_branch / "MSG", "Merge branch 'x'\n")
    assert pcm.main([str(path), "merge"]) == 0
    assert path.read_text(encoding="utf-8") == "Merge branch 'x'\n"


def test_main_reads_source_from_pre_commit_env(ticket_branch, monkeypatch):
    # Regression: under the pre-commit framework the source only arrives in
    # PRE_COMMIT_COMMIT_MSG_SOURCE, so skip_sources were never applied.
    monkeypatch.setenv("PRE_COMMIT_COMMIT_MSG_SOURCE", "commit")
    path = write(ticket_branch / "MSG", "feat: amended\n")
    assert pcm.main([str(path)]) == 0
    assert path.read_text(encoding="utf-8") == "feat: amended\n"


def test_main_leaves_autosquash_commits_alone(ticket_branch):
    path = write(ticket_branch / "MSG", "fixup! feat: add b\n")
    assert pcm.main([str(path), "message"]) == 0
    assert path.read_text(encoding="utf-8") == "fixup! feat: add b\n"


def test_main_prefix_mode(ticket_branch):
    write(ticket_branch / ".githooks.yaml", "issue_prefix:\n  mode: prefix\n")
    path = write(ticket_branch / "MSG", "feat: add b\n")
    assert pcm.main([str(path), "message"]) == 0
    assert path.read_text(encoding="utf-8") == "[ABC-123] feat: add b\n"


def test_main_no_issue_in_branch(repo):
    path = write(repo / "MSG", "feat: add b\n")
    assert pcm.main([str(path), "message"]) == 0
    assert path.read_text(encoding="utf-8") == "feat: add b\n"
