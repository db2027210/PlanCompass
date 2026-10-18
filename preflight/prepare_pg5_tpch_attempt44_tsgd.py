#!/usr/bin/env python3
"""Build the isolated node04 TPC-H T-SGD config from the audited TPC-C shell."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path


ROOT = Path("/home/anonymous/program/resource_adaptive")
SOURCE = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt8_tsgd_v1/config/"
    "tpcc_experience_table_all_subset_sgd.json"
)
REMOTE_LANE = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpch_node04"
)
ATTEMPT = REMOTE_LANE / "experience_sgd/attempt44_tsgd_v1"
REMOTE_CODE = ATTEMPT / "code"
TARGET = ROOT / "preflight/tpch_attempt44_tsgd_config.json"
TABLE_SOURCE = ROOT / "preflight/tpch_attempt38_post_r0/experience_table_T_tpch_memory_v2.json"
POINT_RUNNER = REMOTE_CODE / "preflight/run_tpch_weighted_point_attempt44.sh"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if TARGET.exists():
        raise SystemExit(f"refuse to overwrite existing config: {TARGET}")
    config = json.loads(SOURCE.read_text(encoding="utf-8"))
    table_artifact = json.loads(TABLE_SOURCE.read_text(encoding="utf-8"))
    active = list(table_artifact["active_params"])
    table = table_artifact["table"]
    formal_source = ROOT / "config/formal/pg5_common_outer_bgallowed_20260722_v1"
    config["scoring"] = json.loads(
        (formal_source / "common_scoring_contract.json").read_text(
            encoding="utf-8"
        )
    )

    config["capability"] = {"cpu": 28.8, "memory": 2.0, "io": 144.0}
    config["vm_resource"] = {
        "vm_name": "",
        "cpu": 28.8,
        "memory_gb": 2.0,
        "read_mb": 64.0,
        "write_mb": 80.0,
        "disk_dev": "nvme2n1",
    }
    config["sensitivity_table"] = table
    gradient = config["optimizer"]["gradient"]
    gradient.update(
        {
            "eta": 0.02,
            "max_eta": 0.02,
            "max_iter": 10,
            "patience": 3,
            "gamma": 0.5,
            "min_eta": 0.0001,
            "active_params": active,
            "target_bottleneck_resources": ["memory"],
            "subset_max_workers": 16,
            "subset_log_dir": str(ATTEMPT / "subset_logs"),
        }
    )
    config["formal_outer_protocol"] = {
        "formal_search_initial_replay_top_k": 5,
        "formal_search_second_replay_top_k": 10,
        "formal_search_max_replay_top_k": 16,
        "formal_search_accepted_target": 10,
        "formal_search_accepted_count_start": 0,
    }
    config["benchmark"] = {
        "workload_cmd": str(POINT_RUNNER),
        "workload_timeout_sec": 780,
        "baseline_only": False,
        "warmup_cmd": "true",
        "warmup_enabled": False,
        "validation_warmup_cmd": "true",
        "validation_warmup_enabled": False,
        "validation_warmup_required": False,
    }
    config["postgres"] = {
        "host": "127.0.0.1",
        "port": 64304,
        "user": "postgres",
        "dbname": "template_fullgrid_tpch_pg5",
    }
    config["logging"]["history_path"] = str(
        ATTEMPT / "history/tpch_optimizer_history.jsonl"
    )
    config["resource_iteration_reports"]["output_dir"] = str(
        ATTEMPT / "resource_reports"
    )
    config["plan_capture"].update(
        {"enabled": True, "limit": 100, "output_dir": str(ATTEMPT / "plan_captures")}
    )
    config["pre_benchmark_recovery"] = {
        "enabled": False,
        "required": False,
        "target_db": "template_fullgrid_tpch_pg5",
        "sql": [],
        "snapshot_restore": {"enabled": False, "required": False},
        "restart_db": {"enabled": False, "tags": []},
        "vm_drop_cache": {"enabled": False, "required": False, "tags": []},
    }
    config["post_benchmark_cleanup"].update(
        {
            "target_db": "template_fullgrid_tpch_pg5",
            "ready_db": "template_fullgrid_tpch_pg5",
            "ready_host": "127.0.0.1",
            "ready_port": "64304",
        }
    )
    metrics = config["metrics"]
    metrics["docker_runtime"]["container"] = "pg5_tpch_s04_20260722"
    retraining = metrics["plan_estimator"]["model_retraining"]
    retraining.update(
        {
            "run_id": "pg5v1_tpch_exp_sgd_attempt44_tsgd_v1",
            "feature_cache_dir": str(ATTEMPT / "features_cache_M1"),
            "output_dir": str(ATTEMPT / "resource_models"),
        }
    )
    for resource in ("cpu", "memory", "io"):
        learned = metrics["plan_estimator"][resource]["learned_model"]
        learned["path"] = str(REMOTE_CODE / f"config/generated/{resource}_resource_models.json")
    final_evaluation = config["final_evaluation"]
    final_evaluation["runtime_source"] = "docker-runtime"
    feedback = final_evaluation["feedback_loop"]
    feedback["target_resources"] = ["memory"]
    target_relative_ranges = config["scoring"][
        "target_relative_resource_target_ranges"
    ]
    feedback["resource_target_ranges"] = {
        resource_name: {
            **copy.deepcopy(
                target_relative_ranges[
                    "target_bottleneck"
                    if resource_name == "memory"
                    else "non_bottleneck"
                ]
            ),
            "target_pct": 87.5 if resource_name == "memory" else 40.0,
        }
        for resource_name in ("cpu", "memory", "io")
    }
    feedback["target_util_pct"] = 87.5
    feedback["tolerance_pct"] = 2.5
    feedback["overload_util_pct"] = 95.0
    feedback["hard_overload_util_pct"] = 95.0
    feedback["non_target_target_util_pct"] = 40.0
    feedback["non_target_overload_util_pct"] = 90.0
    feedback["resource_iteration_goal"]["bottleneck_resource"] = "memory"
    config["baseline_resource_band_gate"].update(
        {
            "target_resource": "memory",
            "ranges_pct": {
                "cpu": {"min_pct": 30.0, "max_pct": 50.0},
                "memory": {"min_pct": 85.0, "max_pct": 95.0},
                "io": {"min_pct": 30.0, "max_pct": 50.0},
            },
        }
    )
    config["experience_table_contract"] = {
        "required": True,
        "disable_fallback": True,
        "fallback_enabled": False,
        "artifact_path": str(REMOTE_CODE / "config/formal/experience_table_T_tpch_memory_v2.json"),
        "artifact_sha256": sha256(TABLE_SOURCE),
        "table_id": table_artifact["table_id"],
        "version": table_artifact["version"],
        "table_sha256": table_artifact["table_sha256"],
        "active_params": active,
        "finite_difference_direction_enabled": False,
        "random_direction_enabled": False,
    }
    contract_files = {
        "scoring_contract": "common_scoring_contract.json",
        "r0_resource_gate_contract": "r0_resource_gate_contract.json",
        "stability_gate_contract": "stability_gate_contract_v5.json",
        "io_utilization_contract": "io_utilization_contract.json",
    }
    for config_key, filename in contract_files.items():
        local_contract = formal_source / filename
        config[config_key] = {
            "path": str(REMOTE_CODE / f"config/formal/{filename}"),
            "sha256": sha256(local_contract),
        }
    config["_campaign"] = {
        "campaign_id": "pg5_common_outer_bgallowed_20260722_v1",
        "lane_id": "tpch_node04_nvme2",
        "method": "experience_table_all_subset_sgd",
        "attempt_tag": "attempt44_tsgd_v1",
        "background_load_allowed": True,
        "shared_baseline": str(REMOTE_LANE / "baseline_r0_attempt_43/point/summary.json"),
        "candidate_protocol": {
            "warmup_seconds": 60,
            "measurement_seconds_min": 180,
            "measurement_seconds_max": 600,
            "decision_step_seconds": 30,
            "stability_fields": ["memory_utilization"],
        },
    }
    stability_contract = (
        ROOT
        / "config/formal/pg5_common_outer_bgallowed_20260722_v1/"
        "stability_gate_contract_v5.json"
    )
    candidate_wrapper = ROOT / "shell/run_pg_weighted_sql_baseline.py"
    config["_candidate_measurement_protocol"] = {
        "wrapper": str(REMOTE_CODE / "shell/run_pg_weighted_sql_baseline.py"),
        "wrapper_sha256": sha256(candidate_wrapper),
        "measurement_evidence_root": str(ATTEMPT / "candidate_measurements"),
        "warmup_is_outside_db_and_resource_measurement": True,
        "warmup_seconds": 60,
        "measurement_seconds_min": 180,
        "initial_probe_seconds": 180,
        "measurement_seconds_max": 600,
        "decision_step_seconds": 30,
        "window_seconds": 30,
        "online_last_n_windows": 6,
        "stability": json.loads(stability_contract.read_text(encoding="utf-8")),
        "continue_after_probe_when_unstable": True,
    }
    config["_physical_resource_contract"] = {
        "container": "pg5_tpch_s04_20260722",
        "cpu_quota_cores": 28.8,
        "memory_bytes": 2147483648,
        "io_device": "/dev/nvme2n1",
        "io_read_bps": 67108864,
        "io_write_bps": 83886080,
        "io_total_bps": 150994944,
        "io_semantics": "(read_bps+write_bps)/(io_read_bps+io_write_bps)",
    }
    config["_tpch_attempt44_tsgd_contract"] = {
        "algorithm": "experience_table_subset_sgd_v1",
        "old_grid_results_are_audit_only": True,
        "accepted_shrink_count_start": 0,
        "accepted_shrink_target": 10,
        "subset_count": 127,
        "subset_parallelism": 16,
        "real_replay": "complete_first5_then_expand_to10_then_at_most16",
        "r0_summary": str(REMOTE_LANE / "baseline_r0_attempt_43/point/summary.json"),
    }
    TARGET.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(TARGET, flush=True)
    print(sha256(TARGET), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
