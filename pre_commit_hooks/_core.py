"""Shared plumbing for every hook: git access, config, colour output.

Everything here is standard-library only so the hooks stay dependency-light and
copy-into-any-repo portable. The one soft dependency is PyYAML, used only if it
happens to be installed; otherwise ``_miniyaml`` reads the config.

Git output is always decoded as UTF-8 (git writes paths and diffs as UTF-8 on
every platform), never with the locale codec: on Windows that is cp1252, which
garbles non-ASCII file names and made the hooks silently skip those files.
"""

from __future__ import annotations

import math
import os
import subprocess
import sys
import threading
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

# Never let a status glyph or a non-ASCII file name crash a hook on a legacy
# (cp1252) console: degrade unencodable characters instead of raising. On
# Windows, a stream that is a pipe rather than a console (Git Bash / mintty,
# IDE terminals) expects UTF-8, so write UTF-8 there instead of cp1252.
for _stream in (sys.stdout, sys.stderr):
    try:
        if os.name == "nt" and not _stream.isatty():  # type: ignore[union-attr]
            _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        else:
            _stream.reconfigure(errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError, OSError):  # not every stream supports it
        pass

TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "githooks.yaml"

# Git's null object id, used on pre-push stdin for "no such ref".
ZERO_SHA = "0" * 40

# Index mode of a submodule entry (a commit id, not file content).
GITLINK_MODE = "160000"


def _load_defaults() -> dict[str, Any]:
    """The shipped template is the single source of truth for defaults."""
    from . import _miniyaml

    data = _miniyaml.safe_load(TEMPLATE_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):  # pragma: no cover - the template is under test
        raise TypeError(f"{TEMPLATE_PATH} does not contain a mapping")
    return data


# Defaults mirror the shipped .githooks.yaml so the hooks behave sensibly even
# when no config file is present (e.g. when run through the pre-commit framework).
DEFAULTS: dict[str, Any] = _load_defaults()

# Extension -> language bucket, used by format/lint to group staged files.
# "web" is formatter territory (prettier) and deliberately has no default linter:
# eslint does not understand Markdown, YAML, JSON, CSS or HTML.
_EXT_LANG = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "javascript",
    ".tsx": "javascript",
    ".mts": "javascript",
    ".cts": "javascript",
    ".vue": "javascript",
    ".svelte": "javascript",
    ".json": "web",
    ".css": "web",
    ".scss": "web",
    ".less": "web",
    ".html": "web",
    ".md": "web",
    ".yaml": "web",
    ".yml": "web",
}

LANGUAGES = ("python", "javascript", "web")


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
# Process helpers
# --------------------------------------------------------------------------- #
def _git_env() -> dict[str, str]:
    # File names are handed to git as literal paths, never as glob pathspecs:
    # without this, `git add -- 'a*.py'` would stage every matching file.
    env = dict(os.environ)
    env["GIT_LITERAL_PATHSPECS"] = "1"
    return env


def run(
    cmd: list[str],
    text: bool = True,
    check: bool = False,
    timeout: int | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run a command and capture its output.

    Text is decoded as UTF-8 with replacement, whatever the locale, so a tool
    that prints non-ASCII can never crash a hook with a decode error.
    """
    kwargs: dict[str, Any] = {"encoding": "utf-8", "errors": "replace"} if text else {}
    return subprocess.run(
        cmd,
        capture_output=True,
        check=check,
        timeout=timeout,
        env=env,
        **kwargs,
    )


def git_run(*args: str, input_bytes: bytes | None = None) -> subprocess.CompletedProcess:
    """Run git and return the raw (bytes) CompletedProcess."""
    return subprocess.run(
        ["git", *args],
        input=input_bytes,
        capture_output=True,
        env=_git_env(),
        check=False,
    )


def decode_path(raw: bytes) -> str:
    """Decode a path emitted by git (always UTF-8, even on Windows)."""
    return raw.decode("utf-8", errors="surrogateescape")


def git(*args: str, check: bool = False) -> str:
    proc = git_run(*args)
    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode, ["git", *args], proc.stdout, proc.stderr
        )
    return decode_path(proc.stdout or b"").strip()


def git_ok(*args: str) -> bool:
    return git_run(*args).returncode == 0


def git_bytes(*args: str) -> bytes | None:
    proc = git_run(*args)
    if proc.returncode != 0:
        return None
    return proc.stdout


# --------------------------------------------------------------------------- #
# Per-stage memoisation
# --------------------------------------------------------------------------- #
# Spawning git costs ~50 ms on Windows and every check asks for the same staged
# file list. While the dispatcher runs one stage, read-only git queries are
# answered once and shared; anything that writes the index (the format check's
# `git add`) calls invalidate_memo(). Outside a stage run nothing is cached.
_MEMO: dict[Any, Any] | None = None


@contextmanager
def memoized() -> Iterator[None]:
    global _MEMO
    previous = _MEMO
    _MEMO = {} if previous is None else previous
    try:
        yield
    finally:
        _MEMO = previous


def invalidate_memo() -> None:
    if _MEMO is not None:
        _MEMO.clear()


def _memo(key: Any, compute: Callable[[], Any]) -> Any:
    if _MEMO is None:
        return compute()
    if key not in _MEMO:
        _MEMO[key] = compute()
    return _MEMO[key]


def repo_root() -> Path | None:
    def compute() -> Path | None:
        proc = git_run("rev-parse", "--show-toplevel")
        if proc.returncode != 0:
            return None
        out = decode_path(proc.stdout).strip()
        return Path(out) if out else None

    return _memo(("repo_root", os.getcwd()), compute)


def current_branch() -> str:
    def compute() -> str:
        proc = git_run("symbolic-ref", "--quiet", "--short", "HEAD")
        out = decode_path(proc.stdout).strip()
        if proc.returncode == 0 and out:
            return out
        # Detached HEAD.
        return git("rev-parse", "--short", "HEAD")

    return _memo(("branch", os.getcwd()), compute)


def _split_z(raw: bytes | None) -> list[str]:
    if not raw:
        return []
    return [decode_path(p) for p in raw.split(b"\0") if p]


def staged_files(diff_filter: str = "ACMR") -> list[str]:
    """Paths staged for commit, relative to the repository root.

    Added, Copied, Modified and Renamed by default. Renames matter: a renamed
    file can carry new content (a secret appended after ``git mv``), and leaving
    ``R`` out let it past every content check.
    """

    def compute() -> list[str]:
        raw = git_bytes(
            "diff",
            "--cached",
            "--name-only",
            "-z",
            "--find-renames",
            "--no-ext-diff",
            f"--diff-filter={diff_filter}",
        )
        return _split_z(raw)

    return list(_memo(("staged", os.getcwd(), diff_filter), compute))


def unstaged_files(paths: list[str] | None = None) -> list[str]:
    """Tracked paths whose working-tree content differs from the index."""
    args = ["diff", "--name-only", "-z", "--no-ext-diff"]
    if paths:
        args += ["--", *paths]
    return _split_z(git_bytes(*args))


def index_entries(paths: Iterable[str] | None = None) -> dict[str, tuple[str, str]]:
    """Map path -> (mode, blob sha) for the stage-0 index entries.

    One ``git ls-files`` call instead of one ``git show`` per file. Paths are
    relative to the repository root.
    """
    wanted = list(paths) if paths is not None else None
    key = ("index", os.getcwd(), None if wanted is None else tuple(wanted))
    return dict(_memo(key, lambda: _index_entries(wanted)))


def _index_entries(wanted: list[str] | None) -> dict[str, tuple[str, str]]:
    args = ["ls-files", "-s", "-z", "--full-name"]
    if wanted is not None:
        if not wanted:
            return {}
        # Keep the command line bounded (Windows caps it near 32 KiB).
        if len(wanted) <= 200:
            args += ["--", *wanted]
    raw = git_bytes(*args)
    entries: dict[str, tuple[str, str]] = {}
    if not raw:
        return entries
    for record in raw.split(b"\0"):
        if b"\t" not in record:
            continue
        meta, raw_path = record.split(b"\t", 1)
        parts = meta.split()
        if len(parts) != 3 or parts[2] != b"0":
            continue
        entries[decode_path(raw_path)] = (parts[0].decode(), parts[1].decode())
    if wanted is not None and len(wanted) > 200:
        keep = set(wanted)
        entries = {p: e for p, e in entries.items() if p in keep}
    return entries


def object_sizes(shas: Iterable[str]) -> dict[str, int]:
    """Byte size of each object, from a single ``git cat-file --batch-check``."""
    unique = list(dict.fromkeys(shas))
    if not unique:
        return {}
    proc = git_run(
        "cat-file",
        "--batch-check=%(objectname) %(objectsize)",
        input_bytes=("\n".join(unique) + "\n").encode(),
    )
    sizes: dict[str, int] = {}
    for line in proc.stdout.decode(errors="replace").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            sizes[parts[0]] = int(parts[1])
    return sizes


def iter_blobs(shas: Iterable[str]) -> Iterator[tuple[str, bytes]]:
    """Stream (sha, content) for each object through one ``git cat-file --batch``.

    Objects are read one at a time, so auditing a large tree never holds every
    blob in memory at once.
    """
    unique = list(dict.fromkeys(shas))
    if not unique:
        return
    proc = subprocess.Popen(
        ["git", "cat-file", "--batch"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=_git_env(),
    )
    assert proc.stdin is not None and proc.stdout is not None
    stdin, stdout = proc.stdin, proc.stdout

    def _feed() -> None:
        try:
            for sha in unique:
                stdin.write(sha.encode() + b"\n")
            stdin.close()
        except OSError:  # git exited early (consumer stopped reading)
            pass

    feeder = threading.Thread(target=_feed, daemon=True)
    feeder.start()
    try:
        while True:
            head = stdout.readline()
            if not head:
                break
            parts = head.split()
            if len(parts) != 3:  # "<name> missing"
                continue
            size = int(parts[2])
            content = stdout.read(size)
            stdout.read(1)  # the newline that terminates each object
            yield parts[0].decode(), content
    finally:
        stdout.close()
        proc.wait()
        feeder.join(timeout=5)


def read_staged(paths: list[str]) -> dict[str, bytes]:
    """Content of each path as staged in the index, else the file on disk.

    Paths that are not in the index (or live outside the repository) are read
    from disk, so the helpers also work on arbitrary files given as arguments.
    Submodule entries are skipped.
    """
    entries = index_entries(paths)
    by_sha: dict[str, list[str]] = {}
    for path in paths:
        entry = entries.get(path)
        if entry and entry[0] != GITLINK_MODE:
            by_sha.setdefault(entry[1], []).append(path)
    result: dict[str, bytes] = {}
    for sha, content in iter_blobs(by_sha):
        for path in by_sha.get(sha, []):
            result[path] = content
    for path in paths:
        if path in result or path in entries:
            continue
        try:
            result[path] = Path(path).read_bytes()
        except OSError:
            continue
    return result


def staged_blob(path: str) -> bytes | None:
    """Contents of a path as staged in the index (not the working tree)."""
    entry = index_entries([path]).get(path)
    if entry is None or entry[0] == GITLINK_MODE:
        return None
    for _sha, content in iter_blobs([entry[1]]):
        return content
    return None


def read_staged_or_disk(path: str) -> bytes | None:
    """Prefer the staged blob; fall back to the working-tree file."""
    return read_staged([path]).get(path)


def file_sizes(paths: list[str]) -> dict[str, int]:
    """Staged size of each path (one batch call), else its size on disk."""
    entries = index_entries(paths)
    sizes = object_sizes(e[1] for e in entries.values() if e[0] != GITLINK_MODE)
    result: dict[str, int] = {}
    for path in paths:
        entry = entries.get(path)
        if entry is not None:
            if entry[1] in sizes and entry[0] != GITLINK_MODE:
                result[path] = sizes[entry[1]]
            continue
        try:
            result[path] = Path(path).stat().st_size
        except OSError:
            continue
    return result


def staged_size(path: str) -> int | None:
    """Byte size of the staged blob for a path, or None if not in the index."""
    entry = index_entries([path]).get(path)
    if entry is None:
        return None
    return object_sizes([entry[1]]).get(entry[1])


def file_size(path: str) -> int | None:
    return file_sizes([path]).get(path)


def language_of(path: str) -> str | None:
    return _EXT_LANG.get(Path(path).suffix.lower())


def split_command(command: str) -> list[str]:
    """Split a configured command line, keeping Windows paths intact.

    POSIX ``shlex`` treats backslashes as escapes, which turns
    ``C:\\tools\\ruff.exe`` into ``C:toolsruff.exe``. On Windows, split in
    non-POSIX mode and strip the quotes it leaves around each token.
    """
    import shlex

    if os.name != "nt":
        return shlex.split(command)
    parts = []
    for token in shlex.split(command, posix=False):
        if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
            token = token[1:-1]
        parts.append(token)
    return parts


def find_sh() -> str | None:
    """A POSIX sh to run hook wrappers with: on PATH, or Git for Windows' own."""
    import shutil

    found = shutil.which("sh")
    if found:
        return found
    exec_path = git("--exec-path")
    if exec_path:
        # <git>/mingw64/libexec/git-core -> <git>/bin/sh.exe or <git>/usr/bin/sh.exe
        for base in list(Path(exec_path).parents)[:4]:
            for rel in ("bin/sh.exe", "usr/bin/sh.exe", "bin/sh"):
                if (base / rel).is_file():
                    return str(base / rel)
    return None


def tool_name(executable: str) -> str:
    """Display name of a resolved tool: "eslint", not "eslint.CMD"."""
    path = Path(executable)
    return path.stem if path.suffix.lower() in (".exe", ".cmd", ".bat", ".com") else path.name


def resolve_tool(spec: str) -> list[str] | None:
    """Turn a configured tool spec ("ruff format") into an argv, or None.

    The executable is resolved to its full path with ``shutil.which`` so that
    Windows npm shims (``prettier.cmd``, ``eslint.cmd``) can be launched:
    CreateProcess only finds ``.exe`` files by bare name.
    """
    import shutil

    parts = split_command(spec)
    if not parts:
        return None
    exe = shutil.which(parts[0])
    if not exe:
        return None
    return [exe, *parts[1:]]


# --------------------------------------------------------------------------- #
# Content helpers
# --------------------------------------------------------------------------- #
def is_probably_binary(data: bytes) -> bool:
    """Heuristic binary detection that treats any valid UTF-8 as text.

    The previous heuristic counted every byte above 0x7F as "non-text", so a
    source file with CJK or accented comments was classed as binary and never
    scanned.
    """
    sample = data[:8000]
    if not sample:
        return False
    if b"\0" in sample:
        return True
    try:
        sample.decode("utf-8")
        return False
    except UnicodeDecodeError as exc:
        # A multi-byte character cut at the sample boundary is still text.
        if len(data) > len(sample) and exc.start >= len(sample) - 3:
            try:
                sample[: exc.start].decode("utf-8")
                return False
            except UnicodeDecodeError:
                pass
    text_chars = bytes(range(0x20, 0x7F)) + b"\n\r\t\f\b"
    nontext = sum(1 for b in sample if b not in text_chars)
    return nontext / len(sample) > 0.30


def shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    counts: dict[str, int] = {}
    for ch in value:
        counts[ch] = counts.get(ch, 0) + 1
    length = len(value)
    return -sum((n / length) * math.log2(n / length) for n in counts.values())


# --------------------------------------------------------------------------- #
# Config loading
# --------------------------------------------------------------------------- #
def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def find_config_file() -> Path | None:
    explicit = os.environ.get("GITHOOKS_CONFIG")
    if explicit:
        p = Path(explicit)
        if not p.is_file():
            warn(f"GITHOOKS_CONFIG points at {explicit}, which does not exist; using defaults")
            return None
        return p
    root = repo_root()
    candidates = []
    if root is not None:
        candidates.append(root / ".githooks.yaml")
        candidates.append(root / ".githooks.yml")
    candidates.append(Path.cwd() / ".githooks.yaml")
    for cand in candidates:
        if cand.is_file():
            return cand
    return None


_find_config_file = find_config_file  # backwards-compatible private alias


def yaml_backend() -> str:
    """Which parser reads the config: ``"pyyaml"`` or ``"miniyaml"``.

    ``GITHOOKS_FORCE_MINIYAML=1`` forces the bundled parser even when PyYAML is
    importable, so the no-PyYAML path can be exercised from one environment.
    """
    if os.environ.get("GITHOOKS_FORCE_MINIYAML"):
        return "miniyaml"
    try:
        import yaml  # type: ignore[import-untyped]  # noqa: F401
    except ImportError:
        return "miniyaml"
    return "pyyaml"


def parse_yaml(text: str, backend: str | None = None) -> Any:
    backend = backend or yaml_backend()
    if backend == "pyyaml":
        import yaml  # type: ignore[import-untyped]

        return yaml.safe_load(text)
    from . import _miniyaml

    return _miniyaml.safe_load(text)


_load_yaml = parse_yaml  # backwards-compatible private alias

_CONFIG_CACHE: dict[str, Any] | None = None
_CONFIG_WARNED: set = set()


def _warn_config_problems(path: Path, data: Any) -> None:
    """Point out typos in the config once per process (hooks never fail on them).

    Unknown check names under ``hooks:`` are left to the dispatcher, which
    already says it is skipping them; ``githooks doctor`` reports everything.
    """
    if str(path) in _CONFIG_WARNED:
        return
    _CONFIG_WARNED.add(str(path))
    from . import config_schema

    for problem in config_schema.validate(data, DEFAULTS):
        if not problem.where.startswith("hooks"):
            warn(f"{path.name}: {problem} (run `githooks doctor`)")


def load_config(force_reload: bool = False) -> dict[str, Any]:
    """Return the merged configuration (defaults + .githooks.yaml)."""
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None and not force_reload:
        return _CONFIG_CACHE
    config = deepcopy(DEFAULTS)
    path = find_config_file()
    if path is not None:
        try:
            data = parse_yaml(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                config = _deep_merge(config, data)
            _warn_config_problems(path, data)
        except Exception as exc:  # noqa: BLE001 - config errors must not crash git
            warn(f"could not read {path.name}: {exc}; using defaults")
    _CONFIG_CACHE = config
    return config


def cfg(config: dict[str, Any], dotted: str, default: Any = None) -> Any:
    node: Any = config
    for part in dotted.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return default
    return node


def cfg_list(config: dict[str, Any], dotted: str) -> list[Any]:
    """A list-valued setting; tolerates a missing, null or scalar value."""
    value = cfg(config, dotted, [])
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def skipped_checks() -> set:
    raw = os.environ.get("GITHOOKS_SKIP", "")
    return {c.strip() for c in raw.split(",") if c.strip()}


_SKIP_WARNED: set = set()


def skip_requested(name: str) -> bool:
    """True when GITHOOKS_SKIP names this check; warns once per process.

    Every check calls this from its own ``main``, so the override works the same
    through the native dispatcher, the standalone ``hooks/`` wrappers and the
    pre-commit framework's console scripts.
    """
    if name not in skipped_checks():
        return False
    if name not in _SKIP_WARNED:
        _SKIP_WARNED.add(name)
        warn(f"skipping '{name}' (GITHOOKS_SKIP)")
    return True


def human_size(num: float) -> str:
    step = 1024.0
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(num) < step:
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= step
    return f"{num:.1f} PiB"


# --------------------------------------------------------------------------- #
# Pre-push ranges
# --------------------------------------------------------------------------- #
def commit_exists(rev: str) -> bool:
    return git_ok("cat-file", "-e", f"{rev}^{{commit}}")


def pushed_ranges(stdin_data: str | None) -> list[list[str]]:
    """Revision arguments, one argv list per pushed ref, for a pre-push check.

    Sources, in order:

    1. the pre-commit framework, which does not forward stdin but exports
       ``PRE_COMMIT_FROM_REF`` / ``PRE_COMMIT_TO_REF`` (or, when the push
       includes the root commit, only ``PRE_COMMIT_LOCAL_BRANCH``);
    2. git's native pre-push stdin, one
       ``<local ref> <local sha> <remote ref> <remote sha>`` line per ref;
    3. run by hand: the current branch against its upstream, or everything not
       yet on any remote.

    Every range is a list of separate arguments (``[sha, "--not", "--remotes"]``)
    and never one string: git rejects ``"sha --not --remotes"`` as a revision.
    """
    from_ref = os.environ.get("PRE_COMMIT_FROM_REF") or os.environ.get("PRE_COMMIT_ORIGIN")
    to_ref = os.environ.get("PRE_COMMIT_TO_REF") or os.environ.get("PRE_COMMIT_SOURCE")
    if from_ref and to_ref:
        return [[f"{from_ref}..{to_ref}"]]
    local_branch = os.environ.get("PRE_COMMIT_LOCAL_BRANCH")
    if local_branch and os.environ.get("PRE_COMMIT") and not (stdin_data or "").strip():
        return [[local_branch, "--not", "--remotes"]]

    if stdin_data and stdin_data.strip():
        ranges: list[list[str]] = []
        for line in stdin_data.splitlines():
            parts = line.split()
            if len(parts) < 4:
                continue
            local_sha, remote_sha = parts[1], parts[3]
            if local_sha == ZERO_SHA:
                continue  # branch deletion: nothing is being sent
            if remote_sha != ZERO_SHA and commit_exists(remote_sha):
                ranges.append([f"{remote_sha}..{local_sha}"])
            else:
                # New branch, or a remote tip we have never fetched: everything
                # reachable from the pushed commit that no remote has yet.
                ranges.append([local_sha, "--not", "--remotes"])
        return ranges

    upstream = git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if upstream and commit_exists(upstream):
        return [[f"{upstream}..HEAD"]]
    if commit_exists("HEAD"):
        return [["HEAD", "--not", "--remotes"]]
    return []


def read_hook_stdin() -> str:
    """Read a pre-push hook's stdin, tolerating a closed or interactive stdin."""
    if sys.stdin is None or sys.stdin.isatty():
        return ""
    try:
        return sys.stdin.read()
    except (OSError, ValueError):
        return ""
