#!/usr/bin/env python3
"""Create an isolated JOB outer attempt using the v2 online experience table."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil


LANE = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/"
    "pg5_common_outer_bgallowed_20260722_v1/job_node02"
)
ATTEMPT1 = LANE / "history/job/outer/attempt_1"
ATTEMPT2 = LANE / "history/job/outer/attempt_2_online_t_v2"
WORKSPACE1 = LANE / "workspace_outer"
WORKSPACE2 = LANE / "workspace_outer_attempt2_online_t_v2"
SOURCE_CONFIG_ROOT = ATTEMPT1 / "generated_config/formal_job_outer_attempt1"
CONFIG_ROOT = ATTEMPT2 / "generated_config/formal_job_outer_attempt2_online_t_v2"
CONFIG = CONFIG_ROOT / "memory/job.json"
TABLE = (
    WORKSPACE2
    / "config/formal/pg5_common_outer_bgallowed_20260722_v1/"
    "experience_table_T_job_memory_v2.json"
)
STAGED_TABLE = (
    LANE
    / "launch_outer_attempt2_online_t_v2/experience_table_T_job_memory_v2.json"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if ATTEMPT2.exists() or WORKSPACE2.exists():
        raise RuntimeError(
            f"refusing to overwrite attempt2 artifacts: {ATTEMPT2}, {WORKSPACE2}"
        )
    source_config = SOURCE_CONFIG_ROOT / "memory/job.json"
    for required in (
        source_config,
        WORKSPACE1,
        ATTEMPT1 / "fresh_restart_r0/latest.json",
        STAGED_TABLE,
    ):
        if not required.exists():
            raise RuntimeError(f"required attempt1 input is missing: {required}")

    shutil.copytree(WORKSPACE1, WORKSPACE2, symlinks=True)
    TABLE.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(STAGED_TABLE, TABLE)
    CONFIG_ROOT.parent.mkdir(parents=True, exist_ok=False)
    shutil.copytree(SOURCE_CONFIG_ROOT, CONFIG_ROOT, symlinks=True)
    (ATTEMPT2 / "fresh_restart_r0").mkdir(parents=True)
    shutil.copy2(
        ATTEMPT1 / "fresh_restart_r0/latest.json",
        ATTEMPT2 / "fresh_restart_r0/latest.json",
    )

    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    attempt2_text = str(ATTEMPT2)
    workspace2_text = str(WORKSPACE2)
    generated_root_text = str(CONFIG_ROOT)

    config["_campaign"]["outer_attempt"] = 2
    generated = config["_generated_from"]
    generated["generated_at"] = "2026-07-22T00:00:00+08:00"
    generated["run_id"] = "formal_job_outer_attempt2_online_t_v2"
    generated["script"] = generated["script"].replace(
        str(WORKSPACE1), workspace2_text
    )
    generated["workload_log_path"] = (
        f"{attempt2_text}/generated_output/"
        "formal_job_outer_attempt2_online_t_v2/memory/job.log"
    )

    config["logging"]["history_path"] = f"{attempt2_text}/optimizer_history.jsonl"
    config["auto_explain"]["output_dir"] = (
        f"{attempt2_text}/generated_output/"
        "formal_job_outer_attempt2_online_t_v2/memory/job_auto_explain"
    )
    config["plan_capture"]["output_dir"] = f"{attempt2_text}/plan_captures"
    config["resource_iteration_reports"]["output_dir"] = (
        f"{attempt2_text}/resource_reports"
    )

    protocol = config["formal_outer_protocol"]
    protocol.update(
        {
            "global_replay_counter_path": f"{attempt2_text}/replay_counter.txt",
            "global_replay_ordinal_at_binding": 25,
            "formal_search_replay_counter_path": (
                f"{attempt2_text}/formal_search_replay_counter.txt"
            ),
            "formal_search_replay_count_start": 0,
            "formal_search_accepted_counter_path": (
                f"{attempt2_text}/formal_search_accepted_counter.txt"
            ),
            "formal_search_accepted_count_start": 0,
            "formal_startup_baseline_counter_path": (
                f"{attempt2_text}/formal_startup_baseline_counter.txt"
            ),
            "formal_startup_baseline_count_start": 1,
            "formal_search_initial_replay_top_k": 5,
            "formal_search_max_replay_top_k": 10,
            "formal_search_maximum_replay_count": 10,
            "maximum_cumulative_real_replay_count": 10,
            "maximum_non_default_real_candidates": 10,
            "formal_search_accepted_target": 10,
            "accepted_shrink_target": 10,
        }
    )

    benchmark = config["benchmark"]
    benchmark["warmup_cmd"] = benchmark["warmup_cmd"].replace(
        str(WORKSPACE1), workspace2_text
    )
    benchmark["validation_warmup_cmd"] = benchmark["validation_warmup_cmd"].replace(
        str(WORKSPACE1), workspace2_text
    )
    benchmark["workload_cmd"] = (
        f"python3 -u {LANE}/launch_outer_attempt2_online_t_v2/"
        "formal_job_outer_replay.py"
    )

    retraining = config["metrics"]["plan_estimator"]["model_retraining"]
    retraining["output_dir"] = (
        f"{generated_root_text}/memory/job_resource_models_fresh_2980mib"
    )
    retraining["run_id"] = "formal_job_outer_attempt2_online_t_v2_memory_job_v3"

    artifact = json.loads(TABLE.read_text(encoding="utf-8"))
    contract = config["experience_table_contract"]
    contract.update(
        {
            "artifact_path": str(TABLE),
            "artifact_sha256": sha256(TABLE),
            "table_id": artifact["table_id"],
            "version": artifact["version"],
            "table_sha256": artifact["table_sha256"],
            "active_params": artifact["active_params"],
            "low_confidence_freeze_threshold": artifact[
                "low_confidence_freeze_threshold"
            ],
        }
    )
    config["sensitivity_table"] = artifact["table"]

    feedback = config["final_evaluation"]["feedback_loop"]
    feedback["max_p95_ratio"] = 1.10
    feedback["max_p99_ratio"] = 1.10
    feedback["min_db_qps_ratio"] = 1.0
    feedback["formal_qps_source"] = "point_result.measurement.qps"
    feedback["correctness_required"] = True
    strong = feedback["resource_iteration_goal"]["strong_constraints"]
    strong["bottleneck_initial_target_min_util_pct"] = 85.0
    strong["bottleneck_initial_target_max_util_pct"] = 95.0
    strong["bottleneck_initial_non_target_min_util_pct"] = 30.0
    strong["bottleneck_initial_non_target_max_util_pct"] = 50.0
    config["_attempt2_contract"] = {
        "manifest_path": f"{attempt2_text}/attempt2_contract_manifest.json",
        "baseline_profile": {
            "target_resource": "memory",
            "target_util_pct_inclusive": [85.0, 95.0],
            "each_non_target_util_pct_inclusive": [30.0, 50.0],
        },
    }

    CONFIG.write_text(
        json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    for name, value in (
        ("replay_counter.txt", 25),
        ("formal_search_replay_counter.txt", 0),
        ("formal_search_accepted_counter.txt", 0),
        ("formal_startup_baseline_counter.txt", 1),
    ):
        (ATTEMPT2 / name).write_text(f"{value}\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "status": "prepared",
                "attempt2": str(ATTEMPT2),
                "workspace": str(WORKSPACE2),
                "config": str(CONFIG),
                "config_sha256": sha256(CONFIG),
                "table": str(TABLE),
                "table_artifact_sha256": sha256(TABLE),
                "table_canonical_sha256": artifact["table_sha256"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
