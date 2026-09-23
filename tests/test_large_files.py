"""Tests for the large-file threshold check."""

from __future__ import annotations

from pre_commit_hooks.large_files import check_files

CONFIG = {
    "large_files": {
        "max_bytes": 100,
        "warn_bytes": 40,
        "lfs_hint": True,
    }
}


def _write(tmp_path, name, size):
    path = tmp_path / name
    path.write_bytes(b"x" * size)
    return str(path)


def test_file_over_limit_is_blocked(tmp_path):
    path = _write(tmp_path, "big.bin", 200)
    blocked, _warned, max_bytes = check_files([path], CONFIG)
    assert path in [p for p, _ in blocked]
    assert max_bytes == 100


def test_file_under_limit_is_allowed(tmp_path):
    path = _write(tmp_path, "small.bin", 10)
    blocked, warned, _ = check_files([path], CONFIG)
    assert blocked == []
    assert warned == []


def test_file_in_warn_band_warns_but_allows(tmp_path):
    path = _write(tmp_path, "mid.bin", 60)  # between warn (40) and max (100)
    blocked, warned, _ = check_files([path], CONFIG)
    assert blocked == []
    assert path in [p for p, _ in warned]


def test_exact_limit_is_allowed(tmp_path):
    path = _write(tmp_path, "exact.bin", 100)  # not strictly greater than 100
    blocked, _, _ = check_files([path], CONFIG)
    assert blocked == []


def test_env_override_raises_limit(tmp_path, monkeypatch):
    path = _write(tmp_path, "big.bin", 200)
    monkeypatch.setenv("GITHOOKS_MAX_FILE_BYTES", "1000")
    blocked, _, max_bytes = check_files([path], CONFIG)
    assert blocked == []
    assert max_bytes == 1000


def test_missing_file_is_skipped(tmp_path):
    blocked, warned, _ = check_files([str(tmp_path / "nope.bin")], CONFIG)
    assert blocked == []
    assert warned == []
