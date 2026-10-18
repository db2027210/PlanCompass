#!/usr/bin/env python3
"""Publish formal partial-outer continuation runtime v13 and coordination v20."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OLD_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260814_v12/optimization_contract.json"
NEW_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260814_v13/optimization_contract.json"
OLD_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260814_v19/coordination_contract.json"
NEW_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260814_v20/coordination_contract.json"
SOURCE = ROOT / "history/mysql_four_scene_20260809/v18_formal_bounded_prestate_v12_20260814T1023p0800_algorithm_c_v2"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def rebind(bindings: dict) -> None:
    for key, value in list(bindings.items()):
        if key.endswith("_path") and key[:-5] + "_sha256" in bindings:
            bindings[key[:-5] + "_sha256"] = sha((ROOT / str(value)).resolve())


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=False)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def main() -> int:
    optimization = json.loads(OLD_OPT.read_text(encoding="utf-8"))
    optimization["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v13"
    optimization["predecessor"] = {"path": rel(OLD_OPT), "sha256": sha(OLD_OPT)}
    optimization["successor_reason"] = "formal_rank5_continuation_after_external_workload_gate_abort"
    optimization["formal_partial_outer_continuation"] = {
        "outer_attempt": 5,
        "completed_ranks_reused": [1, 2, 3, 4],
        "first_new_physical_rank": 5,
        "source_evidence_sha256_required": True,
        "source_baseline_and_rankings_reused_without_recomputation": True,
        "candidate_state_mutation_before_continuation": False,
        "accepted_state_credit_before_continuation": 0,
        "all_five_ranks_required_before_decision": True,
        "external_workload_gate_remains_fail_closed": True,
        "workload_or_measurement_semantics_changed": False,
    }
    paths = {
        "formal_partial_source_state": SOURCE / "run_state.json",
        "formal_partial_baseline": SOURCE / "formal_incumbent_baseline/batch_result.json",
        "formal_partial_rankings": SOURCE / "outer_005/predicted_rankings.json",
        "formal_partial_blocked_gate": SOURCE / "outer_005/top5_batch_5_cross_project_wave_quiet_gate_attempt_001.json",
        **{
            f"formal_partial_batch_{rank}": SOURCE / f"outer_005/top5_batch_{rank}/batch_result.json"
            for rank in range(1, 5)
        },
    }
    bindings = optimization["bindings"]
    for label, path in paths.items():
        bindings[f"{label}_path"] = rel(path)
        bindings[f"{label}_sha256"] = sha(path)
    rebind(bindings)
    write(NEW_OPT, optimization)

    coordination = json.loads(OLD_COORD.read_text(encoding="utf-8"))
    coordination["predecessor"] = {"path": rel(OLD_COORD), "sha256": sha(OLD_COORD)}
    coordination["successor_reason"] = "bind_formal_partial_outer_continuation_runtime_v13"
    rebind(coordination["source_bindings"])
    write(NEW_COORD, coordination)
    print(json.dumps({
        "optimization_sha256": sha(NEW_OPT),
        "coordination_sha256": sha(NEW_COORD),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
