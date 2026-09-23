"""Scan the staged changes for credentials before they reach a commit.

Why this exists
---------------
GitHub push protection (error ``GH013``) rejects a *push* when it finds a token
that matches a known provider format. That is late and painful: the secret is
already in your local history, so you have to rewrite commits, rotate the key,
and force-push. This hook moves that check to *commit time*, where a bad line
costs you five seconds instead of an afternoon.

It looks for:
  - private-key blocks (RSA / EC / OPENSSH / PGP ...)
  - AWS access key ids (AKIA / ASIA ...) and secret access keys
  - Google API keys (AIza...)
  - Stripe secret and restricted keys (sk_live / sk_test / rk_live / rk_test)
  - GitHub tokens (ghp_ / gho_ / ghu_ / ghs_ / ghr_ / github_pat_)
  - Slack and Discord webhook URLs   <- the exact shape GH013 blocks
  - Slack / OpenAI / Anthropic / NVIDIA NIM / SendGrid keys, JWTs
  - generic ``KEY = "high entropy value"`` assignments and .env style lines

It deliberately does NOT flag obvious placeholders (``nvapi-XXXX...``,
``your_api_key_here``, ``changeme``, repeated-character fillers) so it stays
quiet on documentation and .env.example files. The placeholder test runs on the
random part of a token (after its provider prefix) and only fires when filler
dominates it, so ``sk_test_...`` keys and real tokens that happen to contain
"fake" or "todo" are still reported.

Push-safety note
----------------
Every credential-shaped literal in this file (the Slack / Discord host strings)
is assembled from concatenated parts at runtime, and the detection regexes carry
provider prefixes followed by character classes -- never a full, valid token. So
this scanner catches real secrets without the scanner's own source ever tripping
a secret scanner.
"""

from __future__ import annotations

import fnmatch
import posixpath
import re
import sys
from typing import Iterable, NamedTuple, Pattern

from . import _core

CHECK_NAME = "secrets"

# Assemble the sensitive webhook host literals from parts so this file never
# contains a scannable webhook URL on disk.
_SLACK_HOST = "hooks." + "slack" + r"\.com"
_DISCORD_HOST = r"(?:ptb\.|canary\.)?discord(?:app)?" + r"\.com"

# Words that make up placeholder values ("your_api_key_here", "fake-token").
# They only count when, together with filler runs, they cover most of a value.
_PLACEHOLDER_WORDS = (
    "example",
    "placeholder",
    "changeme",
    "change",
    "replace",
    "insert",
    "your",
    "dummy",
    "redacted",
    "notreal",
    "real",
    "fake",
    "sample",
    "test",
    "todo",
    "fixme",
    "here",
    "secret",
    "token",
    "key",
    "api",
    "password",
    "passwd",
    "value",
    "demo",
    "mock",
    "foo",
    "bar",
    "baz",
    "abc",
    "xyz",
    "none",
    "null",
    "empty",
    "string",
    "goes",
    "put",
    "the",
    "my",
    "not",
)
_PLACEHOLDER_RX = re.compile(
    "|".join(sorted(_PLACEHOLDER_WORDS, key=len, reverse=True)), re.IGNORECASE
)
# Template / elision markers: "<your-token>", "{{ token }}", "${TOKEN}", "sk-...".
_TEMPLATE_MARKERS = ("<", ">", "{{", "}}", "${", "%(", "...", "…")
_SEPARATORS = set("_-./: ")
# A value counts as a placeholder when filler covers at least this share of it.
_PLACEHOLDER_DOMINANCE = 0.5

# Inline pragmas that suppress a finding on a single line.
_ALLOW_PRAGMAS = (
    "githooks: allow-secret",
    "githooks:allow-secret",
    "pragma: allowlist secret",
    "nosecret",
    "gitleaks:allow",
)


class Rule(NamedTuple):
    id: str
    description: str
    regex: Pattern[str]
    group: int | str  # capture group holding the token (0 = whole match)
    structured: bool  # True = strong provider format, skip the entropy gate
    min_entropy: float  # only used when structured is False
    placeholder_check: bool = True


def _compile_rules() -> list[Rule]:
    # Order is priority: when two rules match the same characters only the first
    # is reported, so specific provider formats come before generic assignments.
    # Provider rules name the random part of the token "body"; the placeholder
    # test runs on it, so a prefix like "sk_test_" can never mark a key as fake.
    return [
        Rule(
            "private-key",
            "Private key block",
            re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----"),
            0,
            True,
            0.0,
            placeholder_check=False,
        ),
        Rule(
            "aws-access-key-id",
            "AWS access key id",
            re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA)(?P<body>[0-9A-Z]{16})\b"),
            0,
            True,
            0.0,
        ),
        Rule(
            "aws-secret-access-key",
            "AWS secret access key",
            re.compile(
                r"(?i)aws.{0,24}?(?:secret|private).{0,24}?['\"]?[:=]\s*['\"]?"
                r"([A-Za-z0-9/+]{40})\b"
            ),
            1,
            False,
            3.6,
        ),
        Rule(
            "gcp-api-key",
            "Google API key",
            re.compile(r"\bAIza(?P<body>[0-9A-Za-z_\-]{35})(?![0-9A-Za-z_\-])"),
            0,
            True,
            0.0,
        ),
        Rule(
            "stripe-secret-key",
            "Stripe secret key",
            re.compile(r"\b(?:sk|rk)_(?:live|test)_(?P<body>[0-9A-Za-z]{16,})\b"),
            0,
            True,
            0.0,
        ),
        Rule(
            "github-token",
            "GitHub token",
            re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_(?P<body>[0-9A-Za-z]{36,})\b"),
            0,
            True,
            0.0,
        ),
        Rule(
            "github-fine-grained-pat",
            "GitHub fine-grained PAT",
            re.compile(r"\bgithub_pat_(?P<body>[0-9A-Za-z_]{22,})\b"),
            0,
            True,
            0.0,
        ),
        Rule(
            "slack-token",
            "Slack token",
            re.compile(r"\bxox[baprs]-(?P<body>[0-9A-Za-z]{10,}(?:-[0-9A-Za-z]{6,})*)\b"),
            0,
            True,
            0.0,
        ),
        Rule(
            "slack-webhook",
            "Slack incoming webhook URL",
            re.compile(
                r"https?://"
                + _SLACK_HOST
                + r"/services/T[A-Z0-9]{7,}/B[A-Z0-9]{7,}/(?P<body>[A-Za-z0-9]{16,})"
            ),
            0,
            True,
            0.0,
        ),
        Rule(
            "discord-webhook",
            "Discord webhook URL",
            re.compile(
                r"https?://"
                + _DISCORD_HOST
                + r"/api/(?:v\d+/)?webhooks/\d{16,}/(?P<body>[0-9A-Za-z_\-]{24,})"
            ),
            0,
            True,
            0.0,
        ),
        Rule(
            "nvidia-nim-key",
            "NVIDIA NIM API key",
            re.compile(r"\bnvapi-(?P<body>[0-9A-Za-z_\-]{16,})(?![0-9A-Za-z_\-])"),
            0,
            True,
            0.0,
        ),
        Rule(
            "anthropic-key",
            "Anthropic API key",
            re.compile(
                r"\bsk-ant-(?:api|admin|sid)\d{2}-(?P<body>[0-9A-Za-z_\-]{32,})(?![0-9A-Za-z_\-])"
            ),
            0,
            True,
            0.0,
        ),
        Rule(
            "openai-key",
            "OpenAI API key",
            re.compile(
                r"\bsk-(?!ant-)(?:proj-|svcacct-|admin-)?(?P<body>[0-9A-Za-z_\-]{20,})"
                r"(?![0-9A-Za-z_\-])"
            ),
            0,
            True,
            0.0,
        ),
        Rule(
            "sendgrid-key",
            "SendGrid API key",
            re.compile(r"\bSG\.(?P<body>[0-9A-Za-z_\-]{20,}\.[0-9A-Za-z_\-]{30,})"),
            0,
            True,
            0.0,
        ),
        Rule(
            "jwt",
            "JSON Web Token",
            re.compile(r"\beyJ[0-9A-Za-z_\-]{10,}\.eyJ[0-9A-Za-z_\-]{10,}\.[0-9A-Za-z_\-]{10,}"),
            0,
            True,
            0.0,
        ),
        Rule(
            "generic-assignment",
            "High-entropy secret assignment",
            re.compile(
                r"(?i)(?:api[_-]?key|secret|token|password|passwd|pwd|"
                r"access[_-]?key|auth[_-]?token|client[_-]?secret|private[_-]?key)"
                r"\s*[:=]\s*['\"]([^'\"\n]{12,120})['\"]"
            ),
            1,
            False,
            3.2,
        ),
        Rule(
            "dotenv-assignment",
            "Secret in an environment assignment",
            re.compile(
                r"(?m)^(?:export\s+)?[A-Z][A-Z0-9_]*"
                r"(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)S?\s*=\s*"
                r"['\"]?([^\s'\"#]{12,200})"
            ),
            1,
            False,
            3.2,
        ),
    ]


RULES = _compile_rules()


class Finding(NamedTuple):
    path: str
    line: int
    column: int
    rule_id: str
    description: str
    preview: str
    commit: str | None = None


# --------------------------------------------------------------------------- #
# Placeholder heuristics
# --------------------------------------------------------------------------- #
def _filler_mask(value: str) -> list[bool]:
    """Mark the characters of ``value`` that are filler rather than randomness."""
    covered = [False] * len(value)
    low = value.lower()
    for m in _PLACEHOLDER_RX.finditer(value):
        for i in range(m.start(), m.end()):
            covered[i] = True
    # Runs of one repeated character (XXXX, 0000, ****).
    for m in re.finditer(r"(.)\1{3,}", low):
        for i in range(m.start(), m.end()):
            covered[i] = True
    # Ascending / descending runs (1234, abcd, 9876).
    i = 0
    while i < len(low) - 1:
        j = i
        step = ord(low[i + 1]) - ord(low[i])
        if step in (1, -1) and low[i].isalnum():
            while (
                j + 1 < len(low) and ord(low[j + 1]) - ord(low[j]) == step and low[j + 1].isalnum()
            ):
                j += 1
        if j - i + 1 >= 4:
            for k in range(i, j + 1):
                covered[k] = True
            i = j + 1
        else:
            i += 1
    return covered


def _looks_like_placeholder(value: str) -> bool:
    """True when ``value`` is filler: template syntax, repeated or example text."""
    if not value:
        return True
    if any(marker in value for marker in _TEMPLATE_MARKERS):
        return True
    low = value.lower()
    # Overwhelmingly one character, or almost no variety at all.
    most_common = max(low.count(c) for c in set(low))
    if most_common / len(low) > 0.6:
        return True
    if len(value) >= 8 and len(set(low)) <= 3:
        return True
    covered = _filler_mask(value)
    countable = [c for ch, c in zip(value, covered) if ch not in _SEPARATORS]
    if not countable:
        return True
    return sum(countable) / len(countable) >= _PLACEHOLDER_DOMINANCE


def _line_allowlisted(line: str) -> bool:
    low = line.lower()
    return any(pragma in low for pragma in _ALLOW_PRAGMAS)


def _value_allowlisted(value: str, allow_regexes: list[Pattern[str]]) -> bool:
    return any(rx.search(value) for rx in allow_regexes)


def _excluded(path: str, patterns: list[str]) -> bool:
    """Match exclude globs against the full path and against the file name.

    ``package-lock.json`` must exclude ``web/package-lock.json`` too; matching
    the full path only made every nested lockfile slip past the exclusion.
    """
    norm = path.replace("\\", "/")
    base = posixpath.basename(norm)
    return any(fnmatch.fnmatch(norm, pat) or fnmatch.fnmatch(base, pat) for pat in patterns)


def _overlaps(span: tuple[int, int], claimed: list[tuple[int, int]]) -> bool:
    return any(span[0] < end and start < span[1] for start, end in claimed)


def scan_lines(
    path: str,
    lines: Iterable[tuple[int, str]],
    entropy_threshold: float,
    allow_regexes: list[Pattern[str]] | None = None,
    commit: str | None = None,
) -> list[Finding]:
    """Scan (line number, text) pairs. Pure and unit-testable.

    Each stretch of characters is reported at most once: rules run in priority
    order and a match that overlaps an earlier one (reported, allow-listed or
    recognised as a placeholder) is dropped. One secret, one finding.
    """
    allow_regexes = allow_regexes or []
    findings: list[Finding] = []
    for lineno, line in lines:
        if _line_allowlisted(line):
            continue
        claimed: list[tuple[int, int]] = []
        for rule in RULES:
            for match in rule.regex.finditer(line):
                token = match.group(rule.group)
                if not token:
                    continue
                span = match.span(rule.group)
                if _overlaps(span, claimed):
                    continue
                body = token
                if "body" in rule.regex.groupindex and match.group("body"):
                    body = match.group("body")
                if rule.placeholder_check and _looks_like_placeholder(body):
                    claimed.append(span)
                    continue
                if _value_allowlisted(token, allow_regexes):
                    claimed.append(span)
                    continue
                if not rule.structured:
                    threshold = max(rule.min_entropy, entropy_threshold)
                    if _core.shannon_entropy(token) < threshold:
                        continue
                claimed.append(span)
                findings.append(
                    Finding(
                        path=path,
                        line=lineno,
                        column=span[0] + 1,
                        rule_id=rule.id,
                        description=rule.description,
                        preview=_redact(token),
                        commit=commit,
                    )
                )
    findings.sort(key=lambda f: (f.line, f.column))
    return findings


def scan_text(
    path: str,
    text: str,
    entropy_threshold: float,
    allow_regexes: list[Pattern[str]] | None = None,
) -> list[Finding]:
    """Scan a single file's text and return findings. Pure and unit-testable."""
    return scan_lines(path, enumerate(text.splitlines(), start=1), entropy_threshold, allow_regexes)


def _redact(token: str) -> str:
    token = token.replace("\n", "")
    if len(token) <= 12:
        return token[:3] + "…"
    return f"{token[:6]}…{token[-4:]} ({len(token)} chars)"


def _allow_regexes(config: dict) -> list[Pattern[str]]:
    compiled = []
    for pat in _core.cfg_list(config, "secrets.allow_regex"):
        try:
            compiled.append(re.compile(str(pat)))
        except re.error:
            _core.warn(f"ignoring invalid secrets.allow_regex entry: {pat!r}")
    return compiled


def scan_files(
    files: list[str],
    config: dict | None = None,
) -> list[Finding]:
    config = config or _core.load_config()
    threshold = float(_core.cfg(config, "secrets.entropy_threshold", 3.2))
    max_bytes = int(_core.cfg(config, "secrets.max_file_bytes", 1_000_000))
    exclude = [str(p) for p in _core.cfg_list(config, "secrets.exclude")]
    allow_regexes = _allow_regexes(config)

    wanted = [p for p in files if not _excluded(p, exclude)]
    contents = _core.read_staged(wanted)
    findings: list[Finding] = []
    for path in wanted:
        data = contents.get(path)
        if data is None:
            continue
        if len(data) > max_bytes:
            _core.warn(
                f"not scanning {path}: {_core.human_size(len(data))} is over secrets.max_file_bytes"
            )
            continue
        if _core.is_probably_binary(data):
            continue
        text = data.decode("utf-8", errors="replace")
        findings.extend(scan_text(path, text, threshold, allow_regexes))
    return findings


def _print_report(findings: list[Finding]) -> None:
    _core.header("\nPotential secrets found in staged changes:\n")
    for f in findings:
        location = f"{_core.C.BOLD}{f.path}:{f.line}:{f.column}{_core.C.RESET}"
        print(
            f"  {location}  {_core.C.RED}{f.description}{_core.C.RESET} "
            f"{_core.C.DIM}[{f.rule_id}]{_core.C.RESET}",
            file=sys.stderr,
        )
        print(f"      {_core.C.DIM}matched: {f.preview}{_core.C.RESET}", file=sys.stderr)
    print(file=sys.stderr)
    _core.warn(
        "GitHub push protection (GH013) would reject a push containing these. "
        "Fix them now, while it is cheap."
    )
    print(
        "\n  What to do:\n"
        "    - Remove the secret and load it from an environment variable instead.\n"
        "    - If it was ever committed or pushed, rotate the key. It is compromised.\n"
        "    - False positive? Add a trailing '# pragma: allowlist secret' to the line,\n"
        "      or add a path to secrets.exclude / a pattern to secrets.allow_regex.\n"
        "    - Genuinely need to bypass once: git commit --no-verify (you own that risk).\n",
        file=sys.stderr,
    )


def main(argv: list[str] | None = None, stdin_data: str | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if _core.skip_requested(CHECK_NAME):
        return 0

    config = _core.load_config()
    # pre-commit passes staged filenames as arguments; standalone gets none.
    files = [a for a in argv if not a.startswith("-")]
    if not files:
        files = _core.staged_files()
    if not files:
        return 0

    findings = scan_files(files, config)
    if not findings:
        return 0
    _print_report(findings)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
