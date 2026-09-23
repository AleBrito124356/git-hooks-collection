# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-09-23

### Added
- `secrets-push` check (pre-push, on by default in new configs): scans the lines
  added by every commit being pushed and reports `<sha> path:line:col`, so a
  secret committed with `--no-verify`, before the hooks were installed or on
  another machine is stopped before GitHub push protection (GH013) sees it.
  Also available as the `githooks-secrets-push` pre-commit hook id.
- Secret scanner: `--range A..B`, `--all-files`, `--format text|json|sarif`
  (SARIF 2.1.0 for code scanning), `--output`, `--exit-zero`; `secrets.scan:
  diff|file`. New rules: GitLab PAT, npm, PyPI, Anthropic, Hugging Face,
  Shopify, DigitalOcean, Telegram bot, Azure storage AccountKey, Twilio API key.
- `githooks` CLI (`python -m pre_commit_hooks`, or `python
  .githooks/githooks-run.py <command>` from the vendored copy): `install`,
  `uninstall`, `run <stage|check> [--all-files]`, `list`, `doctor`, `version`.
- `githooks doctor`: config parse parity (PyYAML vs bundled parser), unknown
  checks / stages / sections / settings with "did you mean", value types,
  regexes and modes, `core.hooksPath`, wrapper presence / shebang / line endings
  / exec bit, the interpreter the wrappers really run (probe), vendored-copy
  drift, missing pre-commit hook types, the never-released `v0.1.0` pin,
  issue-prefix vs Conventional Commits, and formatter / linter availability per
  language in the repository. Exits 1 on errors.
- Config typos are also warned about once per hook run.
- `issue_prefix.mode` (`trailer` default, `prefix`) and `trailer_key`;
  `conventional_commit.ignore_prefix_regex`.
- `format.exclude` / `lint.exclude` (default `.githooks/*`) and a `web` language
  bucket (JSON, CSS, HTML, Markdown, YAML) that prettier formats and no default
  linter receives.
- Installer: `--force`, `--rev`, `--repo-url`; `.githooks/.gitignore` and
  `.githooks/.gitattributes`; warnings for `.git/hooks` scripts (git-lfs) that
  `core.hooksPath` bypasses.
- `GITHOOKS_PYTHON`, `GITHOOKS_DEBUG` and `GITHOOKS_FORCE_MINIYAML` environment
  variables.
- Integration test suite that drives real `git commit` / `git push` through the
  native install, the standalone wrappers and the pre-commit framework, under
  both YAML parsers.

### Changed
- The pre-commit secret check scans the staged diff (the lines being added),
  as the README always said, instead of whole files; `secrets.scan: file`
  restores the old behaviour. Explicit file arguments without staged changes are
  still scanned whole.
- `issue-prefix` now appends a `Refs: ABC-123` trailer by default instead of
  prepending `[ABC-123] ` (set `issue_prefix.mode: prefix` for the old style).
- `no-fixup` patterns match whole words (`WIP` no longer blocks "swipe").
- Re-running the installer with `--hooks`/`--all` rewrites only the `hooks:`
  block of an existing config and removes wrappers of stages with no checks.
  Wrappers are generated only for stages that have checks.
- `install.py` is a thin shim over `pre_commit_hooks/installer.py`; defaults
  come from one template, `pre_commit_hooks/templates/githooks.yaml`.
- Once `secrets` or `secrets-push` fails, the remaining checks of that stage are
  skipped so no other tool prints the secret unredacted.

### Fixed
- Every commit on a ticket branch was rejected: the default issue prefix broke
  the Conventional Commits header check.
- The format hook re-staged whole files, committing unstaged hunks (including
  secrets the scanner never saw). Only fully staged files are re-staged now.
- Renamed / copied files, files with non-ASCII names on Windows and UTF-8 files
  with many non-ASCII characters were skipped by every content check.
- `no-fixup` never checked new branches (its revision range reached git as one
  argument), never blocked under the pre-commit framework (no stdin), and
  crashed on PyYAML's reading of the unquoted `- wip:` pattern.
- Placeholder detection dropped `sk_test_` keys and real tokens containing
  words like "fake"; one secret could be reported twice (and Anthropic keys as
  OpenAI keys); nested lockfiles escaped `secrets.exclude`.
- All `hooks/*` wrappers failed on Git Bash for Windows (`PYTHONPATH` was not
  translated); they were also committed without the executable bit.
- The installer overwrote a foreign `core.hooksPath`, misreported a narrower
  re-install, and left vendored bytecode untracked in `.githooks/`.
- The pre-commit snippet pinned `rev: v0.1.0`, a tag that does not exist.
- `lint` sent Markdown/YAML/JSON/CSS/HTML to eslint; Windows `.cmd` tools
  (prettier, eslint, npm) could not be launched; configured commands lost
  Windows backslash paths.
- Committing the vendored `.githooks/` with ruff installed reformatted and then
  rejected the hooks' own files.
- `GITHOOKS_SKIP` was ignored outside the native dispatcher.
- A value that is only an interpolation (`token = "{make_token()}"`, `"$TOKEN"`)
  was reported as a high-entropy secret.

## [0.1.0] - 2026-07-19

### Added
- Nine hooks: secret scanner, formatter, linter, large-file guard, branch
  protection, Conventional Commits validator, pre-push test runner, fixup/WIP
  guard, and issue-id prefixer.
- `install.py` with two delivery paths: a native installer that vendors the
  hooks into `.githooks/` and sets `core.hooksPath`, and a `--mode pre-commit`
  path that writes a `.pre-commit-config.yaml`.
- `.pre-commit-hooks.yaml` plus console-script entry points so the repository can
  be referenced directly from another project's `.pre-commit-config.yaml`.
- `.githooks.yaml` config with per-check thresholds, read by a bundled
  dependency-free mini-YAML parser when PyYAML is not installed.
- Test suite covering the secret scanner, the Conventional Commits validator,
  the large-file threshold, and the config parser.
