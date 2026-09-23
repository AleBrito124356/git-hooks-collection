"""Tests for the fixup!/squash!/WIP push guard."""

from __future__ import annotations

from _helpers import git, write

from pre_commit_hooks import _core, _miniyaml, no_fixup


def _matches(patterns, subject):
    return any(m.search(subject) for m in no_fixup._compile_patterns(patterns))


def test_pyyaml_style_mapping_pattern_is_coerced():
    # Regression: PyYAML parses an unquoted "- wip:" as {"wip": None}; calling
    # .endswith on it crashed the check and blocked every push.
    assert no_fixup._coerce_pattern({"wip": None}) == "wip:"
    assert _matches([{"wip": None}], "wip: trying something")


def test_non_string_patterns_do_not_crash():
    assert no_fixup._compile_patterns([None, 42, "", "WIP"])


def test_autosquash_markers_are_anchored():
    assert _matches(["fixup!"], "fixup! feat: add b")
    assert not _matches(["fixup!"], "docs: explain fixup! commits")


def test_wip_matches_whole_words_only():
    assert _matches(["WIP"], "WIP: half done")
    assert _matches(["WIP"], "[wip] try the thing")
    assert not _matches(["WIP"], "fix: swipe gesture on mobile")
    assert not _matches(["wip:"], "feat: swip: nope")


def test_shipped_config_patterns_parse_the_same_everywhere():
    text = _core.TEMPLATE_PATH.read_text(encoding="utf-8")
    mini = _miniyaml.safe_load(text)["no_fixup"]["block_patterns"]
    assert "wip:" in mini
    assert all(isinstance(p, str) for p in mini)


def _commit(repo, name, msg):
    write(repo / name, msg + "\n")
    git(repo, "add", name)
    git(repo, "commit", "-q", "--no-verify", "-m", msg)
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def test_new_branch_push_is_checked(repo, monkeypatch):
    # Regression: the new-branch range never reached git log intact.
    tip = _commit(repo, "a.txt", "WIP: half done")
    tip = _commit(repo, "b.txt", "fixup! feat: add b")
    monkeypatch.setattr("sys.stdin", None)
    stdin = f"refs/heads/f {tip} refs/heads/f {_core.ZERO_SHA}\n"
    assert no_fixup.main([], stdin_data=stdin) == 1
    offenders = no_fixup.find_offenders(_core.pushed_ranges(stdin), ["fixup!", "WIP"])
    assert [s for _sha, s in offenders] == ["fixup! feat: add b", "WIP: half done"]


def test_clean_push_passes(repo):
    base = git(repo, "rev-parse", "HEAD").stdout.strip()
    tip = _commit(repo, "a.txt", "feat: a real change")
    assert no_fixup.main([], stdin_data=f"refs/heads/main {tip} refs/heads/main {base}\n") == 0


def test_pre_commit_framework_range_is_honoured(repo, monkeypatch):
    base = git(repo, "rev-parse", "HEAD").stdout.strip()
    tip = _commit(repo, "a.txt", "WIP two")
    monkeypatch.setenv("PRE_COMMIT_FROM_REF", base)
    monkeypatch.setenv("PRE_COMMIT_TO_REF", tip)
    assert no_fixup.main([], stdin_data="") == 1
