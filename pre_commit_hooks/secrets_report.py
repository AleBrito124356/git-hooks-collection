"""Output formats for secret findings: text (for humans), JSON and SARIF 2.1.0.

No format ever contains a raw secret. Findings carry only a redacted preview
(``ghp_a1…q7R8 (40 chars)``), and that preview is all the SARIF snippet holds,
so a report can be uploaded to code scanning or attached to a CI log safely.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Iterable
from urllib.parse import quote

from . import __version__, _core
from .secrets_engine import RULES, Finding

TOOL_NAME = "git-hooks-collection"
INFO_URI = "https://github.com/AleBrito124356/git-hooks-collection"

HEADINGS = {
    "staged": "Potential secrets found in staged changes:",
    "files": "Potential secrets found in these files:",
    "range": "Secrets found in the commits being pushed:",
    "all-files": "Secrets found in tracked files:",
}


def _location(f: Finding) -> str:
    where = f"{f.path}:{f.line}:{f.column}"
    return f"{f.commit[:10]} {where}" if f.commit else where


def print_text(findings: list[Finding], mode: str, stream=None) -> None:
    stream = stream or sys.stderr
    c = _core.C
    _core.header("\n" + HEADINGS.get(mode, HEADINGS["staged"]) + "\n")
    for f in findings:
        print(
            f"  {c.BOLD}{_location(f)}{c.RESET}  {c.RED}{f.description}{c.RESET} "
            f"{c.DIM}[{f.rule_id}]{c.RESET}",
            file=stream,
        )
        print(f"      {c.DIM}matched: {f.preview}{c.RESET}", file=stream)
    print(file=stream)


def to_json(findings: Iterable[Finding], mode: str) -> dict[str, Any]:
    return {
        "tool": TOOL_NAME,
        "version": __version__,
        "mode": mode,
        "findings": [
            {
                "rule_id": f.rule_id,
                "description": f.description,
                "path": f.path,
                "line": f.line,
                "column": f.column,
                "commit": f.commit,
                "preview": f.preview,
            }
            for f in findings
        ],
    }


def _uri(path: str) -> str:
    return quote(path.replace("\\", "/"), safe="/")


def to_sarif(findings: Iterable[Finding]) -> dict[str, Any]:
    """A SARIF 2.1.0 log with one run, suitable for GitHub code scanning."""
    findings = list(findings)
    rule_index = {rule.id: i for i, rule in enumerate(RULES)}
    rules = [
        {
            "id": rule.id,
            "name": "".join(part.capitalize() for part in rule.id.split("-")),
            "shortDescription": {"text": rule.description},
            "fullDescription": {
                "text": f"{rule.description} committed to the repository. Treat it as "
                "leaked: rotate it and remove it from history."
            },
            "defaultConfiguration": {"level": "error"},
            "help": {
                "text": "Remove the secret, load it from the environment instead and rotate it. "
                "If it is a false positive, add '# pragma: allowlist secret' to the line "
                "or extend secrets.exclude / secrets.allow_regex in .githooks.yaml."
            },
            "properties": {"tags": ["security", "secret"]},
        }
        for rule in RULES
    ]
    results = []
    for f in findings:
        result: dict[str, Any] = {
            "ruleId": f.rule_id,
            "ruleIndex": rule_index.get(f.rule_id, 0),
            "level": "error",
            "message": {"text": f"{f.description} ({f.preview})"},
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": _uri(f.path), "uriBaseId": "%SRCROOT%"},
                        "region": {
                            "startLine": f.line,
                            "startColumn": f.column,
                            "snippet": {"text": f.preview},
                        },
                    }
                }
            ],
        }
        if f.commit:
            result["properties"] = {"commit": f.commit}
        results.append(result)
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": TOOL_NAME,
                        "version": __version__,
                        "informationUri": INFO_URI,
                        "rules": rules,
                    }
                },
                "results": results,
            }
        ],
    }


def render(findings: list[Finding], fmt: str, mode: str) -> str:
    if fmt == "json":
        return json.dumps(to_json(findings, mode), indent=2, ensure_ascii=False) + "\n"
    if fmt == "sarif":
        return json.dumps(to_sarif(findings), indent=2, ensure_ascii=False) + "\n"
    raise ValueError(f"unknown format {fmt!r}")
