"""Scan staged changes, commits or the whole tree for credentials.

Why this exists
---------------
GitHub push protection (error ``GH013``) rejects a *push* when it finds a token
that matches a known provider format. That is late and painful: the secret is
already in your local history, so you have to rewrite commits, rotate the key,
and force-push. This hook moves that check to *commit time*, where a bad line
costs you five seconds instead of an afternoon -- and ``secrets-push`` repeats
it over every commit you push, the way GitHub does.

Usage
-----
    githooks-secrets                      scan the staged diff (the pre-commit hook)
    githooks-secrets FILE...              scan these files (pre-commit framework)
    githooks-secrets --range A..B         scan the lines each commit in A..B adds
    githooks-secrets --all-files          audit every tracked file
    githooks-secrets --format sarif -o secrets.sarif --all-files

Options: ``--scan diff|file`` (override ``secrets.scan``), ``--format
text|json|sarif``, ``--output FILE``, ``--exit-zero``.

With ``secrets.scan: diff`` (the default) only the lines being added are
scanned, so text already in the repository is not reported again on every
commit that touches the file. A file passed explicitly that has no staged
changes is scanned whole, so ``pre-commit run --all-files`` audits the tree.

It looks for private keys; AWS, Google, Stripe, GitHub, GitLab, npm, PyPI,
Slack, OpenAI, Anthropic, Hugging Face, Shopify, DigitalOcean, Telegram, Azure
storage, Twilio, NVIDIA NIM and SendGrid credentials; Slack and Discord webhook
URLs (the shape GH013 blocks); JWTs; and high-entropy ``KEY = "..."`` or
``.env`` assignments. Obvious placeholders (``nvapi-XXXX...``,
``your_api_key_here``, ``<token>``) are ignored: the placeholder test runs on
the random part of a token and only fires when filler dominates it, so
``sk_test_...`` keys and real tokens that contain "fake" are still reported.
Reports never contain a raw secret, only a redacted preview.
"""

from __future__ import annotations

import argparse
import sys
from typing import Iterable

from . import _core, secrets_report, secrets_sources
from .secrets_engine import (  # noqa: F401 - re-exported for callers and tests
    RULES,
    Finding,
    Rule,
    Settings,
    _excluded,
    _looks_like_placeholder,
    _redact,
    dedupe,
    scan_lines,
    scan_text,
    settings_from_config,
)
from .secrets_sources import Chunk

CHECK_NAME = "secrets"

ADVICE = {
    "staged": (
        "\n  What to do:\n"
        "    - Remove the secret and load it from an environment variable instead.\n"
        "    - If it was ever committed or pushed, rotate the key. It is compromised.\n"
        "    - False positive? Add a trailing '# pragma: allowlist secret' to the line,\n"
        "      or add a path to secrets.exclude / a pattern to secrets.allow_regex.\n"
        "    - Genuinely need to bypass once: git commit --no-verify (you own that risk).\n"
    ),
    "range": (
        "\n  These secrets are already inside commits. Deleting the line in a new commit\n"
        "  is not enough: the old commit still carries it, and GitHub push protection\n"
        "  (GH013) will reject the push anyway.\n"
        "    - Rotate the key now; treat it as leaked.\n"
        "    - Rewrite the commits that add it: git rebase -i <sha>^  (edit, remove, amend)\n"
        "    - False positive? '# pragma: allowlist secret' on the line, or\n"
        "      secrets.exclude / secrets.allow_regex in .githooks.yaml.\n"
        "    - Bypass once: git push --no-verify (you own that risk).\n"
    ),
    "all-files": (
        "\n  These secrets are in the tracked tree. Rotate them, remove them, and if they\n"
        "  were ever pushed, purge them from history (git filter-repo) as well.\n"
    ),
}
ADVICE["files"] = ADVICE["staged"]


# --------------------------------------------------------------------------- #
# Scanning
# --------------------------------------------------------------------------- #
def scan_chunks(chunks: Iterable[Chunk], settings: Settings) -> list[Finding]:
    findings: list[Finding] = []
    for chunk in chunks:
        if _excluded(chunk.path, settings.exclude):
            continue
        findings.extend(
            scan_lines(
                chunk.path,
                chunk.lines,
                settings.entropy_threshold,
                settings.allow_regexes,
                commit=chunk.commit,
            )
        )
    return dedupe(findings)


def scan_staged(settings: Settings) -> list[Finding]:
    """The pre-commit check: what this commit adds (or whole staged files)."""
    if settings.scan == "file":
        return scan_chunks(
            secrets_sources.files(_core.staged_files(), settings.max_bytes), settings
        )
    return scan_chunks(secrets_sources.staged_diff(settings.max_bytes), settings)


def scan_paths(paths: list[str], settings: Settings) -> list[Finding]:
    """Explicit files (the pre-commit framework passes the staged ones).

    In diff mode a file with staged changes contributes only its added lines;
    a file with none (``pre-commit run --all-files``) is scanned whole.
    """
    if settings.scan == "file":
        return scan_chunks(secrets_sources.files(paths, settings.max_bytes), settings)
    wanted = set(paths)
    staged = set(_core.staged_files()) & wanted
    chunks = [c for c in secrets_sources.staged_diff(settings.max_bytes) if c.path in wanted]
    rest = [p for p in paths if p not in staged]
    return scan_chunks([*chunks, *secrets_sources.files(rest, settings.max_bytes)], settings)


def scan_ranges(ranges: list[list[str]], settings: Settings) -> list[Finding]:
    findings: list[Finding] = []
    for rev_args in ranges:
        findings.extend(
            scan_chunks(secrets_sources.commit_range(rev_args, settings.max_bytes), settings)
        )
    return dedupe(findings)


def scan_all_files(settings: Settings) -> list[Finding]:
    chunks = secrets_sources.all_files(
        settings.max_bytes, keep=lambda path: not _excluded(path, settings.exclude)
    )
    return scan_chunks(chunks, settings)


def scan_files(files: list[str], config: dict | None = None) -> list[Finding]:
    """Scan whole files as staged (kept for API compatibility with 0.1.0)."""
    settings = settings_from_config(config or _core.load_config())
    return scan_chunks(secrets_sources.files(files, settings.max_bytes), settings)


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def report(
    findings: list[Finding], mode: str, fmt: str = "text", output: str | None = None
) -> None:
    if fmt == "text":
        if findings:
            secrets_report.print_text(findings, mode)
            if mode in ("staged", "files"):
                _core.warn(
                    "GitHub push protection (GH013) would reject a push containing these. "
                    "Fix them now, while it is cheap."
                )
            print(ADVICE.get(mode, ADVICE["staged"]), file=sys.stderr)
        return
    rendered = secrets_report.render(findings, fmt, mode)
    if output:
        with open(output, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(rendered)
        _core.info(f"wrote {len(findings)} finding(s) to {output} ({fmt})")
    else:
        sys.stdout.write(rendered)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="githooks-secrets",
        description="Scan staged changes, commits or tracked files for credentials.",
    )
    p.add_argument("files", nargs="*", help="files to scan (default: the staged changes)")
    source = p.add_mutually_exclusive_group()
    source.add_argument(
        "--range",
        action="append",
        metavar="REVS",
        help="scan the lines each commit in this range adds, e.g. origin/main..HEAD",
    )
    source.add_argument("--all-files", action="store_true", help="audit every tracked file")
    p.add_argument("--scan", choices=["diff", "file"], help="override secrets.scan")
    p.add_argument("--format", choices=["text", "json", "sarif"], default="text")
    p.add_argument("-o", "--output", help="write the json/sarif report to this file")
    p.add_argument("--exit-zero", action="store_true", help="exit 0 even when secrets are found")
    return p


def main(argv: list[str] | None = None, stdin_data: str | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if _core.skip_requested(CHECK_NAME):
        return 0
    args = build_parser().parse_args(argv)

    settings = settings_from_config(_core.load_config())
    if args.scan:
        settings = settings._replace(scan=args.scan)

    if args.range:
        mode, findings = "range", scan_ranges([r.split() for r in args.range], settings)
    elif args.all_files:
        mode, findings = "all-files", scan_all_files(settings)
    elif args.files:
        mode, findings = "files", scan_paths(args.files, settings)
    else:
        mode, findings = "staged", scan_staged(settings)

    report(findings, mode, args.format, args.output)
    return 1 if findings and not args.exit_zero else 0


if __name__ == "__main__":
    raise SystemExit(main())
