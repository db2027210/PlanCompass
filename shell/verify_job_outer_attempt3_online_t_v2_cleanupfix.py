#!/usr/bin/env python3
"""Fail-closed preflight for clean-budget JOB attempt3."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


LANE = Path("/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02")
ROOT = LANE / "history/job/outer/attempt_3_online_t_v2_cleanupfix"
WORKSPACE = LANE / "workspace_outer_attempt3_online_t_v2_cleanupfix"
LAUNCH = LANE / "launch_outer_attempt3_online_t_v2_cleanupfix"
CONFIG = ROOT / "generated_config/formal_job_outer_attempt3_online_t_v2_cleanupfix/memory/job.json"
TABLE = WORKSPACE / "config/formal/pg5_common_outer_bgallowed_20260722_v1/experience_table_T_job_memory_v2.json"
CODE = WORKSPACE / "parameter_optimizer.py"
REPLAY = LAUNCH / "formal_job_outer_replay.py"
MANIFEST = ROOT / "attempt3_contract_manifest.json"
PROFILE = {
    "target_resource": "memory",
    "target_util_pct_inclusive": [85.0, 95.0],
    "each_non_target_util_pct_inclusive": [30.0, 50.0],
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    for path in (CONFIG, TABLE, CODE, REPLAY, MANIFEST):
        if not path.is_file():
            raise RuntimeError(f"required attempt3 artifact missing: {path}")
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    table = json.loads(TABLE.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    canonical_sha = hashlib.sha256(
        json.dumps(table["table"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    expected_hashes = {
        "config_sha256": sha256(CONFIG),
        "table_artifact_sha256": sha256(TABLE),
        "table_canonical_sha256": canonical_sha,
        "optimizer_code_sha256": sha256(CODE),
        "replay_bridge_sha256": sha256(REPLAY),
    }
    if any(manifest.get(key) != value for key, value in expected_hashes.items()):
        raise RuntimeError("attempt3 manifest hash binding mismatch")
    if manifest.get("baseline_profile") != PROFILE:
        raise RuntimeError("attempt3 manifest R0 profile mismatch")
    if manifest.get("cleanup_allowed_root_source") != "RA_PG_WORK_ROOT":
        raise RuntimeError("attempt3 cleanup root contract mismatch")
    if 'os.environ.get(\n            "RA_PG_WORK_ROOT"' not in REPLAY.read_text(encoding="utf-8"):
        raise RuntimeError("dynamic cleanup allow-root implementation missing")
    protocol = config["formal_outer_protocol"]
    counters = {}
    for key, expected in (
        ("global_replay_counter_path", 25),
        ("formal_search_replay_counter_path", 0),
        ("formal_search_accepted_counter_path", 0),
        ("formal_startup_baseline_counter_path", 1),
    ):
        path = Path(protocol[key])
        if path.parent != ROOT:
            raise RuntimeError(f"counter escaped attempt3 root: {path}")
        actual = int(path.read_text(encoding="utf-8").strip())
        if actual != expected:
            raise RuntimeError(f"attempt3 counter mismatch: {key}={actual}, expected={expected}")
        counters[key] = actual
    if (
        protocol.get("formal_search_initial_replay_top_k") != 5
        or protocol.get("formal_search_max_replay_top_k") != 10
        or protocol.get("formal_search_maximum_replay_count") != 10
        or protocol.get("formal_search_accepted_target") != 10
    ):
        raise RuntimeError("attempt3 5->10/accepted protocol mismatch")
    strong = config["final_evaluation"]["feedback_loop"]["resource_iteration_goal"]["strong_constraints"]
    if [strong.get("bottleneck_initial_target_min_util_pct"), strong.get("bottleneck_initial_target_max_util_pct")] != [85.0, 95.0]:
        raise RuntimeError("attempt3 target R0 band mismatch")
    if [strong.get("bottleneck_initial_non_target_min_util_pct"), strong.get("bottleneck_initial_non_target_max_util_pct")] != [30.0, 50.0]:
        raise RuntimeError("attempt3 non-target R0 band mismatch")
    predecessor = (config.get("_attempt3_contract") or {}).get("predecessor_failure") or {}
    for key in ("point_result_path", "launcher_exit_path"):
        if not Path(str(predecessor.get(key) or "")).is_file():
            raise RuntimeError(f"attempt2 predecessor evidence missing: {key}")
    print(json.dumps({"status": "ready", "pid_scope": "not_started", "attempt_root": str(ROOT), "manifest_path": str(MANIFEST), "manifest_sha256": sha256(MANIFEST), "counters": counters, "baseline_profile": PROFILE, **expected_hashes}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
