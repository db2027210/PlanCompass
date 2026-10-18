#!/usr/bin/env python3
"""Run formal TPC-C v3 with a lock-scoped formal-state SHA boundary."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import CodeType
from typing import Any, Mapping


SOURCE = Path(
    "/home/anonymous/program/resource_adaptive/"
    "tools/run_tpcc_formal_target10_batch_v3.py"
)
EXPECTED_SOURCE_SHA256 = (
    "c6c7bbfbaa957ef31a23a1d5fd326aa959adcd6d1cf0372cffd6d7834afcb035"
)

OLD_ARG_COUNT = """    if len(sys.argv) != 9:
"""
NEW_ARG_COUNT = """    if len(sys.argv) != 10:
"""
OLD_USAGE = """            \"FORMAL_STATE BATCH_ROOT\"
"""
NEW_USAGE = """            \"FORMAL_STATE EXPECTED_FORMAL_STATE_SHA256 BATCH_ROOT\"
"""
OLD_ARGUMENTS = """    formal_state_path = Path(sys.argv[7]).resolve()
    batch_root = Path(sys.argv[8]).resolve()
"""
NEW_ARGUMENTS = """    formal_state_path = Path(sys.argv[7]).resolve()
    expected_formal_state_sha256 = sys.argv[8]
    batch_root = Path(sys.argv[9]).resolve()
"""
OLD_GATE = """        gate.get(\"passed\") is not True
        or gate.get(\"formal_start_authorized\") is not True
        or gate.get(\"selection_sha256\") != top5_sha
"""
NEW_GATE = """        gate.get(\"passed\") is not True
        or gate.get(\"formal_start_authorized\") is not True
        or gate.get(\"selection_sha256\") != top5_sha
        or gate.get(\"expected_formal_state_sha256\")
        != expected_formal_state_sha256
        or gate.get(\"diagnostic_runner_sha256\") != sha256(runner)
        or gate.get(\"baseline_sha256\") != sha256(baseline)
"""
OLD_LOCK = """        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not formal_state_path.exists():
"""
NEW_LOCK = """        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert_formal_state_sha_locked(
            formal_state_path,
            expected_formal_state_sha256,
            gate,
        )
        if gate.get(\"diagnostic_runner_sha256\") != sha256(runner):
            raise RuntimeError(\"diagnostic runner SHA changed before formal workload\")
        if gate.get(\"baseline_sha256\") != sha256(baseline):
            raise RuntimeError(\"baseline SHA changed before formal workload\")
        if not formal_state_path.exists():
"""


def sha256(path: Path) -> str:
    """Return the SHA-256 digest for one file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def assert_formal_state_sha_locked(
    formal_state_path: Path,
    expected_formal_state_sha256: str,
    gate: Mapping[str, Any],
) -> None:
    """Fail closed when the lock-protected formal authority has changed."""
    if (
        len(expected_formal_state_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_formal_state_sha256)
        or gate.get("expected_formal_state_sha256")
        != expected_formal_state_sha256
        or not formal_state_path.is_file()
        or sha256(formal_state_path) != expected_formal_state_sha256
    ):
        raise RuntimeError(
            "formal state SHA changed after diagnostic gate; refusing formal workload"
        )


def _replace_once(source: str, old: str, new: str, label: str) -> str:
    if source.count(old) != 1:
        raise RuntimeError(f"TPC-C v4 patch anchor mismatch: {label}")
    return source.replace(old, new, 1)


def transformed_code() -> CodeType:
    """Return the SHA-pinned v3 program with the v4 boundary injected."""
    source = SOURCE.read_text(encoding="utf-8")
    if hashlib.sha256(source.encode("utf-8")).hexdigest() != EXPECTED_SOURCE_SHA256:
        raise RuntimeError("TPC-C formal v3 source SHA mismatch")
    for old, new, label in (
        (OLD_ARG_COUNT, NEW_ARG_COUNT, "argument count"),
        (OLD_USAGE, NEW_USAGE, "usage"),
        (OLD_ARGUMENTS, NEW_ARGUMENTS, "arguments"),
        (OLD_GATE, NEW_GATE, "gate"),
        (OLD_LOCK, NEW_LOCK, "lock boundary"),
    ):
        source = _replace_once(source, old, new, label)
    return compile(source, str(SOURCE), "exec")


def load_runtime() -> dict[str, Any]:
    """Load the sealed v4 runtime without running its command-line entry."""
    namespace: dict[str, Any] = {
        "__name__": "tpcc_formal_target10_v4_sha_bound_runtime",
        "__file__": str(Path(__file__).resolve()),
        "__package__": None,
        "assert_formal_state_sha_locked": assert_formal_state_sha_locked,
    }
    exec(transformed_code(), namespace)
    return namespace


def main() -> int:
    return int(load_runtime()["main"]())


if __name__ == "__main__":
    raise SystemExit(main())
