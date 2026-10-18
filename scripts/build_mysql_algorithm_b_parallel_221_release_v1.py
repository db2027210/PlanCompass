#!/usr/bin/env python3
"""Build the immutable Algorithm B parallel 2+2+1 scheduler release."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_b_parallel_221_20260818_v9"
    / "algorithm_b_parallel_221_contract.json"
)
CONTROLLER = (
    ROOT
    / "scripts/run_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v9.py"
)
LAUNCHER = (
    ROOT
    / "scripts/launch_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v9.py"
)
PREDECESSOR = (
    ROOT
    / "scripts/run_mysql_four_scene_algorithm_b_independent_resume_outer_rank3_identity_v7.py"
)
TEST = ROOT / "tests/test_mysql_algorithm_b_parallel_221_resume_v9.py"
FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v18"
    / "four_scene_effective_resource_successor.json"
)
COORDINATION = (
    ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260817_v63"
    / "coordination_contract.json"
)
OUTER1 = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v8_formal_independent_outer_rank3_identity_resume_v63_20260818_attempt001"
)
SCENES = ["job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io"]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def binding(path: Path) -> dict[str, str]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular release input required: {path}")
    return {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=False)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def cpu_set(value: str) -> set[int]:
    result: set[int] = set()
    for part in value.split(","):
        endpoints = [int(item) for item in part.split("-", 1)]
        result.update(
            range(endpoints[0], endpoints[-1] + 1)
        )
    return result


def verify_two_worker_freeze() -> dict[str, Any]:
    value = json.loads(FREEZE.read_text(encoding="utf-8"))
    workers = value.get("workers") or []
    audit: dict[str, Any] = {}
    for scene in SCENES:
        rows = sorted(
            (dict(row) for row in workers if row.get("scene") == scene),
            key=lambda row: row["worker_id"],
        )
        if len(rows) != 2:
            raise RuntimeError(f"{scene} two-worker freeze cardinality differs")
        w1, w2 = rows
        database_disjoint = not (
            cpu_set(w1["database_cpuset"]) & cpu_set(w2["database_cpuset"])
        )
        client_disjoint = not (
            cpu_set(w1["client_cpuset"]) & cpu_set(w2["client_cpuset"])
        )
        equal_limits = all(
            w1[name] == w2[name]
            for name in (
                "cpu_quota_cores", "memory_bytes", "swap_bytes", "pids_max",
                "io_read_bps", "io_write_bps", "database_cpuset_mems",
                "client_cpuset_mems",
            )
        )
        if not database_disjoint or not client_disjoint or not equal_limits:
            raise RuntimeError(f"{scene} frozen parallel lane symmetry differs")
        audit[scene] = {
            "workers": [w1["worker_id"], w2["worker_id"]],
            "database_cpusets": [w1["database_cpuset"], w2["database_cpuset"]],
            "client_cpusets": [w1["client_cpuset"], w2["client_cpuset"]],
            "database_cpuset_disjoint": database_disjoint,
            "client_cpuset_disjoint": client_disjoint,
            "resource_limits_equal": equal_limits,
        }
    return audit


def main() -> int:
    if OUTPUT.parent.exists() or OUTPUT.parent.is_symlink():
        raise RuntimeError(f"fresh release directory required: {OUTPUT.parent}")
    two_worker_audit = verify_two_worker_freeze()
    batches = {
        f"outer1_rank_{rank}": binding(
            OUTER1 / f"outer_001/top5_batch_{rank}/batch_result.json"
        )
        for rank in range(1, 6)
    }
    source_bindings = {
        "controller": binding(CONTROLLER),
        "launcher": binding(LAUNCHER),
        "predecessor_controller": binding(PREDECESSOR),
        "directed_test": binding(TEST),
        "two_worker_freeze": binding(FREEZE),
        "device_scoped_coordination_contract": binding(COORDINATION),
        "outer1_failure_state": binding(OUTER1 / "run_state.json"),
        "outer1_rankings": binding(
            OUTER1 / "outer_001/predicted_rankings.json"
        ),
        **batches,
    }
    atomic(OUTPUT, {
        "schema_version": "mysql_four_scene_algorithm_b_parallel_221_contract_v1",
        "release_version": "v9",
        "status": "FROZEN_RUNTIME_VALIDATED",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "active_scenes": SCENES,
        "candidate_batches": [[1, 2], [3, 4], [5]],
        "candidate_parallelism": 2,
        "lane_sequence": {
            "wave_1": {"w1": 1, "w2": 2},
            "wave_2": {"w1": 3, "w2": 4},
            "wave_3": {"w1": 5, "w2": None},
        },
        "workload_contract": {
            "job": "113 canonical SQL exactly once per candidate",
            "tpch": "SF4 Q1-Q22 exactly once in frozen five lanes per candidate",
            "xp_cpu": "native IQ1-IQ7 uniform and frozen XAP:XTP ratio",
            "xp_io": "native IQ1-IQ7 uniform and frozen XAP:XTP ratio",
            "maximum_candidate_seconds": 900,
            "four_scenes_concurrent_within_each_candidate": True,
            "two_candidates_concurrent_on_disjoint_frozen_lanes": True,
        },
        "isolation": {
            "w1_w2_database_cpuset_disjoint": True,
            "w1_w2_client_cpuset_disjoint": True,
            "per_container_cpu_memory_swap_io_and_pids_readback_required": True,
            "underlying_dynamic_gate_release": "v63",
            "unknown_cross_lane_failure": "fail_both_ranks_closed",
            "two_worker_freeze_audit": two_worker_audit,
        },
        "failure_isolation": {
            "physical_joint_wave_preserved": True,
            "rank_specific_derived_batches": True,
            "known_lane_failure_invalidates_only_that_rank": True,
            "unknown_failure_invalidates_both_ranks": True,
            "cross_rank_measurement_mixing": False,
        },
        "continuation": {
            "outer1_complete_valid_replays_reused": [1, 3, 4, 5],
            "outer1_invalid_timeout_preserved": [2],
            "outer1_accepted_or_rejected_state_reused": False,
            "outer1_failure": (
                "FreshBaselineObjective.score raw argument missing after all replay"
            ),
            "outer1_replay_required_again": False,
        },
        "convergence": {
            "accepted_shrinks_required": 10,
            "maximum_interval_ratio": 1.01,
            "required_consecutive_stable_accepted_rounds": 2,
            "fixed_real_top_k": 5,
        },
        "score_contract": {
            "resource_score_only": True,
            "qps_score_contribution": 0.0,
            "qps_used_for_resource_prediction_gradient_or_score": False,
            "raw_payload_supplied_for_api_compatibility": True,
        },
        "source_bindings": source_bindings,
    })
    print(json.dumps({
        "status": "BUILT",
        "path": str(OUTPUT),
        "sha256": sha(OUTPUT),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
