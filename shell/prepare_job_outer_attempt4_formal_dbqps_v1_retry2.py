#!/usr/bin/env python3
"""Prepare retry2 with the authoritative extension/smoke-tested formal runner."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
from typing import Any


LANE = Path("/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02")
FAILED = LANE / "history/job/outer/attempt_4_formal_dbqps_v1_retry1"
ROOT = LANE / "history/job/outer/attempt_4_formal_dbqps_v1_retry2"
SOURCE_WORKSPACE = LANE / "workspace_outer_attempt4_formal_dbqps_v1_retry1"
WORKSPACE = LANE / "workspace_outer_attempt4_formal_dbqps_v1_retry2"
LAUNCH = LANE / "launch_outer_attempt4_formal_dbqps_v1_retry2"
SOURCE_CONFIG = FAILED / "generated_config/formal_job_outer_attempt4_formal_dbqps_v1_retry1/memory/job.json"
CONFIG = ROOT / "generated_config/formal_job_outer_attempt4_formal_dbqps_v1_retry2/memory/job.json"
TABLE = WORKSPACE / "config/formal/pg5_common_outer_bgallowed_20260722_v1/experience_table_T_job_memory_v2.json"
MANIFEST = ROOT / "attempt4_contract_manifest.json"
EXPECTED = {
    "optimizer": "81c01808e08277c1d2f4e047323b1bf469141de9a40423895d5965e860f1a8ba",
    "runner": "9b655402d82ede45a7d509cd81fdf9eb7c79c828c066b1b117bdc8fef2fd19ae",
    "runner_test": "dd4537230c9a4f90d5182e2def4f522d69f9d3d702fe42aa6a75cf77e492401e",
    "bridge": "9be6f09f3f1b0f9497ea1c64b9125b0d3a1bdb3e52fb4e3d185b3527f602efd2",
    "table": "0bc09819a693bd1a3de0ab92975efee404ac9f9d36d3ae226ca7476554ac6341",
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
            value.replace(str(FAILED), str(ROOT))
            .replace(str(SOURCE_WORKSPACE), str(WORKSPACE))
            .replace("formal_job_outer_attempt4_formal_dbqps_v1_retry1", "formal_job_outer_attempt4_formal_dbqps_v1_retry2")
            .replace("launch_outer_attempt4_formal_dbqps_v1_retry1", "launch_outer_attempt4_formal_dbqps_v1_retry2")
            .replace("runtime_outer_attempt4_formal_dbqps_v1_retry1", "runtime_outer_attempt4_formal_dbqps_v1_retry2")
        )
    return value


def require_hash(path: Path, expected: str) -> None:
    if not path.is_file() or sha256(path) != expected:
        raise RuntimeError(f"retry2 staged SHA mismatch: {path}")


def main() -> int:
    if ROOT.exists() or WORKSPACE.exists():
        raise RuntimeError("refusing to overwrite attempt4 retry2")
    failed_result = FAILED / "formal_points/point_0029/point_result.json"
    for required in (
        SOURCE_CONFIG,
        SOURCE_WORKSPACE,
        FAILED / "fresh_restart_r0/latest.json",
        failed_result,
        FAILED / "startup_baseline_supervisor.log",
    ):
        if not required.exists():
            raise RuntimeError(f"retry2 predecessor evidence missing: {required}")
    failed = json.loads(failed_result.read_text(encoding="utf-8"))
    if (
        failed.get("status") != "failed"
        or "pg_stat_statements" not in str(failed.get("error") or "")
        or (failed.get("measurement") or {}).get("db_qps") is not None
    ):
        raise RuntimeError("point 29 is not the expected missing-extension failure")
    require_hash(LAUNCH / "parameter_optimizer.py", EXPECTED["optimizer"])
    require_hash(LAUNCH / "formal_pg_point_runner.authoritative.py", EXPECTED["runner"])
    require_hash(LAUNCH / "test_formal_db_qps_runner.py", EXPECTED["runner_test"])
    require_hash(LAUNCH / "formal_job_outer_replay.py", EXPECTED["bridge"])
    if (LAUNCH / "formal_pg_point_runner.py").exists():
        raise RuntimeError("retry2 launch directory must not shadow workspace runner")

    shutil.copytree(SOURCE_WORKSPACE, WORKSPACE, symlinks=True)
    shutil.copy2(LAUNCH / "parameter_optimizer.py", WORKSPACE / "parameter_optimizer.py")
    shutil.copy2(LAUNCH / "formal_pg_point_runner.authoritative.py", WORKSPACE / "formal_pg_point_runner.py")
    CONFIG.parent.mkdir(parents=True, exist_ok=False)
    shutil.copy2(SOURCE_CONFIG, CONFIG)
    (ROOT / "fresh_restart_r0").mkdir(parents=True)
    shutil.copy2(FAILED / "fresh_restart_r0/latest.json", ROOT / "fresh_restart_r0/latest.json")

    config = transform(json.loads(CONFIG.read_text(encoding="utf-8")))
    config["startup_baseline_reuse"] = {"enabled": False}
    config["_campaign"]["outer_attempt"] = 4
    config["_attempt4_contract"] = {
        "phase": "awaiting_new_formal_startup_baseline_retry2",
        "manifest_path": str(MANIFEST),
        "previous_global_ordinal": 29,
        "failed_point_29_path": str(failed_result),
        "failed_point_29_sha256": sha256(failed_result),
        "failure_reason": "fresh_point_pg_stat_statements_extension_missing",
        "points_28_and_29_preserved_without_counter_rollback": True,
        "authoritative_runner_fix": (
            "create_extension_and_numeric_sum_calls_smoke_before_warmup_measurement"
        ),
    }
    protocol = config["formal_outer_protocol"]
    protocol.update(
        {
            "global_replay_counter_path": str(ROOT / "replay_counter.txt"),
            "global_replay_ordinal_at_binding": 29,
            "formal_search_replay_counter_path": str(ROOT / "formal_search_replay_counter.txt"),
            "formal_search_replay_count_start": 0,
            "formal_search_accepted_counter_path": str(ROOT / "formal_search_accepted_counter.txt"),
            "formal_search_accepted_count_start": 0,
            "formal_startup_baseline_counter_path": str(ROOT / "formal_startup_baseline_counter.txt"),
            "formal_startup_baseline_count_start": 0,
        }
    )
    config["benchmark"]["workload_cmd"] = f"python3 -u {LAUNCH / 'formal_job_outer_replay.py'}"
    CONFIG.write_text(json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for name, value in (
        ("replay_counter.txt", 29),
        ("formal_search_replay_counter.txt", 0),
        ("formal_search_accepted_counter.txt", 0),
        ("formal_startup_baseline_counter.txt", 0),
    ):
        (ROOT / name).write_text(f"{value}\n", encoding="utf-8")
    require_hash(TABLE, EXPECTED["table"])
    table = json.loads(TABLE.read_text(encoding="utf-8"))
    manifest = {
        "schema_version": 1,
        "attempt": "attempt_4_formal_dbqps_v1_retry2",
        "phase": "awaiting_new_formal_startup_baseline",
        "previous_global_ordinal": 29,
        "startup_global_ordinal": 30,
        "config_path": str(CONFIG),
        "config_sha256": sha256(CONFIG),
        "table_path": str(TABLE),
        "table_artifact_sha256": sha256(TABLE),
        "table_canonical_sha256": table["table_sha256"],
        "optimizer_code_path": str(WORKSPACE / "parameter_optimizer.py"),
        "optimizer_code_sha256": sha256(WORKSPACE / "parameter_optimizer.py"),
        "formal_runner_path": str(WORKSPACE / "formal_pg_point_runner.py"),
        "formal_runner_sha256": sha256(WORKSPACE / "formal_pg_point_runner.py"),
        "formal_runner_test_path": str(LAUNCH / "test_formal_db_qps_runner.py"),
        "formal_runner_test_sha256": sha256(LAUNCH / "test_formal_db_qps_runner.py"),
        "formal_runner_test_count": 6,
        "formal_runner_test_status": "passed_by_authoritative_owner",
        "replay_bridge_path": str(LAUNCH / "formal_job_outer_replay.py"),
        "replay_bridge_sha256": sha256(LAUNCH / "formal_job_outer_replay.py"),
        "formal_qps_source": "point_result.measurement.db_qps",
        "db_qps_source": "formal_benchmark_db_qps",
        "candidate_budget_before_startup": 0,
        "startup_baseline_excluded_from_candidate_budget": True,
        "failed_point_29_path": str(failed_result),
        "failed_point_29_sha256": sha256(failed_result),
        "points_28_and_29_preserved_without_counter_rollback": True,
        "runner_extension_smoke_fix": True,
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({**manifest, "status": "prepared_for_retry2_startup_only", "manifest_sha256": sha256(MANIFEST)}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
