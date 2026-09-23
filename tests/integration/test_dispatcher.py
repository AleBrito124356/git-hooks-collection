"""The stage dispatcher: config-driven check lists, unknown names, crash isolation."""

from __future__ import annotations

import json
import subprocess
import sys

from _helpers import ROOT, write

from pre_commit_hooks import dispatch, registry


def _config(repo, text):
    write(repo / ".githooks.yaml", text)


def test_unknown_check_is_warned_with_a_suggestion(repo, capsys):
    _config(repo, "hooks:\n  pre-commit: [secret]\n")
    assert dispatch.run_stage("pre-commit", []) == 0
    err = capsys.readouterr().err
    assert "unknown check 'secret' in hooks.pre-commit" in err
    assert "did you mean 'secrets'" in err


def test_a_crashing_check_fails_the_stage_but_others_still_run(repo, capsys, monkeypatch):
    _config(repo, "hooks:\n  pre-commit: [large-files, branch-protect]\n")
    real = registry.entry_point
    ran = []

    def fake_entry_point(name):
        if name == "large-files":

            def boom(argv, stdin_data=None):
                raise RuntimeError("kaboom")

            return boom

        def wrapped(argv, stdin_data=None):
            ran.append(name)
            return real(name)(argv, stdin_data=stdin_data)

        return wrapped

    monkeypatch.setattr(registry, "entry_point", fake_entry_point)
    assert dispatch.run_stage("pre-commit", []) == 1
    err = capsys.readouterr().err
    assert "check 'large-files' crashed: RuntimeError: kaboom" in err
    assert ran == ["branch-protect"]  # still ran, and blocked the commit on main
    assert "2 check(s) failed at pre-commit: large-files, branch-protect" in err


def test_run_single_check_and_unknown_check(repo, capsys):
    assert dispatch.main(["run", "branch-protect"]) == 1
    assert dispatch.main(["run", "nope"]) == 2
    assert "unknown check 'nope'" in capsys.readouterr().err


def test_empty_stage_is_a_fast_no_op(repo):
    _config(repo, "hooks:\n  commit-msg: []\n")
    assert dispatch.run_stage("commit-msg", ["MSG"]) == 0


def test_probe_reports_interpreter_and_version(repo, monkeypatch):
    proc = subprocess.run(
        [sys.executable, str(ROOT / "githooks-run.py"), "pre-commit"],
        env={**__import__("os").environ, "GITHOOKS_PROBE": "1"},
        stdout=subprocess.PIPE,
        encoding="utf-8",
        check=False,
    )
    data = json.loads(proc.stdout)
    from pre_commit_hooks import __version__

    assert data["package_version"] == __version__
    assert data["version"][0] == 3


def test_a_secret_stops_the_stage_so_other_tools_cannot_echo_it(repo, capsys, monkeypatch):
    from _helpers import GH_TOKEN, git

    _config(repo, "hooks:\n  pre-commit: [secrets, branch-protect]\n")
    write(repo / "leak.py", f'token = "{GH_TOKEN}"\n')
    git(repo, "add", "leak.py")
    assert dispatch.run_stage("pre-commit", []) == 1
    err = capsys.readouterr().err
    assert "not running branch-protect: a secret was found" in err
    assert "protected branch" not in err
    assert GH_TOKEN not in err
