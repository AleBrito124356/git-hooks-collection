"""The config schema behind `githooks doctor` and the hooks' typo warnings."""

from __future__ import annotations

from pre_commit_hooks import _core, _miniyaml
from pre_commit_hooks.config_schema import ERROR, WARNING, first_difference, validate


def problems(text):
    return [(p.level, str(p)) for p in validate(_miniyaml.safe_load(text), _core.DEFAULTS)]


def test_shipped_config_is_clean():
    assert validate(_core.DEFAULTS, _core.DEFAULTS) == []


def test_unknown_check_with_suggestion():
    assert problems("hooks:\n  pre-commit: [secret]\n") == [
        (
            ERROR,
            "hooks.pre-commit: unknown check 'secret' (did you mean 'secrets'?); it would be skipped",
        )
    ]


def test_unknown_stage_and_misplaced_check():
    found = problems("hooks:\n  pre-comit: [secrets]\n  pre-push: [lint]\n")
    assert found[0][0] == ERROR and "unknown git stage 'pre-comit'" in found[0][1]
    assert "did you mean 'pre-commit'" in found[0][1]
    assert found[1] == (
        WARNING,
        "hooks.pre-push: 'lint' is designed for the pre-commit stage, not pre-push",
    )


def test_unknown_setting_and_section():
    found = problems("large_files:\n  max_byte: 10\nlarge_file:\n  max_bytes: 10\n")
    assert (
        ERROR,
        "large_files.max_byte: unknown setting (did you mean 'max_bytes'?); it is ignored",
    ) in found
    assert (ERROR, "large_file: unknown section (did you mean 'large_files'?)") in found


def test_types_modes_regexes_and_buckets():
    found = {
        msg.split(":")[0]: msg
        for _level, msg in problems(
            "large_files:\n  max_bytes: 5MB\n"
            "secrets:\n  scan: everything\n  allow_regex: ['(unclosed']\n"
            "issue_prefix:\n  template: 'no placeholder'\n"
            "format:\n  tools:\n    pyhton: [black]\n"
            "format2: 1\n"
        )
    }
    assert "expected number, got string" in found["large_files.max_bytes"]
    assert "must be one of diff, file" in found["secrets.scan"]
    assert "invalid regular expression" in found["secrets.allow_regex"]
    assert "{issue}" in found["issue_prefix.template"]
    assert "did you mean 'python'" in found["format.tools.pyhton"]


def test_mapping_list_item_is_flagged():
    data = {"no_fixup": {"block_patterns": ["WIP", {"wip": None}]}}
    (problem,) = validate(data, _core.DEFAULTS)
    assert problem.level == WARNING and "quote it" in problem.message


def test_first_difference():
    assert first_difference({"a": [1, "x"]}, {"a": [1, "x"]}) is None
    assert first_difference({"a": [1, {"wip": None}]}, {"a": [1, "wip:"]}) == (
        "a[1]",
        {"wip": None},
        "wip:",
    )
    assert first_difference({"a": True}, {"a": 1}) == ("a", True, 1)
