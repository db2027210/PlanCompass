#!/usr/bin/env python3
"""Bind the exact R0 profile and deployed attempt2 artifacts in one manifest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


LANE = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/"
    "pg5_common_outer_bgallowed_20260722_v1/job_node02"
)
ROOT = LANE / "history/job/outer/attempt_2_online_t_v2"
WORKSPACE = LANE / "workspace_outer_attempt2_online_t_v2"
CONFIG = ROOT / "generated_config/formal_job_outer_attempt2_online_t_v2/memory/job.json"
TABLE = WORKSPACE / (
    "config/formal/pg5_common_outer_bgallowed_20260722_v1/"
    "experience_table_T_job_memory_v2.json"
)
CODE = WORKSPACE / "parameter_optimizer.py"
REPLAY = LANE / "launch_outer_attempt2_online_t_v2/formal_job_outer_replay.py"
MANIFEST = ROOT / "attempt2_contract_manifest.json"
PROFILE = {
    "target_resource": "memory",
    "target_util_pct_inclusive": [85.0, 95.0],
    "each_non_target_util_pct_inclusive": [30.0, 50.0],
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    feedback = config["final_evaluation"]["feedback_loop"]
    strong = feedback["resource_iteration_goal"]["strong_constraints"]
    strong.update(
        {
            "bottleneck_initial_target_min_util_pct": 85.0,
            "bottleneck_initial_target_max_util_pct": 95.0,
            "bottleneck_initial_non_target_min_util_pct": 30.0,
            "bottleneck_initial_non_target_max_util_pct": 50.0,
        }
    )
    config["_attempt2_contract"] = {
        "manifest_path": str(MANIFEST),
        "baseline_profile": PROFILE,
    }
    CONFIG.write_text(
        json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    table = json.loads(TABLE.read_text(encoding="utf-8"))
    manifest = {
        "schema_version": 1,
        "attempt": "attempt_2_online_t_v2",
        "baseline_profile": PROFILE,
        "config_path": str(CONFIG),
        "config_sha256": sha256(CONFIG),
        "table_path": str(TABLE),
        "table_artifact_sha256": sha256(TABLE),
        "table_canonical_sha256": table["table_sha256"],
        "optimizer_code_path": str(CODE),
        "optimizer_code_sha256": sha256(CODE),
        "replay_bridge_path": str(REPLAY),
        "replay_bridge_sha256": sha256(REPLAY),
        "formal_qps_source": "point_result.measurement.qps",
        "correctness_required": True,
        "max_p95_ratio": 1.10,
        "max_p99_ratio": 1.10,
        "initial_replay_top_k": 5,
        "maximum_replay_top_k": 10,
        "accepted_target": 10,
    }
    MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({**manifest, "manifest_path": str(MANIFEST), "manifest_sha256": sha256(MANIFEST)}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
