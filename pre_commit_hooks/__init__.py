"""git-hooks-collection: a curated set of git hooks with a one-command installer.

The modules in this package each expose a ``main(argv=None)`` entry point and are
wired up both as console scripts (see pyproject.toml) for the pre-commit
framework and as in-process callables for the native dispatcher (dispatch.py,
reached through githooks-run.py). ``python -m pre_commit_hooks`` is the
``githooks`` CLI.
"""

__version__ = "0.2.0"

__all__ = [
    "branch_protect",
    "cli",
    "config_schema",
    "conventional_commit",
    "dispatch",
    "doctor",
    "format_code",
    "installer",
    "large_files",
    "lint",
    "no_fixup",
    "prepare_commit_msg",
    "registry",
    "run_tests",
    "secrets",
    "secrets_push",
]
