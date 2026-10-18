#!/usr/bin/env python3
"""Run one SHA-bound formal batch using exactly three trusted TPC-C points."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import CodeType


SOURCE = Path(
    "/home/anonymous/program/resource_adaptive/tools/run_tpcc_formal_target10_batch_v3.py"
)
EXPECTED_SOURCE_SHA256 = (
    "c6c7bbfbaa957ef31a23a1d5fd326aa959adcd6d1cf0372cffd6d7834afcb035"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _replace_once(source: str, old: str, new: str, label: str) -> str:
    if source.count(old) != 1:
        raise RuntimeError(f"TPC-C v43 formal runner patch mismatch: {label}")
    return source.replace(old, new, 1)


def transformed_code() -> CodeType:
    source = SOURCE.read_text(encoding="utf-8")
    if hashlib.sha256(source.encode("utf-8")).hexdigest() != EXPECTED_SOURCE_SHA256:
        raise RuntimeError("TPC-C v43 formal v3 source SHA mismatch")
    replacements = (
        ("    if len(sys.argv) != 9:\n", "    if len(sys.argv) != 10:\n", "argument count"),
        (
            '            "FORMAL_STATE BATCH_ROOT"\n',
            '            "FORMAL_STATE EXPECTED_FORMAL_STATE_SHA256 BATCH_ROOT"\n',
            "usage",
        ),
        (
            """    formal_state_path = Path(sys.argv[7]).resolve()
    batch_root = Path(sys.argv[8]).resolve()
""",
            """    formal_state_path = Path(sys.argv[7]).resolve()
    expected_formal_state_sha256 = sys.argv[8]
    batch_root = Path(sys.argv[9]).resolve()
""",
            "arguments",
        ),
        (
            """        gate.get(\"passed\") is not True
        or gate.get(\"formal_start_authorized\") is not True
        or gate.get(\"selection_sha256\") != top5_sha
""",
            """        gate.get(\"passed\") is not True
        or gate.get(\"formal_start_authorized\") is not True
        or gate.get(\"selection_sha256\") != top5_sha
        or gate.get(\"expected_formal_state_sha256\")
        != expected_formal_state_sha256
        or gate.get(\"diagnostic_runner_sha256\") != sha256(runner)
        or gate.get(\"baseline_sha256\") != sha256(baseline)
        or gate.get(\"formal_trusted_ranks\") != [1, 2, 3]
        or int(gate.get(\"probe_formal_call_count\", -1)) != 0
""",
            "gate",
        ),
        (
            """    if selection_contract == \"tpcc_robust_exact_plan_regime_trusted3_probe2_v1\":
""",
            """    if (selection.get(\"v43_formal_trusted3_contract\") or {}).get(\"contract\") == \"tpcc_v43_formal_trusted3_only_v1\":
        replay_round = 16
    elif selection_contract == \"tpcc_robust_exact_plan_regime_trusted3_probe2_v1\":
""",
            "selection contract",
        ),
        (
            """    if [item[\"selection_role\"] for item in selection[\"top5\"]] != [
        \"trusted\",
        \"trusted\",
        \"trusted\",
        \"uncertain_probe\",
        \"uncertain_probe\",
    ]:
        raise RuntimeError(\"formal Top-5 role contract mismatch\")
""",
            """    if [item[\"selection_role\"] for item in selection[\"top5\"]] != [
        \"trusted\",
        \"trusted\",
        \"trusted\",
    ]:
        raise RuntimeError(\"formal trusted3 role contract mismatch\")
""",
            "roles",
        ),
        (
            """        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not formal_state_path.exists():
""",
            """        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (
            not formal_state_path.is_file()
            or sha256(formal_state_path) != expected_formal_state_sha256
            or gate.get(\"expected_formal_state_sha256\") != expected_formal_state_sha256
            or gate.get(\"diagnostic_runner_sha256\") != sha256(runner)
            or gate.get(\"baseline_sha256\") != sha256(baseline)
        ):
            raise RuntimeError(\"formal SHA boundary changed before trusted3 workload\")
        if not formal_state_path.exists():
""",
            "lock boundary",
        ),
        ("            and physical == 5\n", "            and physical == 3\n", "physical"),
        ("            and valid == 5\n", "            and valid == 3\n", "valid"),
    )
    for old, new, label in replacements:
        source = _replace_once(source, old, new, label)
    return compile(source, str(SOURCE), "exec")


def load_runtime() -> dict:
    namespace = {
        "__name__": "tpcc_formal_target10_trusted3_v43_runtime",
        "__file__": str(Path(__file__).resolve()),
        "__package__": None,
    }
    exec(transformed_code(), namespace)
    return namespace


def main() -> int:
    return int(load_runtime()["main"]())


if __name__ == "__main__":
    raise SystemExit(main())
