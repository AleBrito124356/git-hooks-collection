#!/usr/bin/env python3
"""Install the hooks into a repository.

Two delivery paths, one command:

    python install.py                 # interactive, native install into .githooks/
    python install.py --all           # install every hook, no prompts
    python install.py --hooks secrets,branch-protect,no-fixup
    python install.py --mode pre-commit   # generate a .pre-commit-config.yaml
    python install.py --uninstall

Native mode vendors the hook package into ``<repo>/.githooks/`` and points
``core.hooksPath`` at it, so the hooks are version-controlled and shared with
everyone who clones the repo -- no pip install required on their side.

pre-commit mode writes a ``.pre-commit-config.yaml`` that references this project
so it plugs into an existing pre-commit setup.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

SOURCE_DIR = Path(__file__).resolve().parent
MARKER = ".githooks-collection"

# Degrade unencodable glyphs to '?' on legacy consoles instead of crashing.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

# name, git stage, one-line description, pre-commit hook id
CATALOG: List[Tuple[str, str, str, str]] = [
    ("secrets", "pre-commit", "Scan staged changes for secrets", "githooks-secrets"),
    ("large-files", "pre-commit", "Block oversized files, suggest Git LFS", "githooks-large-files"),
    ("branch-protect", "pre-commit", "Block direct commits to main/master", "githooks-branch-protect"),
    ("format", "pre-commit", "Format staged files (ruff/black/prettier)", "githooks-format"),
    ("lint", "pre-commit", "Lint staged files", "githooks-lint"),
    ("conventional-commit", "commit-msg", "Validate Conventional Commits", "githooks-conventional-commit"),
    ("issue-prefix", "prepare-commit-msg", "Prefix message with branch issue id", "githooks-issue-prefix"),
    ("no-fixup", "pre-push", "Block pushing fixup!/squash!/WIP", "githooks-no-fixup"),
    ("tests", "pre-push", "Run a fast test subset before push", "githooks-tests"),
]

STAGE_ORDER = ["pre-commit", "prepare-commit-msg", "commit-msg", "pre-push"]

ALL_NAMES = [c[0] for c in CATALOG]
NAME_TO_STAGE = {c[0]: c[1] for c in CATALOG}
NAME_TO_ID = {c[0]: c[3] for c in CATALOG}


# --------------------------------------------------------------------------- #
# Small colour helpers (kept local so install.py runs without the package)
# --------------------------------------------------------------------------- #
def _c(code: str, text: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"\033[{code}m{text}\033[0m"


def info(msg: str) -> None:
    print(_c("36", "· ") + msg)


def ok(msg: str) -> None:
    print(_c("32", "✓ ") + msg)


def warn(msg: str) -> None:
    print(_c("33", "! ") + msg)


def fail(msg: str) -> None:
    print(_c("31", "✗ ") + msg, file=sys.stderr)


# --------------------------------------------------------------------------- #
# Git helpers
# --------------------------------------------------------------------------- #
def git_root(target: Path) -> Optional[Path]:
    proc = subprocess.run(
        ["git", "-C", str(target), "rev-parse", "--show-toplevel"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0:
        return None
    return Path(proc.stdout.strip())


def git_config(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), "config", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


# --------------------------------------------------------------------------- #
# Config rendering
# --------------------------------------------------------------------------- #
def _render_config(selected: List[str]) -> str:
    def stage_block(stage: str) -> str:
        names = [n for n in selected if NAME_TO_STAGE[n] == stage]
        if not names:
            return f"  {stage}: []\n"
        lines = "\n".join(f"    - {n}" for n in names)
        return f"  {stage}:\n{lines}\n"

    hooks_section = "".join(stage_block(s) for s in STAGE_ORDER)
    return (
        "# git-hooks-collection configuration.\n"
        "# Enable/disable a check by editing the lists under 'hooks:' below.\n"
        "# Thresholds for each check follow. Docs: see the project README.\n"
        "version: 1\n\n"
        "hooks:\n"
        f"{hooks_section}\n"
        "secrets:\n"
        "  max_file_bytes: 1000000       # skip scanning files larger than this\n"
        "  entropy_threshold: 3.2        # generic-assignment gate; lower = stricter\n"
        "  exclude:\n"
        "    - \"*.min.js\"\n"
        "    - \"*.lock\"\n"
        "    - \"package-lock.json\"\n"
        "    - \"poetry.lock\"\n"
        "    - \"yarn.lock\"\n"
        "  allow_regex: []               # matched values are treated as non-secrets\n\n"
        "large_files:\n"
        "  max_bytes: 5242880            # 5 MiB hard limit\n"
        "  warn_bytes: 1048576           # 1 MiB soft warning\n"
        "  lfs_hint: true\n\n"
        "branch_protect:\n"
        "  protected:\n"
        "    - main\n"
        "    - master\n"
        "  allow_env: ALLOW_COMMIT_TO_PROTECTED\n\n"
        "format:\n"
        "  autostage: true               # re-stage files the formatter rewrote\n"
        "  tools:\n"
        "    python:\n"
        "      - ruff format\n"
        "      - black\n"
        "    javascript:\n"
        "      - prettier --write\n\n"
        "lint:\n"
        "  fail_on_error: true\n"
        "  tools:\n"
        "    python:\n"
        "      - ruff check\n"
        "    javascript:\n"
        "      - eslint\n\n"
        "conventional_commit:\n"
        "  types:\n"
        "    - feat\n"
        "    - fix\n"
        "    - docs\n"
        "    - style\n"
        "    - refactor\n"
        "    - perf\n"
        "    - test\n"
        "    - build\n"
        "    - ci\n"
        "    - chore\n"
        "    - revert\n"
        "  max_header_length: 72\n"
        "  min_subject_length: 1\n"
        "  require_scope: false\n"
        "  allow_breaking: true\n\n"
        "issue_prefix:\n"
        "  branch_regex: '([A-Z][A-Z0-9]+-\\d+)'\n"
        "  template: '[{issue}] '\n"
        "  skip_sources:\n"
        "    - merge\n"
        "    - squash\n"
        "    - commit\n\n"
        "tests:\n"
        "  command: ''                   # empty = auto-detect pytest / npm test\n"
        "  timeout_seconds: 120\n"
        "  auto_detect: true\n\n"
        "no_fixup:\n"
        "  block_patterns:\n"
        "    - fixup!\n"
        "    - squash!\n"
        "    - amend!\n"
        "    - WIP\n"
        "    - wip:\n"
    )


def _wrapper_script(stage: str) -> str:
    return (
        "#!/usr/bin/env sh\n"
        f"# Auto-generated by git-hooks-collection install.py for the {stage} stage.\n"
        "# Delegates to the bundled dispatcher; edit .githooks.yaml to change behaviour.\n"
        'here="$(cd "$(dirname "$0")" && pwd)"\n'
        'PY="$(command -v python3 || command -v python)"\n'
        'if [ -z "$PY" ]; then\n'
        '  echo "git-hooks-collection: python not found on PATH" >&2\n'
        "  exit 1\n"
        "fi\n"
        f'exec "$PY" "$here/githooks-run.py" {stage} "$@"\n'
    )


# --------------------------------------------------------------------------- #
# Native install
# --------------------------------------------------------------------------- #
def install_native(root: Path, selected: List[str]) -> int:
    hooks_dir = root / ".githooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    (hooks_dir / MARKER).write_text(
        "Managed by git-hooks-collection install.py. Safe to commit.\n",
        encoding="utf-8",
    )

    # Vendor the package + dispatcher so no pip install is needed downstream.
    pkg_src = SOURCE_DIR / "pre_commit_hooks"
    pkg_dst = hooks_dir / "pre_commit_hooks"
    if pkg_dst.exists():
        shutil.rmtree(pkg_dst)
    shutil.copytree(
        pkg_src,
        pkg_dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copy2(SOURCE_DIR / "githooks-run.py", hooks_dir / "githooks-run.py")

    # Config: never clobber an existing one.
    config_path = root / ".githooks.yaml"
    if config_path.exists():
        info(f"keeping existing {config_path.name} (edit it to change enabled checks)")
    else:
        config_path.write_text(_render_config(selected), encoding="utf-8")
        ok(f"wrote {config_path.name}")

    # One wrapper per stage that has at least one selected check.
    stages = [s for s in STAGE_ORDER if any(NAME_TO_STAGE[n] == s for n in selected)]
    for stage in stages:
        script = hooks_dir / stage
        script.write_text(_wrapper_script(stage), encoding="utf-8")
        try:
            script.chmod(0o755)
        except OSError:
            pass

    result = git_config(root, "core.hooksPath", ".githooks")
    if result.returncode != 0:
        fail(f"could not set core.hooksPath: {result.stderr.strip()}")
        return 1

    ok(f"installed {len(selected)} hook(s) into .githooks/ and set core.hooksPath")
    print()
    info("Enabled checks:")
    for name in selected:
        print(f"    {name:<22} ({NAME_TO_STAGE[name]})")
    print()
    info("Commit the .githooks/ directory so your whole team gets these hooks:")
    print("    git add .githooks .githooks.yaml")
    print('    git commit -m "chore: add shared git hooks"')
    print()
    info("Teammates activate them with a single command after cloning:")
    print("    git config core.hooksPath .githooks")
    return 0


# --------------------------------------------------------------------------- #
# pre-commit framework install
# --------------------------------------------------------------------------- #
def _pre_commit_snippet(selected: List[str]) -> str:
    hook_lines = "\n".join(f"      - id: {NAME_TO_ID[n]}" for n in selected)
    return (
        "repos:\n"
        "  - repo: https://github.com/AleBrito124356/git-hooks-collection\n"
        "    rev: v0.1.0\n"
        "    hooks:\n"
        f"{hook_lines}\n"
    )


def install_pre_commit(root: Path, selected: List[str]) -> int:
    config_path = root / ".pre-commit-config.yaml"
    snippet = _pre_commit_snippet(selected)
    if config_path.exists():
        warn(f"{config_path.name} already exists; not overwriting it.")
        print("\nAdd this block under its 'repos:' key:\n")
        # Drop the leading 'repos:' line when merging into an existing file.
        print("\n".join(snippet.splitlines()[1:]))
    else:
        config_path.write_text(snippet, encoding="utf-8")
        ok(f"wrote {config_path.name}")

    print()
    info("Then install the framework hooks for every stage these use:")
    print("    pipx install pre-commit   # or: pip install pre-commit")
    print("    pre-commit install --hook-type pre-commit \\")
    print("                        --hook-type commit-msg \\")
    print("                        --hook-type prepare-commit-msg \\")
    print("                        --hook-type pre-push")
    return 0


# --------------------------------------------------------------------------- #
# Uninstall
# --------------------------------------------------------------------------- #
def uninstall(root: Path) -> int:
    current = git_config(root, "--get", "core.hooksPath")
    if current.returncode == 0 and current.stdout.strip() == ".githooks":
        git_config(root, "--unset", "core.hooksPath")
        ok("unset core.hooksPath")
    else:
        info("core.hooksPath was not pointing at .githooks; leaving git config alone")

    hooks_dir = root / ".githooks"
    if (hooks_dir / MARKER).is_file():
        shutil.rmtree(hooks_dir)
        ok("removed .githooks/ (generated by this installer)")
    else:
        info("no installer-managed .githooks/ directory to remove")

    info("Left .githooks.yaml in place. Delete it manually if you no longer want it.")
    info("Using the pre-commit framework instead? Run: pre-commit uninstall")
    return 0


# --------------------------------------------------------------------------- #
# Interactive selection
# --------------------------------------------------------------------------- #
def choose_interactively() -> List[str]:
    print("Available hooks (all enabled by default):\n")
    for idx, (name, stage, desc, _id) in enumerate(CATALOG, start=1):
        print(f"  {idx:>2}. {name:<22} {_c('2', stage):<28} {desc}")
    print()
    raw = input(
        "Numbers to DISABLE (comma-separated), or press Enter to keep all: "
    ).strip()
    if not raw:
        return list(ALL_NAMES)
    disabled = set()
    for part in raw.replace(" ", "").split(","):
        if not part:
            continue
        try:
            disabled.add(int(part))
        except ValueError:
            warn(f"ignoring '{part}' (not a number)")
    return [name for i, name in enumerate(ALL_NAMES, start=1) if i not in disabled]


def resolve_selection(args) -> List[str]:
    if args.hooks:
        requested = [h.strip() for h in args.hooks.split(",") if h.strip()]
        unknown = [h for h in requested if h not in ALL_NAMES]
        if unknown:
            fail(f"unknown hook(s): {', '.join(unknown)}")
            fail(f"valid names: {', '.join(ALL_NAMES)}")
            sys.exit(2)
        return requested
    if args.all or args.yes or not sys.stdin.isatty():
        return list(ALL_NAMES)
    return choose_interactively()


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="install.py",
        description="Install git-hooks-collection into a repository.",
    )
    p.add_argument("--mode", choices=["native", "pre-commit"], default="native",
                   help="native installer (default) or generate a .pre-commit-config.yaml")
    p.add_argument("--all", action="store_true", help="install every hook without prompting")
    p.add_argument("--hooks", help="comma-separated hook names to install")
    p.add_argument("--yes", action="store_true", help="assume yes / accept defaults")
    p.add_argument("--target", default=".", help="path inside the target repo (default: cwd)")
    p.add_argument("--uninstall", action="store_true", help="remove the native installation")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    target = Path(args.target).resolve()
    root = git_root(target)
    if root is None:
        fail(f"{target} is not inside a git repository (run 'git init' first)")
        return 2

    if args.uninstall:
        return uninstall(root)

    selected = resolve_selection(args)
    if not selected:
        fail("no hooks selected; nothing to do")
        return 1

    info(f"target repository: {root}")
    if args.mode == "native":
        return install_native(root, selected)
    return install_pre_commit(root, selected)


if __name__ == "__main__":
    raise SystemExit(main())
