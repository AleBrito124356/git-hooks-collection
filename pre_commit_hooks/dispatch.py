"""Run the checks configured for a git stage, or one check on demand.

The installed stage wrappers in ``.githooks/`` call::

    python githooks-run.py <stage> "$@"

``<stage>`` is one of pre-commit, prepare-commit-msg, commit-msg, pre-push. The
dispatcher reads ``.githooks.yaml`` to find the checks enabled for that stage
and calls them in-process (for pre-push the refs on stdin are read once and
shared). It aggregates the results and exits non-zero if any check failed. A
check that crashes is reported and counted as a failure; the others still run.

``python githooks-run.py run <check|stage> [args]`` runs a single check (this is
what the standalone ``hooks/*`` wrappers use). Any other first argument is handed
to the ``githooks`` CLI, so ``python .githooks/githooks-run.py doctor`` works in
a repository that has the vendored copy but no pip install.
"""

from __future__ import annotations

import json
import os
import sys
import traceback

from . import __version__, _core, registry

# A failure of these stops the stage: later checks could echo the secret.
SECRET_CHECKS = ("secrets", "secrets-push")


def _stdin_for(stage: str, stdin_data: str | None) -> str | None:
    if stage != "pre-push" or stdin_data is not None:
        return stdin_data
    return _core.read_hook_stdin()


def run_check(name: str, argv: list[str], stdin_data: str | None = None) -> int:
    """Run one check by name, honouring GITHOOKS_SKIP. Crashes count as failures."""
    check = registry.get(name)
    if check is None:
        hint = registry.suggest(name)
        _core.error(f"unknown check '{name}'" + (f"; did you mean '{hint}'?" if hint else ""))
        return 2
    if _core.skip_requested(name):
        return 0
    stdin_data = _stdin_for(check.stage, stdin_data)
    try:
        return int(registry.entry_point(name)(list(argv), stdin_data=stdin_data) or 0)
    except Exception as exc:  # noqa: BLE001 - a buggy check must not wedge git
        _core.error(f"check '{name}' crashed: {type(exc).__name__}: {exc}")
        if os.environ.get("GITHOOKS_DEBUG"):
            traceback.print_exc()
        else:
            _core.info("set GITHOOKS_DEBUG=1 for the full traceback")
        return 1


def run_stage(stage: str, argv: list[str], stdin_data: str | None = None) -> int:
    with _core.memoized():
        return _run_stage(stage, argv, stdin_data)


def _run_stage(stage: str, argv: list[str], stdin_data: str | None) -> int:
    config = _core.load_config()
    checks = [str(c) for c in _core.cfg_list(config, f"hooks.{stage}")]
    stdin_data = _stdin_for(stage, stdin_data)

    failures = []
    for index, name in enumerate(checks):
        if registry.get(name) is None:
            hint = registry.suggest(name)
            _core.warn(
                f"unknown check '{name}' in hooks.{stage}; skipping"
                + (f" (did you mean '{hint}'?)" if hint else "")
            )
            continue
        if run_check(name, argv, stdin_data) != 0:
            failures.append(name)
            rest = [n for n in checks[index + 1 :] if registry.get(n)]
            if name in SECRET_CHECKS and rest:
                # Formatters, linters and test runners print source lines; the
                # secret report is redacted, their output would not be.
                _core.warn(
                    f"not running {', '.join(rest)}: a secret was found, and their "
                    "output could print it unredacted"
                )
                break

    if failures:
        _core.header(f"\n{len(failures)} check(s) failed at {stage}: {', '.join(failures)}")
        return 1
    return 0


def _probe() -> int:
    """Report the interpreter and package a wrapper resolves (githooks doctor)."""
    print(
        json.dumps(
            {
                "python": sys.executable,
                "version": list(sys.version_info[:3]),
                "package_version": __version__,
                "package_path": os.path.dirname(os.path.abspath(__file__)),
            }
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if os.environ.get("GITHOOKS_PROBE"):
        return _probe()
    if not argv:
        _core.error("expected a stage name (e.g. pre-commit) or a command (e.g. run, doctor)")
        return 2
    first, rest = argv[0], argv[1:]
    if first in registry.STAGES:
        return run_stage(first, rest)
    # Anything else is a `githooks` command: run, list, doctor, install ...
    from . import cli

    return cli.main(argv)
