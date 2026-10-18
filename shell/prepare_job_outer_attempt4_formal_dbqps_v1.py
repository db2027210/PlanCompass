#!/usr/bin/env python3
"""Prepare attempt4 for a new formal DB-QPS startup baseline and clean search."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
from typing import Any


LANE = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/"
    "pg5_common_outer_bgallowed_20260722_v1/job_node02"
)
ATTEMPT3 = LANE / "history/job/outer/attempt_3_online_t_v2_cleanupfix"
ATTEMPT4 = LANE / "history/job/outer/attempt_4_formal_dbqps_v1"
WORKSPACE3 = LANE / "workspace_outer_attempt3_online_t_v2_cleanupfix"
WORKSPACE4 = LANE / "workspace_outer_attempt4_formal_dbqps_v1"
LAUNCH4 = LANE / "launch_outer_attempt4_formal_dbqps_v1"
CONFIG3_ROOT = (
    ATTEMPT3
    / "generated_config/formal_job_outer_attempt3_online_t_v2_cleanupfix"
)
CONFIG4_ROOT = ATTEMPT4 / "generated_config/formal_job_outer_attempt4_formal_dbqps_v1"
CONFIG4 = CONFIG4_ROOT / "memory/job.json"
TABLE4 = (
    WORKSPACE4
    / "config/formal/pg5_common_outer_bgallowed_20260722_v1/"
    "experience_table_T_job_memory_v2.json"
)
CODE4 = WORKSPACE4 / "parameter_optimizer.py"
RUNNER4 = WORKSPACE4 / "formal_pg_point_runner.py"
REPLAY4 = LAUNCH4 / "formal_job_outer_replay.py"
MANIFEST4 = ATTEMPT4 / "attempt4_contract_manifest.json"
EXPECTED_CODE_SHA256 = "81c01808e08277c1d2f4e047323b1bf469141de9a40423895d5965e860f1a8ba"
EXPECTED_RUNNER_SHA256 = "acb25efff9cdb4e29948430fcd9f0064d9355a4a99660844ee3ac5f2f2d74839"
EXPECTED_REPLAY_SHA256 = "9be6f09f3f1b0f9497ea1c64b9125b0d3a1bdb3e52fb4e3d185b3527f602efd2"
EXPECTED_TABLE_ARTIFACT_SHA256 = "0bc09819a693bd1a3de0ab92975efee404ac9f9d36d3ae226ca7476554ac6341"
EXPECTED_TABLE_CANONICAL_SHA256 = "c21f73cbce988e0d38e72030e850f5daabf44a142759997a8b6fdf6f00b3786d"
GLOBAL_ORDINAL_BEFORE_STARTUP = 27
PROFILE = {
    "target_resource": "memory",
    "target_util_pct_inclusive": [85.0, 95.0],
    "each_non_target_util_pct_inclusive": [30.0, 50.0],
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def transform(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: transform(item) for key, item in value.items()}
    if isinstance(value, list):
        return [transform(item) for item in value]
    if isinstance(value, str):
        return (
            value.replace(str(ATTEMPT3), str(ATTEMPT4))
            .replace(str(WORKSPACE3), str(WORKSPACE4))
            .replace(
                "formal_job_outer_attempt3_online_t_v2_cleanupfix",
                "formal_job_outer_attempt4_formal_dbqps_v1",
            )
            .replace(
                "attempt_3_online_t_v2_cleanupfix",
                "attempt_4_formal_dbqps_v1",
            )
            .replace(
                "runtime_outer_attempt3_online_t_v2_cleanupfix",
                "runtime_outer_attempt4_formal_dbqps_v1",
            )
            .replace(
                "launch_outer_attempt3_online_t_v2_cleanupfix",
                "launch_outer_attempt4_formal_dbqps_v1",
            )
        )
    return value


def assert_staged_hash(path: Path, expected: str) -> None:
    if not path.is_file():
        raise RuntimeError(f"required staged file missing: {path}")
    actual = sha256(path)
    if actual != expected:
        raise RuntimeError(f"staged SHA mismatch for {path}: {actual} != {expected}")


def main() -> int:
    if ATTEMPT4.exists() or WORKSPACE4.exists():
        raise RuntimeError("refusing to overwrite existing attempt4 artifacts")
    for required in (
        CONFIG3_ROOT / "memory/job.json",
        WORKSPACE3,
        ATTEMPT3 / "fresh_restart_r0/latest.json",
    ):
        if not required.exists():
            raise RuntimeError(f"required attempt3 input missing: {required}")
    assert_staged_hash(LAUNCH4 / "parameter_optimizer.py", EXPECTED_CODE_SHA256)
    assert_staged_hash(LAUNCH4 / "formal_pg_point_runner.py", EXPECTED_RUNNER_SHA256)
    assert_staged_hash(REPLAY4, EXPECTED_REPLAY_SHA256)

    shutil.copytree(WORKSPACE3, WORKSPACE4, symlinks=True)
    shutil.copy2(LAUNCH4 / "parameter_optimizer.py", CODE4)
    shutil.copy2(LAUNCH4 / "formal_pg_point_runner.py", RUNNER4)
    CONFIG4_ROOT.parent.mkdir(parents=True, exist_ok=False)
    shutil.copytree(CONFIG3_ROOT, CONFIG4_ROOT, symlinks=True)
    (ATTEMPT4 / "fresh_restart_r0").mkdir(parents=True)
    shutil.copy2(
        ATTEMPT3 / "fresh_restart_r0/latest.json",
        ATTEMPT4 / "fresh_restart_r0/latest.json",
    )

    config = transform(json.loads(CONFIG4.read_text(encoding="utf-8")))
    if not isinstance(config, dict):
        raise RuntimeError("attempt4 config is not a mapping")
    config["_campaign"]["outer_attempt"] = 4
    config.pop("_attempt2_contract", None)
    config.pop("_attempt3_contract", None)
    config["_attempt4_contract"] = {
        "phase": "awaiting_new_formal_startup_baseline",
        "manifest_path": str(MANIFEST4),
        "baseline_profile": PROFILE,
        "previous_attempts_preserved": True,
        "previous_global_ordinal": GLOBAL_ORDINAL_BEFORE_STARTUP,
        "startup_baseline_must_use_measurement_db_qps": True,
    }
    config["startup_baseline_reuse"] = {"enabled": False}

    protocol = config["formal_outer_protocol"]
    protocol.update(
        {
            "global_replay_counter_path": str(ATTEMPT4 / "replay_counter.txt"),
            "global_replay_ordinal_at_binding": GLOBAL_ORDINAL_BEFORE_STARTUP,
            "formal_search_replay_counter_path": str(
                ATTEMPT4 / "formal_search_replay_counter.txt"
            ),
            "formal_search_replay_count_start": 0,
            "formal_search_accepted_counter_path": str(
                ATTEMPT4 / "formal_search_accepted_counter.txt"
            ),
            "formal_search_accepted_count_start": 0,
            "formal_startup_baseline_counter_path": str(
                ATTEMPT4 / "formal_startup_baseline_counter.txt"
            ),
            "formal_startup_baseline_count_start": 0,
            "formal_search_initial_replay_top_k": 5,
            "formal_search_max_replay_top_k": 10,
            "formal_search_maximum_replay_count": 10,
            "maximum_cumulative_real_replay_count": 10,
            "maximum_non_default_real_candidates": 10,
            "formal_search_accepted_target": 10,
            "accepted_shrink_target": 10,
        }
    )
    feedback = config["final_evaluation"]["feedback_loop"]
    feedback["formal_qps_source"] = "formal_benchmark_db_qps"
    feedback["db_qps_source"] = "formal_benchmark_db_qps"
    config.setdefault("formal_pg_resource_semantics", {})[
        "db_qps_source"
    ] = "formal_benchmark_db_qps"
    benchmark = config["benchmark"]
    benchmark["workload_cmd"] = f"python3 -u {REPLAY4}"
    strong = feedback["resource_iteration_goal"]["strong_constraints"]
    strong.update(
        {
            "bottleneck_initial_target_min_util_pct": 85.0,
            "bottleneck_initial_target_max_util_pct": 95.0,
            "bottleneck_initial_non_target_min_util_pct": 30.0,
            "bottleneck_initial_non_target_max_util_pct": 50.0,
        }
    )
    CONFIG4.write_text(
        json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    for name, value in (
        ("replay_counter.txt", GLOBAL_ORDINAL_BEFORE_STARTUP),
        ("formal_search_replay_counter.txt", 0),
        ("formal_search_accepted_counter.txt", 0),
        ("formal_startup_baseline_counter.txt", 0),
    ):
        (ATTEMPT4 / name).write_text(f"{value}\n", encoding="utf-8")

    table = json.loads(TABLE4.read_text(encoding="utf-8"))
    if sha256(TABLE4) != EXPECTED_TABLE_ARTIFACT_SHA256:
        raise RuntimeError("attempt4 experience-table artifact SHA mismatch")
    if table.get("table_sha256") != EXPECTED_TABLE_CANONICAL_SHA256:
        raise RuntimeError("attempt4 experience-table canonical SHA mismatch")
    manifest = {
        "schema_version": 1,
        "attempt": "attempt_4_formal_dbqps_v1",
        "phase": "awaiting_new_formal_startup_baseline",
        "baseline_profile": PROFILE,
        "previous_global_ordinal": GLOBAL_ORDINAL_BEFORE_STARTUP,
        "startup_global_ordinal": GLOBAL_ORDINAL_BEFORE_STARTUP + 1,
        "config_path": str(CONFIG4),
        "config_sha256": sha256(CONFIG4),
        "table_path": str(TABLE4),
        "table_artifact_sha256": sha256(TABLE4),
        "table_canonical_sha256": table["table_sha256"],
        "optimizer_code_path": str(CODE4),
        "optimizer_code_sha256": sha256(CODE4),
        "formal_runner_path": str(RUNNER4),
        "formal_runner_sha256": sha256(RUNNER4),
        "replay_bridge_path": str(REPLAY4),
        "replay_bridge_sha256": sha256(REPLAY4),
        "formal_qps_source": "point_result.measurement.db_qps",
        "db_qps_source": "formal_benchmark_db_qps",
        "candidate_budget_before_startup": 0,
        "startup_baseline_excluded_from_candidate_budget": True,
        "initial_replay_top_k": 5,
        "maximum_replay_top_k": 10,
        "accepted_target": 10,
    }
    MANIFEST4.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                **manifest,
                "status": "prepared_for_startup_baseline_only",
                "manifest_path": str(MANIFEST4),
                "manifest_sha256": sha256(MANIFEST4),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
