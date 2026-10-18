#!/usr/bin/env python3
"""Create clean-budget attempt3 after attempt2's post-measurement cleanup failure."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil


LANE = Path("/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02")
ATTEMPT2 = LANE / "history/job/outer/attempt_2_online_t_v2"
ATTEMPT3 = LANE / "history/job/outer/attempt_3_online_t_v2_cleanupfix"
WORKSPACE2 = LANE / "workspace_outer_attempt2_online_t_v2"
WORKSPACE3 = LANE / "workspace_outer_attempt3_online_t_v2_cleanupfix"
LAUNCH3 = LANE / "launch_outer_attempt3_online_t_v2_cleanupfix"
CONFIG2_ROOT = ATTEMPT2 / "generated_config/formal_job_outer_attempt2_online_t_v2"
CONFIG3_ROOT = ATTEMPT3 / "generated_config/formal_job_outer_attempt3_online_t_v2_cleanupfix"
CONFIG3 = CONFIG3_ROOT / "memory/job.json"
TABLE3 = WORKSPACE3 / "config/formal/pg5_common_outer_bgallowed_20260722_v1/experience_table_T_job_memory_v2.json"
CODE3 = WORKSPACE3 / "parameter_optimizer.py"
REPLAY3 = LAUNCH3 / "formal_job_outer_replay.py"
MANIFEST3 = ATTEMPT3 / "attempt3_contract_manifest.json"
PROFILE = {
    "target_resource": "memory",
    "target_util_pct_inclusive": [85.0, 95.0],
    "each_non_target_util_pct_inclusive": [30.0, 50.0],
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def transform(value: object) -> object:
    if isinstance(value, dict):
        return {key: transform(item) for key, item in value.items()}
    if isinstance(value, list):
        return [transform(item) for item in value]
    if isinstance(value, str):
        return (
            value.replace(str(ATTEMPT2), str(ATTEMPT3))
            .replace(str(WORKSPACE2), str(WORKSPACE3))
            .replace(
                "formal_job_outer_attempt2_online_t_v2",
                "formal_job_outer_attempt3_online_t_v2_cleanupfix",
            )
            .replace(
                "attempt_2_online_t_v2",
                "attempt_3_online_t_v2_cleanupfix",
            )
            .replace(
                "runtime_outer_attempt2_online_t_v2",
                "runtime_outer_attempt3_online_t_v2_cleanupfix",
            )
            .replace(
                "launch_outer_attempt2_online_t_v2",
                "launch_outer_attempt3_online_t_v2_cleanupfix",
            )
        )
    return value


def main() -> int:
    if ATTEMPT3.exists() or WORKSPACE3.exists():
        raise RuntimeError("refusing to overwrite existing attempt3 artifacts")
    for required in (
        CONFIG2_ROOT / "memory/job.json",
        WORKSPACE2,
        REPLAY3,
        ATTEMPT2 / "fresh_restart_r0/latest.json",
        ATTEMPT2 / "formal_points/point_0026/point_result.json",
        ATTEMPT2 / "launcher_exit.json",
    ):
        if not required.exists():
            raise RuntimeError(f"required attempt2 evidence/input missing: {required}")

    shutil.copytree(WORKSPACE2, WORKSPACE3, symlinks=True)
    CONFIG3_ROOT.parent.mkdir(parents=True, exist_ok=False)
    shutil.copytree(CONFIG2_ROOT, CONFIG3_ROOT, symlinks=True)
    (ATTEMPT3 / "fresh_restart_r0").mkdir(parents=True)
    shutil.copy2(
        ATTEMPT2 / "fresh_restart_r0/latest.json",
        ATTEMPT3 / "fresh_restart_r0/latest.json",
    )

    config = transform(json.loads(CONFIG3.read_text(encoding="utf-8")))
    assert isinstance(config, dict)
    config["_campaign"]["outer_attempt"] = 3
    config.pop("_attempt2_contract", None)
    config["_attempt3_contract"] = {
        "manifest_path": str(MANIFEST3),
        "baseline_profile": PROFILE,
        "predecessor_failure": {
            "attempt": "attempt_2_online_t_v2",
            "point_result_path": str(ATTEMPT2 / "formal_points/point_0026/point_result.json"),
            "launcher_exit_path": str(ATTEMPT2 / "launcher_exit.json"),
            "reason": "post_measurement_cleanup_allow_root_hardcoded_to_attempt1",
            "predecessor_counters_not_rolled_back": True,
        },
    }
    protocol = config["formal_outer_protocol"]
    protocol.update(
        {
            "global_replay_counter_path": str(ATTEMPT3 / "replay_counter.txt"),
            "global_replay_ordinal_at_binding": 25,
            "formal_search_replay_counter_path": str(ATTEMPT3 / "formal_search_replay_counter.txt"),
            "formal_search_replay_count_start": 0,
            "formal_search_accepted_counter_path": str(ATTEMPT3 / "formal_search_accepted_counter.txt"),
            "formal_search_accepted_count_start": 0,
            "formal_startup_baseline_counter_path": str(ATTEMPT3 / "formal_startup_baseline_counter.txt"),
            "formal_startup_baseline_count_start": 1,
            "formal_search_initial_replay_top_k": 5,
            "formal_search_max_replay_top_k": 10,
            "formal_search_maximum_replay_count": 10,
            "formal_search_accepted_target": 10,
        }
    )
    CONFIG3.write_text(
        json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    for name, value in (
        ("replay_counter.txt", 25),
        ("formal_search_replay_counter.txt", 0),
        ("formal_search_accepted_counter.txt", 0),
        ("formal_startup_baseline_counter.txt", 1),
    ):
        (ATTEMPT3 / name).write_text(f"{value}\n", encoding="utf-8")

    table = json.loads(TABLE3.read_text(encoding="utf-8"))
    manifest = {
        "schema_version": 1,
        "attempt": "attempt_3_online_t_v2_cleanupfix",
        "baseline_profile": PROFILE,
        "config_path": str(CONFIG3),
        "config_sha256": sha256(CONFIG3),
        "table_path": str(TABLE3),
        "table_artifact_sha256": sha256(TABLE3),
        "table_canonical_sha256": table["table_sha256"],
        "optimizer_code_path": str(CODE3),
        "optimizer_code_sha256": sha256(CODE3),
        "replay_bridge_path": str(REPLAY3),
        "replay_bridge_sha256": sha256(REPLAY3),
        "cleanup_allowed_root_source": "RA_PG_WORK_ROOT",
        "predecessor_attempt2_preserved": True,
        "formal_qps_source": "point_result.measurement.qps",
        "correctness_required": True,
        "max_p95_ratio": 1.10,
        "max_p99_ratio": 1.10,
        "initial_replay_top_k": 5,
        "maximum_replay_top_k": 10,
        "accepted_target": 10,
    }
    MANIFEST3.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {**manifest, "manifest_path": str(MANIFEST3), "manifest_sha256": sha256(MANIFEST3)},
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
