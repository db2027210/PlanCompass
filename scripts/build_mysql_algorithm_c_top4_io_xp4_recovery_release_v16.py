#!/usr/bin/env python3
"""Freeze IO-C v25 decision-only recovery after valid companion completion."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "config/formal/frozen_mysql_algorithm_c_top4_io_xp4_recovery_20260826_v24"
OUTPUT = ROOT / "config/formal/frozen_mysql_algorithm_c_top4_io_xp4_recovery_20260826_v25"
RUNTIME = ROOT / "scripts/run_mysql_four_scene_algorithm_c_xp4_runtime_v12.py"
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_async_scene_xp4_v18.py"
LAUNCHER = ROOT / "scripts/launch_mysql_algorithm_c_top4_io_outer6_partial_v3.py"
COMPLETED_STATE = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v253_to_c_top4_io_outer6_partial_20260826_attempt022_xp_io/run_state.json"
)
COMPLETED_BATCH = COMPLETED_STATE.parent / "outer_006/top5_batch_5/batch_result.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def atomic(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def binding(path: Path) -> dict[str, str]:
    return {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError("fresh IO-C v25 release required")
    state = load(COMPLETED_STATE)
    batch = load(COMPLETED_BATCH)
    if (
        state.get("status") != "FAILED_CLOSED"
        or state.get("error") != "KeyError: 'candidate_ranking'"
        or batch.get("status") != "PASS"
        or batch.get("failures") not in ([], None)
    ):
        raise RuntimeError("IO-C completed companion predecessor differs")
    OUTPUT.mkdir(parents=True, mode=0o700)
    for name in (
        "coordination_contract.json", "optimization_contract.json",
        "transition_contract.json",
    ):
        atomic(OUTPUT / name, load(SOURCE / name))
    asynchronous = load(SOURCE / "algorithm_c_xp4_async_contract.json")
    bindings = asynchronous["source_bindings"]
    bindings["runtime"] = binding(RUNTIME)
    bindings["controller"] = binding(CONTROLLER)
    bindings["physical_partial_launcher"] = binding(LAUNCHER)
    bindings["coordination_contract"] = binding(OUTPUT / "coordination_contract.json")
    bindings["optimization_contract"] = binding(OUTPUT / "optimization_contract.json")
    bindings["completed_companion_failure_state"] = binding(COMPLETED_STATE)
    bindings["completed_companion_batch"] = binding(COMPLETED_BATCH)
    asynchronous["completed_companion_measurement_reused"] = True
    asynchronous["candidate_measurements_replayed"] = 0
    asynchronous["accepted_or_rejected_state_credit_reused"] = False
    asynchronous["candidate_ranking_metadata_reconstructed_from_frozen_ranking"] = True
    atomic(OUTPUT / "algorithm_c_xp4_async_contract.json", asynchronous)
    print(json.dumps({
        "status": "FROZEN", "release": str(OUTPUT),
        "async_contract_sha256": sha(OUTPUT / "algorithm_c_xp4_async_contract.json"),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
