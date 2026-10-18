#!/usr/bin/env python3
"""Migrate unchanged-scene Algorithm C state to v18 and reset XP/I/O credit."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_outer_checkpoint_v2 as checkpointmod
from scripts import run_mysql_four_scene_algorithm_c_v2 as alg


HISTORY = ROOT / "history/mysql_four_scene_20260809"
SOURCE = HISTORY / "algorithm_c_outer4_checkpoint_v2_optimized_20260812/prepared_binding.json"
FREEZE = ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v18/four_scene_effective_resource_successor.json"
PILOT = HISTORY / "four_scene_v18_incumbent_pilot_nonformal_20260813T1620p0800/incumbent_replay/batch_result.json"
RELEASE = ROOT / "artifacts/mysql_xp_io_v18_calibration_release_20260813/release.json"
OUTPUT = HISTORY / "algorithm_c_v18_transition_checkpoint_20260813T1640p0800/prepared_binding.json"
XP_IO = "hybench_xp_io"


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"JSON object required: {path}")
    return value


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bound(path: Path) -> dict:
    return {"path": str(path.resolve()), "sha256": sha(path)}


def main() -> int:
    if OUTPUT.exists():
        raise SystemExit(f"refusing to replace transition checkpoint: {OUTPUT}")
    source = checkpointmod.verify_checkpoint(SOURCE)
    freeze, pilot, release = load(FREEZE), load(PILOT), load(RELEASE)
    policy = freeze.get("version_transition_credit_policy") or {}
    measurements = pilot.get("measurements") or {}
    xp_measurement = measurements.get("xp_io_w1") or {}
    if (
        freeze.get("schema_version") != "mysql_disk2_four_scene_two_worker_effective_resource_successor_v18"
        or policy.get("job_memory_v15_rounds_may_be_migrated_with_version_provenance") is not True
        or policy.get("tpch_sf4_memory_v15_rounds_may_be_migrated_with_version_provenance") is not True
        or policy.get("hybench_xp_cpu_v15_rounds_may_be_migrated_with_version_provenance") is not True
        or policy.get("hybench_xp_io_pre_v18_rounds_may_be_migrated") is not False
        or policy.get("hybench_xp_io_v18_r0_required") is not True
        or pilot.get("status") != "PASS"
        or set(measurements) != {"job_w1", "tpch_w1", "xp_cpu_w1", "xp_io_w1"}
        or any(row.get("hard_gate_passed") is not True for row in measurements.values())
        or release.get("status") != "RELEASED"
        or release.get("freeze", {}).get("sha256") != sha(FREEZE)
    ):
        raise SystemExit("v18 transition authority differs")
    state = copy.deepcopy(source["reconstructed_state"])
    table = load(
        ROOT / "config/formal/frozen_mysql_operator_physics_experience_tables_20260724_v1/T.hybench_xp_io.operator_physics_v1.json"
    )
    state["accepted_rounds"][XP_IO] = 0
    state["rejected_outer_attempts"][XP_IO] = 0
    state["consecutive_failed_rounds"][XP_IO] = 0
    state["interval_stable_rounds"][XP_IO] = 0
    state["stable_intervals"][XP_IO] = None
    state["converged"][XP_IO] = False
    state["params_by_scene"][XP_IO] = copy.deepcopy(alg.DEFAULTS)
    state["ranges_by_scene"][XP_IO] = copy.deepcopy(table["parameter_space"]["ranges"])
    state["active_by_scene"][XP_IO] = copy.deepcopy(table["parameter_space"]["active_params"])
    state["replayed_param_keys_by_scene"][XP_IO] = []
    # This is diagnostic context for ranking only, never an accepted baseline.
    state["incumbents"][XP_IO] = {
        "db_qps": xp_measurement["db_qps"],
        "p95_ms": xp_measurement["p95_ms"],
        "p99_ms": xp_measurement["p99_ms"],
        "resource_means": xp_measurement["resource_means"],
        "diagnostic_only": True,
        "formal_comparison_prohibited_until_v18_r0": True,
    }
    document = {
        "schema_version": checkpointmod.CONTRACT,
        "status": "PREPARED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "source_run_root": str(PILOT.parent.parent.resolve()),
        "completed_outer_attempts": state["outer_attempt"],
        "workload_started": False,
        "fresh_r0_complete": False,
        "formal_outer_started": False,
        "formal_launch_authorized": False,
        "formal_launch_blocker": "hybench_xp_io_v18_fresh_formal_r0_required",
        "freeze_path": str(FREEZE.resolve()),
        "freeze_sha256": sha(FREEZE),
        "resource_model_contract_path": source["resource_model_contract_path"],
        "resource_model_contract_sha256": source["resource_model_contract_sha256"],
        "reconstructed_state": state,
        "version_transition": {
            "source_freeze_sha256": source["freeze_sha256"],
            "target_freeze_sha256": sha(FREEZE),
            "migrated_accepted_rounds": {
                "job_memory": state["accepted_rounds"]["job_memory"],
                "tpch_sf4_memory": state["accepted_rounds"]["tpch_sf4_memory"],
                "hybench_xp_cpu": state["accepted_rounds"]["hybench_xp_cpu"],
            },
            "reset_scene": XP_IO,
            "reset_accepted_rounds": 0,
            "pre_v18_xp_io_evidence_combination_prohibited": True,
            "pilot_is_diagnostic_only": True,
            "pilot_accepted_rounds_credit": 0,
            "calibration_release": bound(RELEASE),
        },
        "evidence": {
            "source_checkpoint": bound(SOURCE),
            "baseline": bound(PILOT),
            "outers": {},
        },
        "builder_source_path": str(Path(__file__).resolve()),
        "builder_source_sha256": sha(Path(__file__).resolve()),
    }
    document["checkpoint_sha256"] = checkpointmod._canonical_sha256(document)
    OUTPUT.parent.mkdir(parents=True, exist_ok=False)
    OUTPUT.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    checkpointmod.verify_checkpoint(OUTPUT)
    print(json.dumps({"status": "PREPARED", "path": str(OUTPUT), "sha256": sha(OUTPUT), "accepted_rounds": state["accepted_rounds"], "formal_launch_authorized": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
