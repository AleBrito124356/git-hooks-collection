"""A lightweight schema for .githooks.yaml, derived from the shipped defaults.

A misspelled check name used to be skipped with a warning buried in commit
output, and a misspelled threshold (``max_byte: 100``) was silently ignored --
the default applied and nobody noticed. ``validate`` reports both, with a
"did you mean" suggestion, plus wrong value types, invalid regular expressions
and unknown modes. The hooks print these once per run; ``githooks doctor``
fails on them.
"""

from __future__ import annotations

import re
from typing import Any, NamedTuple

from . import registry

ERROR = "error"
WARNING = "warning"

# Values that must be one of a fixed set.
_CHOICES = {
    ("secrets", "scan"): ("diff", "file"),
    ("issue_prefix", "mode"): ("trailer", "prefix"),
}
# Settings that hold regular expressions (single values or lists).
_REGEXES = {
    ("secrets", "allow_regex"),
    ("issue_prefix", "branch_regex"),
    ("conventional_commit", "ignore_prefix_regex"),
}
# Sections whose values are {language bucket: [tool, ...]}.
_TOOL_TABLES = {("format", "tools"), ("lint", "tools")}


class Problem(NamedTuple):
    level: str  # "error" or "warning"
    where: str  # dotted path, e.g. "large_files.max_bytes"
    message: str

    def __str__(self) -> str:
        return f"{self.where}: {self.message}"


def _suggest(name: str, options) -> str:
    hint = registry.suggest(str(name), [str(o) for o in options])
    return f" (did you mean '{hint}'?)" if hint else ""


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "mapping"
    return type(value).__name__


def _type_ok(default: Any, value: Any) -> bool:
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, int):
        return isinstance(value, int) and not isinstance(value, bool)
    if isinstance(default, float):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if isinstance(default, str):
        return isinstance(value, str)
    if isinstance(default, list):
        return isinstance(value, list) or value is None
    if isinstance(default, dict):
        return isinstance(value, dict)
    return True


def _check_regex(where: str, value: Any, problems: list[Problem]) -> None:
    for pattern in value if isinstance(value, list) else [value]:
        if pattern in (None, ""):
            continue
        try:
            re.compile(str(pattern))
        except re.error as exc:
            problems.append(Problem(ERROR, where, f"invalid regular expression {pattern!r}: {exc}"))


def _check_hooks(hooks: Any, problems: list[Problem]) -> None:
    if not isinstance(hooks, dict):
        problems.append(Problem(ERROR, "hooks", f"expected a mapping, got {_type_name(hooks)}"))
        return
    for stage, names in hooks.items():
        where = f"hooks.{stage}"
        if stage not in registry.STAGES:
            problems.append(
                Problem(
                    ERROR,
                    where,
                    f"unknown git stage '{stage}'{_suggest(stage, registry.STAGES)}; "
                    f"supported: {', '.join(registry.STAGES)}",
                )
            )
            continue
        if names is None:
            continue
        if not isinstance(names, list):
            problems.append(Problem(ERROR, where, f"expected a list, got {_type_name(names)}"))
            continue
        for name in names:
            check = registry.get(str(name)) if isinstance(name, str) else None
            if check is None:
                problems.append(
                    Problem(
                        ERROR,
                        where,
                        f"unknown check {name!r}{_suggest(str(name), registry.NAMES)}; "
                        "it would be skipped",
                    )
                )
            elif check.stage != stage:
                problems.append(
                    Problem(
                        WARNING,
                        where,
                        f"'{name}' is designed for the {check.stage} stage, not {stage}",
                    )
                )


def validate(data: Any, defaults: dict) -> list[Problem]:
    """Problems in a parsed config file (the file only, before merging)."""
    problems: list[Problem] = []
    if data is None:
        return problems
    if not isinstance(data, dict):
        return [Problem(ERROR, "<root>", f"expected a mapping, got {_type_name(data)}")]
    for section, value in data.items():
        if section not in defaults:
            problems.append(
                Problem(ERROR, str(section), f"unknown section{_suggest(section, defaults)}")
            )
            continue
        if section == "hooks":
            _check_hooks(value, problems)
            continue
        default = defaults[section]
        if not isinstance(default, dict):
            if not _type_ok(default, value):
                problems.append(
                    Problem(
                        ERROR, section, f"expected {_type_name(default)}, got {_type_name(value)}"
                    )
                )
            continue
        if value is None:
            continue
        if not isinstance(value, dict):
            problems.append(Problem(ERROR, section, f"expected a mapping, got {_type_name(value)}"))
            continue
        for key, item in value.items():
            where = f"{section}.{key}"
            if key not in default:
                problems.append(
                    Problem(ERROR, where, f"unknown setting{_suggest(key, default)}; it is ignored")
                )
                continue
            if not _type_ok(default[key], item):
                problems.append(
                    Problem(
                        ERROR,
                        where,
                        f"expected {_type_name(default[key])}, got {_type_name(item)} ({item!r})",
                    )
                )
                continue
            choices = _CHOICES.get((section, key))
            if choices and str(item).strip().lower() not in choices:
                problems.append(
                    Problem(ERROR, where, f"must be one of {', '.join(choices)}, got {item!r}")
                )
            if (section, key) in _REGEXES:
                _check_regex(where, item, problems)
            if (section, key) in _TOOL_TABLES and isinstance(item, dict):
                for bucket in item:
                    if bucket not in ("python", "javascript", "web"):
                        problems.append(
                            Problem(
                                ERROR,
                                f"{where}.{bucket}",
                                "unknown language bucket"
                                f"{_suggest(bucket, ('python', 'javascript', 'web'))}; "
                                "use python, javascript or web",
                            )
                        )
            if isinstance(item, list):
                for entry in item:
                    if isinstance(entry, dict):
                        problems.append(
                            Problem(
                                WARNING,
                                where,
                                f"list item {entry!r} is a mapping; quote it "
                                "(an unquoted 'wip:' is read as a mapping by PyYAML)",
                            )
                        )
        template = value.get("template") if section == "issue_prefix" else None
        if template is not None and "{issue}" not in str(template):
            problems.append(
                Problem(ERROR, "issue_prefix.template", "must contain the {issue} placeholder")
            )
    return problems


def first_difference(a: Any, b: Any, where: str = "") -> tuple[str, Any, Any] | None:
    """Where two parsed configs diverge (PyYAML vs the bundled parser)."""
    if isinstance(a, dict) and isinstance(b, dict):
        for key in list(a) + [k for k in b if k not in a]:
            if key not in a or key not in b:
                return (f"{where}.{key}".lstrip("."), a.get(key), b.get(key))
            diff = first_difference(a[key], b[key], f"{where}.{key}")
            if diff:
                return diff
        return None
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return (where.lstrip("."), a, b)
        for i, (x, y) in enumerate(zip(a, b)):
            diff = first_difference(x, y, f"{where}[{i}]")
            if diff:
                return diff
        return None
    if a != b or type(a) is not type(b):
        return (where.lstrip("."), a, b)
    return None
