"""Shared plumbing for every hook: git access, config, colour output.

Everything here is standard-library only so the hooks stay dependency-light and
copy-into-any-repo portable. The one soft dependency is PyYAML, used only if it
happens to be installed; otherwise ``_miniyaml`` reads the config.
"""

from __future__ import annotations

import math
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

# Never let a status glyph crash a git hook on a legacy (cp1252) console:
# degrade unencodable characters to '?' instead of raising UnicodeEncodeError.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - not all streams support reconfigure
        pass

# --------------------------------------------------------------------------- #
# Defaults. These mirror the shipped .githooks.yaml so the hooks behave sensibly
# even when no config file is present (e.g. a single hook copied into a repo).
# --------------------------------------------------------------------------- #
DEFAULTS: Dict[str, Any] = {
    "version": 1,
    "hooks": {
        "pre-commit": ["secrets", "large-files", "branch-protect", "format", "lint"],
        "commit-msg": ["conventional-commit"],
        "prepare-commit-msg": ["issue-prefix"],
        "pre-push": ["no-fixup", "tests"],
    },
    "secrets": {
        "max_file_bytes": 1_000_000,
        "entropy_threshold": 3.2,
        "exclude": [
            "*.min.js",
            "*.lock",
            "package-lock.json",
            "poetry.lock",
            "yarn.lock",
        ],
        "allow_regex": [],
    },
    "large_files": {
        "max_bytes": 5_242_880,  # 5 MiB
        "warn_bytes": 1_048_576,  # 1 MiB
        "lfs_hint": True,
    },
    "branch_protect": {
        "protected": ["main", "master"],
        "allow_env": "ALLOW_COMMIT_TO_PROTECTED",
    },
    "format": {
        "autostage": True,
        "tools": {
            "python": ["ruff format", "black"],
            "javascript": ["prettier --write"],
        },
    },
    "lint": {
        "fail_on_error": True,
        "tools": {
            "python": ["ruff check"],
            "javascript": ["eslint"],
        },
    },
    "conventional_commit": {
        "types": [
            "feat",
            "fix",
            "docs",
            "style",
            "refactor",
            "perf",
            "test",
            "build",
            "ci",
            "chore",
            "revert",
        ],
        "max_header_length": 72,
        "min_subject_length": 1,
        "require_scope": False,
        "allow_breaking": True,
    },
    "issue_prefix": {
        "branch_regex": r"([A-Z][A-Z0-9]+-\d+)",
        "template": "[{issue}] ",
        "skip_sources": ["merge", "squash", "commit"],
    },
    "tests": {
        "command": "",
        "timeout_seconds": 120,
        "auto_detect": True,
    },
    "no_fixup": {
        "block_patterns": ["fixup!", "squash!", "amend!", "WIP", "wip:"],
    },
}

# Extension -> language bucket, used by format/lint to group staged files.
_EXT_LANG = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "javascript",
    ".tsx": "javascript",
    ".vue": "javascript",
    ".svelte": "javascript",
    ".json": "javascript",
    ".css": "javascript",
    ".scss": "javascript",
    ".html": "javascript",
    ".md": "javascript",
    ".yaml": "javascript",
    ".yml": "javascript",
}


# --------------------------------------------------------------------------- #
# Colour output
# --------------------------------------------------------------------------- #
def _colour_enabled() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("GITHOOKS_FORCE_COLOR"):
        return True
    return sys.stderr.isatty()


class C:
    _on = _colour_enabled()
    RED = "\033[31m" if _on else ""
    GREEN = "\033[32m" if _on else ""
    YELLOW = "\033[33m" if _on else ""
    CYAN = "\033[36m" if _on else ""
    DIM = "\033[2m" if _on else ""
    BOLD = "\033[1m" if _on else ""
    RESET = "\033[0m" if _on else ""


def error(msg: str) -> None:
    print(f"{C.RED}✗{C.RESET} {msg}", file=sys.stderr)


def warn(msg: str) -> None:
    print(f"{C.YELLOW}!{C.RESET} {msg}", file=sys.stderr)


def ok(msg: str) -> None:
    print(f"{C.GREEN}✓{C.RESET} {msg}", file=sys.stderr)


def info(msg: str) -> None:
    print(f"{C.CYAN}·{C.RESET} {msg}", file=sys.stderr)


def header(msg: str) -> None:
    print(f"{C.BOLD}{msg}{C.RESET}", file=sys.stderr)


# --------------------------------------------------------------------------- #
# Git helpers
# --------------------------------------------------------------------------- #
def run(cmd: List[str], text: bool = True, check: bool = False,
        timeout: Optional[int] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        check=check,
        timeout=timeout,
    )


def git(*args: str, check: bool = False) -> str:
    proc = run(["git", *args], check=check)
    return (proc.stdout or "").strip()


def git_bytes(*args: str) -> Optional[bytes]:
    proc = subprocess.run(
        ["git", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


def repo_root() -> Optional[Path]:
    proc = run(["git", "rev-parse", "--show-toplevel"])
    if proc.returncode != 0:
        return None
    out = proc.stdout.strip()
    return Path(out) if out else None


def current_branch() -> str:
    proc = run(["git", "symbolic-ref", "--quiet", "--short", "HEAD"])
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip()
    # Detached HEAD.
    return git("rev-parse", "--short", "HEAD")


def staged_files(diff_filter: str = "ACM") -> List[str]:
    """Paths staged for commit (Added/Copied/Modified by default)."""
    out = git("diff", "--cached", "--name-only", f"--diff-filter={diff_filter}", "-z")
    if not out:
        # -z output has no trailing content when empty; fall back to newline form.
        out = git("diff", "--cached", "--name-only", f"--diff-filter={diff_filter}")
        return [line for line in out.splitlines() if line]
    return [p for p in out.split("\0") if p]


def staged_blob(path: str) -> Optional[bytes]:
    """Contents of a path as staged in the index (not the working tree)."""
    return git_bytes("show", f":{path}")


def staged_size(path: str) -> Optional[int]:
    """Byte size of the staged blob for a path, or None if not in the index."""
    proc = run(["git", "cat-file", "-s", f":{path}"])
    if proc.returncode != 0:
        return None
    try:
        return int(proc.stdout.strip())
    except ValueError:
        return None


def read_staged_or_disk(path: str) -> Optional[bytes]:
    """Prefer the staged blob; fall back to the working-tree file."""
    data = staged_blob(path)
    if data is not None:
        return data
    try:
        return Path(path).read_bytes()
    except OSError:
        return None


def file_size(path: str) -> Optional[int]:
    size = staged_size(path)
    if size is not None:
        return size
    try:
        return Path(path).stat().st_size
    except OSError:
        return None


def language_of(path: str) -> Optional[str]:
    return _EXT_LANG.get(Path(path).suffix.lower())


# --------------------------------------------------------------------------- #
# Content helpers
# --------------------------------------------------------------------------- #
def is_probably_binary(data: bytes) -> bool:
    if b"\0" in data[:8000]:
        return True
    # Heuristic: a high proportion of non-text bytes.
    sample = data[:8000]
    if not sample:
        return False
    text_chars = bytes(range(0x20, 0x7F)) + b"\n\r\t\f\b"
    nontext = sum(1 for b in sample if b not in text_chars)
    return nontext / len(sample) > 0.30


def shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    counts: Dict[str, int] = {}
    for ch in value:
        counts[ch] = counts.get(ch, 0) + 1
    length = len(value)
    return -sum((n / length) * math.log2(n / length) for n in counts.values())


# --------------------------------------------------------------------------- #
# Config loading
# --------------------------------------------------------------------------- #
def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _find_config_file() -> Optional[Path]:
    explicit = os.environ.get("GITHOOKS_CONFIG")
    if explicit:
        p = Path(explicit)
        return p if p.is_file() else None
    root = repo_root()
    candidates = []
    if root is not None:
        candidates.append(root / ".githooks.yaml")
        candidates.append(root / ".githooks.yml")
    candidates.append(Path.cwd() / ".githooks.yaml")
    # When installed via install.py the config lives next to the runner.
    here = Path(__file__).resolve().parent
    candidates.append(here / ".githooks.yaml")
    candidates.append(here.parent / ".githooks.yaml")
    for cand in candidates:
        if cand.is_file():
            return cand
    return None


def _load_yaml(text: str) -> Any:
    try:
        import yaml  # type: ignore

        return yaml.safe_load(text)
    except ImportError:
        from . import _miniyaml

        return _miniyaml.safe_load(text)


_CONFIG_CACHE: Optional[Dict[str, Any]] = None


def load_config(force_reload: bool = False) -> Dict[str, Any]:
    """Return the merged configuration (defaults + .githooks.yaml)."""
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None and not force_reload:
        return _CONFIG_CACHE
    config = deepcopy(DEFAULTS)
    path = _find_config_file()
    if path is not None:
        try:
            data = _load_yaml(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                config = _deep_merge(config, data)
        except Exception as exc:  # noqa: BLE001 - config errors must not crash git
            warn(f"could not read {path.name}: {exc}; using defaults")
    _CONFIG_CACHE = config
    return config


def cfg(config: Dict[str, Any], dotted: str, default: Any = None) -> Any:
    node: Any = config
    for part in dotted.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return default
    return node


def skipped_checks() -> set:
    raw = os.environ.get("GITHOOKS_SKIP", "")
    return {c.strip() for c in raw.split(",") if c.strip()}


def human_size(num: int) -> str:
    step = 1024.0
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(num) < step:
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= step
    return f"{num:.1f} PiB"
