# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
