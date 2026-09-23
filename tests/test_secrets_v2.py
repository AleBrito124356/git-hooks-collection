"""Scanner v2: provider rules, the patch parser, and the JSON / SARIF output.

Push-safety: every credential-shaped fixture is generated at runtime from a
seeded PRNG or assembled from parts; no contiguous token is written to disk.
"""

from __future__ import annotations

import json
import random
import string

import pytest

from pre_commit_hooks import secrets_report
from pre_commit_hooks.secrets_engine import RULES, scan_text
from pre_commit_hooks.secrets_sources import parse_patch, unquote_path

THRESHOLD = 3.2
ALNUM = string.ascii_letters + string.digits
HEX = "0123456789abcdef"


def rnd(alphabet: str, n: int, seed: int) -> str:
    r = random.Random(seed)
    return "".join(r.choice(alphabet) for _ in range(n))


def ids(text: str):
    return [f.rule_id for f in scan_text("f.py", text, THRESHOLD)]


# (rule id, real-looking token, placeholder of the same shape)
PROVIDERS = [
    ("gitlab-pat", "glpat-" + rnd(ALNUM, 20, 1), "glpat-" + "x" * 20),
    ("npm-token", "npm_" + rnd(ALNUM, 36, 2), "npm_" + "X" * 36),
    (
        "pypi-token",
        "pypi-" + "AgEIcHlwaS5vcmc" + rnd(ALNUM + "_-", 70, 3),
        "pypi-" + "AgEIcHlwaS5vcmc" + "X" * 70,
    ),
    ("huggingface-token", "hf_" + rnd(string.ascii_letters, 34, 4), "hf_" + "x" * 34),
    ("shopify-token", "shpat_" + rnd(HEX, 32, 5), "shpat_" + "0" * 32),
    ("digitalocean-token", "dop_v1_" + rnd(HEX, 64, 6), "dop_v1_" + "0" * 64),
    (
        "telegram-bot-token",
        "5" + "123" + "98765:" + "AA" + rnd(ALNUM + "_-", 33, 7),
        "123456789:" + "AA" + "x" * 33,
    ),
    (
        "azure-storage-key",
        "AccountKey=" + rnd(ALNUM + "+/", 86, 8) + "==",
        "AccountKey=" + "A" * 86 + "==",
    ),
    ("twilio-api-key", "SK" + rnd(HEX, 32, 9), "SK" + "0" * 32),
    (
        "anthropic-key",
        "sk-" + "ant-" + "api03-" + rnd(ALNUM + "_-", 93, 10),
        "sk-" + "ant-" + "api03-" + "X" * 93,
    ),
]


@pytest.mark.parametrize(
    ("rule", "token", "_placeholder"), PROVIDERS, ids=[p[0] for p in PROVIDERS]
)
def test_provider_token_is_detected_once(rule, token, _placeholder):
    assert ids(f'value = "{token}"') == [rule]


@pytest.mark.parametrize(
    ("rule", "_token", "placeholder"), PROVIDERS, ids=[p[0] for p in PROVIDERS]
)
def test_provider_placeholder_is_ignored(rule, _token, placeholder):
    assert ids(f'value = "{placeholder}"') == []


def test_every_rule_is_documented_and_unique():
    rule_ids = [r.id for r in RULES]
    assert len(rule_ids) == len(set(rule_ids))
    assert all(r.description for r in RULES)


# --------------------------------------------------------------------------- #
# Patch parser
# --------------------------------------------------------------------------- #
def _patch(text: str) -> list[bytes]:
    return [line.encode() + b"\n" for line in text.split("\n")]


def test_parse_patch_maps_new_file_line_numbers():
    patch = _patch(
        "diff --git a/app.py b/app.py\n"
        "index 1..2 100644\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -3,0 +4,2 @@ def f():\n"
        "+line four\n"
        "+line five\n"
        "@@ -10 +12 @@\n"
        "-old twelve\n"
        "+new twelve\n"
        "\\ No newline at end of file"
    )
    chunks = list(parse_patch(patch))
    assert len(chunks) == 1
    assert chunks[0].path == "app.py"
    assert chunks[0].lines == [(4, "line four"), (5, "line five"), (12, "new twelve")]


def test_parse_patch_handles_commits_deletions_and_binaries():
    patch = _patch(
        "\x01" + "a" * 40 + "\n"
        "\n"
        "diff --git a/gone.txt b/gone.txt\n"
        "deleted file mode 100644\n"
        "--- a/gone.txt\n"
        "+++ /dev/null\n"
        "@@ -1 +0,0 @@\n"
        "-bye\n"
        "diff --git a/img.png b/img.png\n"
        "Binary files /dev/null and b/img.png differ\n"
        "\x01" + "b" * 40 + "\n"
        "\n"
        "diff --git a/my file.py b/my file.py\n"
        "--- /dev/null\n"
        "+++ b/my file.py\t\n"
        "@@ -0,0 +1 @@\n"
        "++ starts with plus\n"
    )
    chunks = list(parse_patch(patch))
    assert [(c.path, c.commit, c.lines) for c in chunks] == [
        ("my file.py", "b" * 40, [(1, "+ starts with plus")]),
    ]


def test_parse_patch_skips_oversized_files():
    patch = _patch("diff --git a/b b/b\n+++ b/big.txt\n@@ -0,0 +1,2 @@\n+" + "x" * 50 + "\n+y")
    assert list(parse_patch(patch, max_bytes=20)) == []


def test_unquote_path():
    assert unquote_path(b'"caf\\303\\251.py"') == "café.py"
    assert unquote_path(b'"b/a\\tb.py"') == "b/a\tb.py"
    assert unquote_path(b"plain.py") == "plain.py"


# --------------------------------------------------------------------------- #
# Reports
# --------------------------------------------------------------------------- #
def _findings(commit=None):
    token = "ghp_" + rnd(ALNUM, 36, 11)
    found = scan_text("src/my app.py", f'x = 1\ntoken = "{token}"\n', THRESHOLD)
    if commit:
        found = [f._replace(commit=commit) for f in found]
    return token, found


def test_json_report_is_redacted():
    token, findings = _findings(commit="c" * 40)
    data = json.loads(secrets_report.render(findings, "json", "range"))
    assert data["mode"] == "range"
    assert data["findings"][0] == {
        "rule_id": "github-token",
        "description": "GitHub token",
        "path": "src/my app.py",
        "line": 2,
        "column": 10,
        "commit": "c" * 40,
        "preview": findings[0].preview,
    }
    assert token not in json.dumps(data)


def test_sarif_report_structure():
    token, findings = _findings()
    log = json.loads(secrets_report.render(findings, "sarif", "all-files"))
    assert log["version"] == "2.1.0"
    assert log["$schema"].endswith("sarif-2.1.0.json")
    (run,) = log["runs"]
    driver = run["tool"]["driver"]
    assert driver["name"] == "git-hooks-collection"
    assert {r["id"] for r in driver["rules"]} == {r.id for r in RULES}
    (result,) = run["results"]
    assert result["ruleId"] == "github-token"
    assert driver["rules"][result["ruleIndex"]]["id"] == "github-token"
    assert result["level"] == "error"
    location = result["locations"][0]["physicalLocation"]
    assert location["artifactLocation"]["uri"] == "src/my%20app.py"
    assert location["region"]["startLine"] == 2
    assert location["region"]["startColumn"] == 10
    assert token not in json.dumps(log)
    assert location["region"]["snippet"]["text"] == findings[0].preview
