"""git-hooks-collection: a curated set of git hooks with a one-command installer.

The modules in this package each expose a ``main(argv=None)`` entry point and are
wired up both as console scripts (see pyproject.toml) for the pre-commit
framework and as in-process callables for the native dispatcher (githooks-run.py).
"""

__version__ = "0.1.0"

__all__ = [
    "secrets",
    "format_code",
    "lint",
    "large_files",
    "branch_protect",
    "conventional_commit",
    "run_tests",
    "no_fixup",
    "prepare_commit_msg",
]
