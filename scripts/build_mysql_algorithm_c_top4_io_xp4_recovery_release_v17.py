#!/usr/bin/env python3
"""Freeze IO-C v26 with empty unresolved candidate waves skipped."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "config/formal/frozen_mysql_algorithm_c_top4_io_xp4_recovery_20260826_v25"
OUTPUT = ROOT / "config/formal/frozen_mysql_algorithm_c_top4_io_xp4_recovery_20260826_v26"
RUNTIME = ROOT / "scripts/run_mysql_four_scene_algorithm_c_xp4_runtime_v13.py"
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_async_scene_xp4_v19.py"
LAUNCHER = ROOT / "scripts/launch_mysql_algorithm_c_top4_io_empty_wave_recovery_v1.py"
CHECKPOINT_BUILDER = ROOT / "scripts/build_mysql_algorithm_c_top4_io_outer6_checkpoint_v1.py"
CHECKPOINT = ROOT / "history/mysql_four_scene_20260809/v261_to_c_top4_io_outer6_checkpoint_20260826/prepared_binding.json"
QUARANTINE = ROOT / "history/mysql_four_scene_20260809/v262_to_c_top4_io_outer6_quarantine_20260826/replay_quarantine.json"


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
        raise RuntimeError("fresh IO-C v26 release required")
    checkpoint = load(CHECKPOINT)
    quarantine = load(QUARANTINE)
    if (
        checkpoint.get("status") != "PREPARED"
        or checkpoint.get("completed_outer_attempts") != 6
        or quarantine.get("status") != "PASS"
        or quarantine.get("resume_checkpoint_outer_attempt") != 6
    ):
        raise RuntimeError("IO-C v26 recovery artifacts differ")
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
    bindings["outer6_checkpoint_builder"] = binding(CHECKPOINT_BUILDER)
    bindings["outer6_checkpoint"] = binding(CHECKPOINT)
    bindings["outer6_replay_quarantine"] = binding(QUARANTINE)
    bindings["coordination_contract"] = binding(OUTPUT / "coordination_contract.json")
    bindings["optimization_contract"] = binding(OUTPUT / "optimization_contract.json")
    asynchronous.update({
        "empty_unresolved_candidate_physical_wave_skipped": True,
        "candidate_set_unchanged": True,
        "candidate_sampling": False,
        "incumbent_companion_required_for_empty_independent_wave": False,
        "candidate_measurements_replayed": 0,
        "diagnostic_companion_measurements_credited": 0,
        "accepted_or_rejected_state_credit_reused": False,
    })
    atomic(OUTPUT / "algorithm_c_xp4_async_contract.json", asynchronous)
    print(json.dumps({
        "status": "FROZEN", "release": str(OUTPUT),
        "async_contract_sha256": sha(OUTPUT / "algorithm_c_xp4_async_contract.json"),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
