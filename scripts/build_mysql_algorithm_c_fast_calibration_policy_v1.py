#!/usr/bin/env python3
"""Freeze the first-valid-data calibration and release-reuse policy."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_c_fast_calibration_20260812_v1"
    / "calibration_release_policy.json"
)
RESOURCE_FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260811_v15"
    / "four_scene_effective_resource_successor.json"
)
JOB_TPCH_WORKLOAD = (
    ROOT / "config/formal/frozen_mysql_four_scene_job_schedule_20260810_v3"
    / "workload_successor.json"
)
XP_WORKLOAD = (
    ROOT / "config/formal/frozen_mysql_four_scene_native_xp_iq1_iq7_20260809_v2"
    / "workload_successor.json"
)
MODEL_CONTRACT = (
    ROOT / "config/formal/frozen_mysql_algorithm_c_contextual_plan_physics_20260809_v2"
    / "model_contract.json"
)
MODEL_SOURCE = ROOT / "mysql_algorithm_c_resource_model.py"
ALGORITHM_SOURCE = ROOT / "scripts/run_mysql_four_scene_algorithm_c_v2.py"

SCENES = ["job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io"]
OPTIMIZER_PARAMETERS = [
    "io_block_read_cost",
    "memory_block_read_cost",
    "row_evaluate_cost",
    "key_compare_cost",
    "disk_temptable_create_cost",
    "disk_temptable_row_cost",
    "memory_temptable_create_cost",
    "memory_temptable_row_cost",
]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def relative(path: Path) -> str:
    return str(path.relative_to(ROOT))


def canonical_sha(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def binding(path: Path) -> dict[str, str]:
    return {"path": relative(path), "sha256": sha(path)}


def main() -> int:
    for path in (
        RESOURCE_FREEZE,
        JOB_TPCH_WORKLOAD,
        XP_WORKLOAD,
        MODEL_CONTRACT,
        MODEL_SOURCE,
        ALGORITHM_SOURCE,
    ):
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"regular frozen input required: {path}")
    if OUTPUT.exists():
        raise RuntimeError(f"refusing to replace frozen output: {OUTPUT}")

    resource = load(RESOURCE_FREEZE)
    job_tpch = load(JOB_TPCH_WORKLOAD)
    xp = load(XP_WORKLOAD)
    model = load(MODEL_CONTRACT)
    workers = resource.get("workers") or []
    if (
        resource.get("status") != "FROZEN_RUNTIME_READBACK_PENDING"
        or resource.get("formal_scope") != "mysql_only"
        or resource.get("cross_engine_comparison_prohibited") is not True
        or resource.get("active_scenes") != SCENES
        or len(workers) != 8
        or job_tpch.get("status") != "FROZEN"
        or job_tpch.get("job", {}).get("required_count") != 113
        or job_tpch.get("tpch", {}).get("scale_factor") != 4
        or job_tpch.get("tpch", {}).get("executions_per_measurement") != 1
        or xp.get("status") != "FROZEN"
        or xp.get("active_scenes") != SCENES
        or xp.get("native_hybench_xp", {}).get("iq_selection")
        != "uniform_random_IQ1_IQ7"
        or xp.get("native_hybench_xp", {}).get("at_mix_percent")
        != [35, 25, 15, 15, 7, 3]
        or model.get("status") != "FROZEN"
        or model.get("schema_version")
        != "mysql_algorithm_c_contextual_plan_physics_contract_v2"
        or model.get("model", {}).get("sha256") != sha(MODEL_SOURCE)
    ):
        raise RuntimeError("fast-calibration policy inputs differ")

    resource_signature = [
        {
            key: worker[key]
            for key in (
                "worker_id",
                "scene",
                "database_cpuset",
                "database_cpuset_mems",
                "client_cpuset",
                "client_cpuset_mems",
                "cpu_quota_cores",
                "memory_bytes",
                "swap_bytes",
                "io_read_bps",
                "io_write_bps",
                "pids_max",
                "innodb_buffer_pool_bytes",
            )
        }
        for worker in workers
    ]
    compatibility_payload = {
        "formal_scope": "mysql_only",
        "active_scenes": SCENES,
        "workload_contracts": {
            "job_tpch": binding(JOB_TPCH_WORKLOAD),
            "native_xp": binding(XP_WORKLOAD),
        },
        "workload_identity": {
            "job": {"database": "imdb_job", "sql_count": 113, "concurrency": 16},
            "tpch": {
                "database": "tpch",
                "scale_factor": 4,
                "sql_count": 22,
                "lanes": 5,
            },
            "hybench_xp_cpu": {
                "database": "hybench_sf10x",
                "iq": "IQ1-IQ7_uniform_random",
                "at_mix": "35/25/15/15/7/3",
                "xap_xtp_ratio": "1:1",
                "clients": 8,
            },
            "hybench_xp_io": {
                "database": "hybench_sf10x",
                "iq": "IQ1-IQ7_uniform_random",
                "at_mix": "35/25/15/15/7/3",
                "xap_xtp_ratio": "1:1",
                "clients": 8,
            },
        },
        "data_snapshot_identity": {
            "kind": "sealed_physical_copy_predecessor_binding",
            "binding_sha256": "94add246e20bf796f8a2605476c1a900e05c352474c45864f784c329001d7dc8",
            "databases": ["imdb_job", "tpch", "hybench_sf10x"],
        },
        "resource_envelope": resource_signature,
        "resource_freeze": binding(RESOURCE_FREEZE),
        "plan_feature_model": {
            "contract": binding(MODEL_CONTRACT),
            "source": binding(MODEL_SOURCE),
            "physical_identity_excludes_planner_cost_numbers": True,
        },
        "optimizer_parameters": OPTIMIZER_PARAMETERS,
        "optimizer_parameter_count": len(OPTIMIZER_PARAMETERS),
    }

    document = {
        "schema_version": "mysql_algorithm_c_fast_calibration_release_policy_v1",
        "status": "FROZEN",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "authority": "user_first_valid_data_soft_rule_2026-08-12",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "active_scenes": SCENES,
        "purpose": "minimize fresh calibration while preserving first-version validity",
        "release_reuse": {
            "decision": "reuse_only_on_exact_compatibility_and_plan_fingerprint_pass",
            "compatibility_payload": compatibility_payload,
            "compatibility_signature_sha256": canonical_sha(compatibility_payload),
            "all_compatibility_fields_are_hard_exact_match": True,
            "mismatch_action": "fresh_calibration_7_train_plus_3_holdout",
            "baseline_controls_still_required_per_run": True,
        },
        "plan_fingerprint_recheck": {
            "frequency": "once_before_release_reuse",
            "execution": "offline_or_read_only_full_EXPLAIN",
            "coverage": {
                "job": "all_113_SQL",
                "tpch_sf4": "Q1-Q22",
                "native_xp": "IQ1-IQ7_and_AT1-AT6_plan_bearing_statements",
            },
            "physical_identity_model": "planner_cost_independent_v2",
            "no_benchmark_replay_required_for_fingerprint": True,
            "mismatch_action": "fresh_calibration_7_train_plus_3_holdout",
        },
        "fresh_calibration": {
            "training_points": 7,
            "training_design_must_be_full_rank": True,
            "independent_holdout_points": 3,
            "total_candidate_points": 10,
            "parallel_candidate_points_per_wave": 2,
            "candidate_waves": 5,
            "adaptive_expansion_before_first_top5": False,
            "real_replay_required": True,
            "release_may_be_published_only_after_all_hard_gates_pass": True,
        },
        "hard_gates": {
            "full_workload_and_timeout": {
                "job": "113/113 exactly once and <=900 seconds",
                "tpch_sf4": "22/22 exactly once in five lanes and <=900 seconds",
                "native_xp": "native frozen workload complete within contract",
            },
            "technical_failure_isolation": True,
            "target_resource_must_be_identifiable": True,
            "non_target_resource_must_not_become_new_bottleneck": True,
            "source_and_plan_evidence_complete": True,
            "ssd_pollution_gate": True,
            "dynamic_non_interference_gate": "required_by_mysql_only_project_policy",
        },
        "diagnostic_only_before_first_real_top5": {
            "spearman": "record_and_downweight_ranking_only",
            "mae": "record_and_downweight_ranking_only",
            "confidence": "record_and_downweight_ranking_only",
            "may_block_first_formal_replay": False,
            "recalibrate_after_real_top5": True,
        },
        "current_contextual_model": {
            "role": "soft_ranking_prior_not_a_same_workload_calibration_release",
            "empirical_replays": model.get("empirical_replay_basis", {}).get(
                "effective_replays"
            ),
            "legacy_tpch_sf5_evidence_present": True,
            "may_skip_full_workload_replay": False,
            "may_skip_hard_gates": False,
            "confidence_adjusted_soft_score": True,
            "operator_count_hard_pruning": False,
        },
        "implementation_bindings": {
            "algorithm_source": binding(ALGORITHM_SOURCE),
            "current_model_contract": binding(MODEL_CONTRACT),
            "current_model_source": binding(MODEL_SOURCE),
        },
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=False)
    OUTPUT.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii"
    )
    print(OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
