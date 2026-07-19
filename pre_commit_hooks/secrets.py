"""Scan the staged changes for credentials before they reach a commit.

Why this exists
---------------
GitHub push protection (error ``GH013``) rejects a *push* when it finds a token
that matches a known provider format. That is late and painful: the secret is
already in your local history, so you have to rewrite commits, rotate the key,
and force-push. This hook moves that check to *commit time*, on the staged diff,
where a bad line costs you five seconds instead of an afternoon.

It looks for:
  - private-key blocks (RSA / EC / OPENSSH / PGP ...)
  - AWS access key ids (AKIA / ASIA ...)
  - Google API keys (AIza...)
  - Stripe secret keys (sk_live / sk_test / rk_live ...)
  - GitHub tokens (ghp_ / gho_ / ghs_ / github_pat_ ...)
  - Slack and Discord webhook URLs   <- the exact shape GH013 blocks
  - Slack / OpenAI / NVIDIA NIM / SendGrid keys, JWTs
  - generic ``KEY = "high entropy value"`` assignments and .env style lines

It deliberately does NOT flag obvious placeholders (``nvapi-XXXX...``,
``your_api_key_here``, ``changeme``, repeated-character fillers) so it stays
quiet on documentation and .env.example files.

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
import re
import sys
from typing import List, NamedTuple, Optional, Pattern

from . import _core

# Assemble the sensitive webhook host literals from parts so this file never
# contains a scannable webhook URL on disk.
_SLACK_HOST = "hooks." + "slack" + r"\.com"
_DISCORD_HOST = r"(?:ptb\.|canary\.)?discord(?:app)?" + r"\.com"

# Words / shapes that mean "this is a placeholder, not a real secret".
_PLACEHOLDER_TOKENS = (
    "xxxx",
    "example",
    "placeholder",
    "changeme",
    "your_",
    "your-",
    "yourkey",
    "yourtoken",
    "dummy",
    "redacted",
    "notreal",
    "fake",
    "sample",
    "test_",
    "todo",
    "fixme",
    "<",
    ">",
    "{{",
    "}}",
    "...",
    "0123456789",
    "abcdef",
)

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
    group: int          # capture group holding the token (0 = whole match)
    structured: bool    # True = strong provider format, skip the entropy gate
    min_entropy: float  # only used when structured is False


def _compile_rules() -> List[Rule]:
    return [
        Rule(
            "private-key",
            "Private key block",
            re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----"),
            0,
            True,
            0.0,
        ),
        Rule(
            "aws-access-key-id",
            "AWS access key id",
            re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA)[0-9A-Z]{16}\b"),
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
            re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
            0,
            True,
            0.0,
        ),
        Rule(
            "stripe-secret-key",
            "Stripe secret key",
            re.compile(r"\b(?:sk|rk)_(?:live|test)_[0-9A-Za-z]{16,}\b"),
            0,
            True,
            0.0,
        ),
        Rule(
            "github-token",
            "GitHub token",
            re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[0-9A-Za-z]{36,}\b"),
            0,
            True,
            0.0,
        ),
        Rule(
            "github-fine-grained-pat",
            "GitHub fine-grained PAT",
            re.compile(r"\bgithub_pat_[0-9A-Za-z_]{22,}\b"),
            0,
            True,
            0.0,
        ),
        Rule(
            "slack-token",
            "Slack token",
            re.compile(r"\bxox[baprs]-[0-9A-Za-z]{10,}(?:-[0-9A-Za-z]{6,})*\b"),
            0,
            True,
            0.0,
        ),
        Rule(
            "slack-webhook",
            "Slack incoming webhook URL",
            re.compile(
                r"https?://" + _SLACK_HOST
                + r"/services/T[A-Z0-9]{7,}/B[A-Z0-9]{7,}/[A-Za-z0-9]{16,}"
            ),
            0,
            True,
            0.0,
        ),
        Rule(
            "discord-webhook",
            "Discord webhook URL",
            re.compile(
                r"https?://" + _DISCORD_HOST
                + r"/api/(?:v\d+/)?webhooks/\d{16,}/[0-9A-Za-z_\-]{24,}"
            ),
            0,
            True,
            0.0,
        ),
        Rule(
            "nvidia-nim-key",
            "NVIDIA NIM API key",
            re.compile(r"\bnvapi-[0-9A-Za-z_\-]{16,}\b"),
            0,
            True,
            0.0,
        ),
        Rule(
            "openai-key",
            "OpenAI API key",
            re.compile(r"\bsk-(?:proj-)?[0-9A-Za-z_\-]{20,}\b"),
            0,
            True,
            0.0,
        ),
        Rule(
            "sendgrid-key",
            "SendGrid API key",
            re.compile(r"\bSG\.[0-9A-Za-z_\-]{20,}\.[0-9A-Za-z_\-]{30,}\b"),
            0,
            True,
            0.0,
        ),
        Rule(
            "jwt",
            "JSON Web Token",
            re.compile(
                r"\beyJ[0-9A-Za-z_\-]{10,}\.eyJ[0-9A-Za-z_\-]{10,}\.[0-9A-Za-z_\-]{10,}\b"
            ),
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


def _looks_like_placeholder(value: str) -> bool:
    low = value.lower()
    for token in _PLACEHOLDER_TOKENS:
        if token in low:
            return True
    # Four or more of the same alphanumeric character in a row (XXXX, 0000 ...).
    # Restricted to alphanumerics so legitimate delimiters like the "-----" in a
    # PEM header are not mistaken for filler.
    if re.search(r"([A-Za-z0-9])\1{3,}", value):
        return True
    # Overwhelmingly one character.
    most_common = max((value.count(c) for c in set(value)), default=0)
    if value and most_common / len(value) > 0.6:
        return True
    return False


def _line_allowlisted(line: str) -> bool:
    low = line.lower()
    return any(pragma in low for pragma in _ALLOW_PRAGMAS)


def _value_allowlisted(value: str, allow_regexes: List[Pattern[str]]) -> bool:
    return any(rx.search(value) for rx in allow_regexes)


def _excluded(path: str, patterns: List[str]) -> bool:
    return any(fnmatch.fnmatch(path, pat) for pat in patterns)


def scan_text(
    path: str,
    text: str,
    entropy_threshold: float,
    allow_regexes: Optional[List[Pattern[str]]] = None,
) -> List[Finding]:
    """Scan a single file's text and return findings. Pure and unit-testable."""
    allow_regexes = allow_regexes or []
    findings: List[Finding] = []
    seen = set()
    lines = text.splitlines()
    for lineno, line in enumerate(lines, start=1):
        if _line_allowlisted(line):
            continue
        for rule in RULES:
            for match in rule.regex.finditer(line):
                token = match.group(rule.group)
                if not token:
                    continue
                if _looks_like_placeholder(token):
                    continue
                if _value_allowlisted(token, allow_regexes):
                    continue
                if not rule.structured:
                    threshold = max(rule.min_entropy, entropy_threshold)
                    if _core.shannon_entropy(token) < threshold:
                        continue
                key = (lineno, rule.id, match.start())
                if key in seen:
                    continue
                seen.add(key)
                findings.append(
                    Finding(
                        path=path,
                        line=lineno,
                        column=match.start(rule.group) + 1,
                        rule_id=rule.id,
                        description=rule.description,
                        preview=_redact(token),
                    )
                )
    return findings


def _redact(token: str) -> str:
    token = token.replace("\n", "")
    if len(token) <= 12:
        return token[:3] + "…"
    return f"{token[:6]}…{token[-4:]} ({len(token)} chars)"


def scan_files(
    files: List[str],
    config: Optional[dict] = None,
) -> List[Finding]:
    config = config or _core.load_config()
    threshold = float(_core.cfg(config, "secrets.entropy_threshold", 3.2))
    max_bytes = int(_core.cfg(config, "secrets.max_file_bytes", 1_000_000))
    exclude = list(_core.cfg(config, "secrets.exclude", []) or [])
    allow_raw = list(_core.cfg(config, "secrets.allow_regex", []) or [])
    allow_regexes = []
    for pat in allow_raw:
        try:
            allow_regexes.append(re.compile(pat))
        except re.error:
            _core.warn(f"ignoring invalid secrets.allow_regex entry: {pat!r}")

    findings: List[Finding] = []
    for path in files:
        if _excluded(path, exclude):
            continue
        data = _core.read_staged_or_disk(path)
        if data is None:
            continue
        if len(data) > max_bytes:
            continue
        if _core.is_probably_binary(data):
            continue
        text = data.decode("utf-8", errors="replace")
        findings.extend(scan_text(path, text, threshold, allow_regexes))
    return findings


def _print_report(findings: List[Finding]) -> None:
    _core.header("\nPotential secrets found in staged changes:\n")
    for f in findings:
        location = f"{_core.C.BOLD}{f.path}:{f.line}:{f.column}{_core.C.RESET}"
        print(
            f"  {location}  {_core.C.RED}{f.description}{_core.C.RESET} "
            f"{_core.C.DIM}[{f.rule_id}]{_core.C.RESET}",
            file=sys.stderr,
        )
        print(f"      {_core.C.DIM}matched: {f.preview}{_core.C.RESET}", file=sys.stderr)
    print("", file=sys.stderr)
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


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
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
