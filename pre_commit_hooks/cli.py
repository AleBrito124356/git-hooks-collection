"""The ``githooks`` command: install, run, list and diagnose the hooks.

    githooks install [--all | --hooks a,b] [--mode native|pre-commit] [--force]
    githooks uninstall
    githooks run <stage|check> [--all-files] [args...]
    githooks list
    githooks doctor
    githooks version

Also available as ``python -m pre_commit_hooks`` and, in a repository that
only has the vendored copy, as ``python .githooks/githooks-run.py <command>``.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__, _core, dispatch, doctor, installer, registry


def _root_or_exit(target: str) -> Path:
    root = installer.git_root(Path(target).resolve())
    if root is None:
        _core.error(f"{target} is not inside a git repository")
        raise SystemExit(2)
    return root


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #
def cmd_run(args: argparse.Namespace) -> int:
    root = _root_or_exit(".")
    cwd = Path.cwd()
    target = args.target
    rest = list(args.args)
    all_files = "--all-files" in rest
    rest = [a for a in rest if a != "--all-files"]
    os.chdir(root)

    def rooted(arg: str) -> str:
        # File arguments are given relative to where the user is; the hooks
        # work from the repository root.
        if arg.startswith("-"):
            return arg
        path = Path(arg)
        full = path if path.is_absolute() else cwd / path
        if full.exists():
            try:
                return full.resolve().relative_to(root.resolve()).as_posix()
            except ValueError:
                return str(full)
        return arg

    rest = [rooted(a) for a in rest]
    if target in registry.STAGES:
        if all_files:
            _core.error("--all-files applies to a single check, not a whole stage")
            return 2
        return dispatch.run_stage(target, rest)
    check = registry.get(target)
    if check is None:
        return dispatch.run_check(target, rest)  # prints the "did you mean"
    if all_files:
        if not check.takes_files:
            _core.error(f"'{target}' does not take files, so --all-files does not apply")
            return 2
        if target == "secrets":
            rest = ["--all-files", *rest]
        elif target == "format":
            _core.error("format rewrites files; run your formatter over the tree directly")
            return 2
        else:
            rest = [p for p in _core.index_entries() if os.path.isfile(p)]
    return dispatch.run_check(target, rest)


# --------------------------------------------------------------------------- #
# list
# --------------------------------------------------------------------------- #
def cmd_list(args: argparse.Namespace) -> int:
    root = _root_or_exit(args.target)
    os.chdir(root)
    config = _core.load_config()
    path = _core.find_config_file()
    enabled = {
        stage: [str(n) for n in _core.cfg_list(config, f"hooks.{stage}")]
        for stage in registry.STAGES
    }
    hooks_dir = root / installer.HOOKS_DIR
    native = (hooks_dir / installer.MARKER).is_file()
    print(f"config: {path if path else 'none (shipped defaults)'}")
    if native:
        print("install: native (.githooks/)")
    print()
    print(f"{'check':<22}{'stage':<20}{'enabled':<9}description")
    for check in registry.CHECKS:
        on = check.name in enabled.get(check.stage, [])
        state = "yes" if on else "-"
        if on and native and not (hooks_dir / check.stage).is_file():
            state = "no hook"
        print(f"{check.name:<22}{check.stage:<20}{state:<9}{check.description}")
    extra = [
        (stage, n) for stage, names in enabled.items() for n in names if registry.get(n) is None
    ]
    for stage, name in extra:
        hint = registry.suggest(name)
        print(
            f"\n! unknown check '{name}' under hooks.{stage}"
            + (f"; did you mean '{hint}'?" if hint else "")
        )
    return 0


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="githooks",
        description="git-hooks-collection: install, run, list and diagnose the hooks.",
    )
    p.add_argument("--version", action="version", version=f"git-hooks-collection {__version__}")
    sub = p.add_subparsers(dest="command", metavar="<command>")

    install = sub.add_parser("install", help="install the hooks (native or pre-commit config)")
    installer.add_install_arguments(install)

    uninstall = sub.add_parser("uninstall", help="remove the native installation")
    uninstall.add_argument("--target", default=".", help="path inside the target repo")

    run = sub.add_parser(
        "run",
        help="run one check or a whole stage now",
        description="Run a check (e.g. secrets) or a stage (e.g. pre-commit) on demand. "
        "Arguments after the name are passed to the check; --all-files feeds every "
        "tracked file to secrets, large-files or lint.",
    )
    run.add_argument("target", help="check or stage name")
    run.add_argument("args", nargs=argparse.REMAINDER, help="files / hook arguments")

    lst = sub.add_parser("list", help="show every check, its stage and whether it is enabled")
    lst.add_argument("--target", default=".", help="path inside the target repo")

    doc = sub.add_parser("doctor", help="diagnose the installation and the config")
    doc.add_argument("--target", default=".", help="path inside the target repo")

    sub.add_parser("version", help="print the version")
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "install":
        args.uninstall = False
        return installer.run_install(args)
    if args.command == "uninstall":
        root = _root_or_exit(args.target)
        return installer.uninstall(root)
    if args.command == "run":
        return cmd_run(args)
    if args.command == "list":
        return cmd_list(args)
    if args.command == "doctor":
        return doctor.run(args.target)
    if args.command == "version":
        print(f"git-hooks-collection {__version__}")
        return 0
    parser.print_help()
    return 0
