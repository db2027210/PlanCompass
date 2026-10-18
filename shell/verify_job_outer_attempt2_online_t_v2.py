#!/usr/bin/env python3
"""Fail-closed preflight for isolated JOB outer attempt2 online-T-v2."""

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
CONFIG = (
    ROOT
    / "generated_config/formal_job_outer_attempt2_online_t_v2/memory/job.json"
)
TABLE = (
    WORKSPACE
    / "config/formal/pg5_common_outer_bgallowed_20260722_v1/"
    "experience_table_T_job_memory_v2.json"
)
CODE = WORKSPACE / "parameter_optimizer.py"
REPLAY = (
    LANE / "launch_outer_attempt2_online_t_v2/formal_job_outer_replay.py"
)
MANIFEST = ROOT / "attempt2_contract_manifest.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    for path in (CONFIG, TABLE, CODE, REPLAY, MANIFEST):
        if not path.is_file():
            raise RuntimeError(f"required attempt2 artifact missing: {path}")
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    table = json.loads(TABLE.read_text(encoding="utf-8"))
    contract = config["experience_table_contract"]
    canonical_sha = hashlib.sha256(
        json.dumps(
            table["table"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if table.get("schema_version") != 2:
        raise RuntimeError("experience table is not schema v2")
    if set(next(iter(table["table"].values()))) != {
        "cpu", "memory", "io", "qps", "confidence", "evidence_count"
    }:
        raise RuntimeError("experience table v2 fields are incomplete")
    if (
        contract.get("artifact_sha256") != sha256(TABLE)
        or contract.get("table_sha256") != canonical_sha
        or table.get("table_sha256") != canonical_sha
        or config.get("sensitivity_table") != table.get("table")
    ):
        raise RuntimeError("config/T hash binding mismatch")
    protocol = config["formal_outer_protocol"]
    expected = {
        "global_replay_counter_path": 25,
        "formal_search_replay_counter_path": 0,
        "formal_search_accepted_counter_path": 0,
        "formal_startup_baseline_counter_path": 1,
    }
    counters = {}
    for key, value in expected.items():
        path = Path(protocol[key])
        if path.parent != ROOT:
            raise RuntimeError(f"attempt2 counter escaped isolated root: {path}")
        actual = int(path.read_text(encoding="utf-8").strip())
        if actual != value:
            raise RuntimeError(f"counter mismatch {key}: {actual} != {value}")
        counters[key] = actual
    if (
        protocol.get("global_replay_ordinal_at_binding") != 25
        or protocol.get("formal_search_initial_replay_top_k") != 5
        or protocol.get("formal_search_max_replay_top_k") != 10
        or protocol.get("formal_search_maximum_replay_count") != 10
        or protocol.get("formal_search_accepted_target") != 10
    ):
        raise RuntimeError("attempt2 5->10 replay/accepted protocol mismatch")
    feedback = config["final_evaluation"]["feedback_loop"]
    if (
        float(feedback.get("max_p95_ratio")) > 1.10
        or float(feedback.get("max_p99_ratio")) > 1.10
        or feedback.get("correctness_required") is not True
        or feedback.get("formal_qps_source") != "point_result.measurement.qps"
    ):
        raise RuntimeError("formal correctness/QPS/latency guard mismatch")
    strong = feedback["resource_iteration_goal"]["strong_constraints"]
    exact_baseline_profile = {
        "target_resource": "memory",
        "target_util_pct_inclusive": [85.0, 95.0],
        "each_non_target_util_pct_inclusive": [30.0, 50.0],
    }
    if (
        strong.get("bottleneck_initial_target_min_util_pct") != 85.0
        or strong.get("bottleneck_initial_target_max_util_pct") != 95.0
        or strong.get("bottleneck_initial_non_target_min_util_pct") != 30.0
        or strong.get("bottleneck_initial_non_target_max_util_pct") != 50.0
        or (config.get("_attempt2_contract") or {}).get("baseline_profile")
        != exact_baseline_profile
    ):
        raise RuntimeError("unified R0 85-95/30-50 baseline profile mismatch")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if (
        manifest.get("baseline_profile") != exact_baseline_profile
        or manifest.get("config_sha256") != sha256(CONFIG)
        or manifest.get("table_artifact_sha256") != sha256(TABLE)
        or manifest.get("table_canonical_sha256") != canonical_sha
        or manifest.get("optimizer_code_sha256") != sha256(CODE)
        or manifest.get("replay_bridge_sha256") != sha256(REPLAY)
    ):
        raise RuntimeError("attempt2 manifest hash/profile binding mismatch")
    startup = config["startup_baseline_reuse"]
    for key in ("point_result", "reclassification_evidence"):
        path = Path(startup[f"{key}_path"])
        if not path.is_file() or sha256(path) != startup[f"{key}_sha256"]:
            raise RuntimeError(f"immutable startup baseline hash mismatch: {key}")
    source = CODE.read_text(encoding="utf-8")
    for marker in (
        "point_result.measurement.qps",
        "_online_update_experience_table_and_rerank",
        'db_qps_key=',
        '"formal_qps"',
        '"static_blind_expansion": False',
    ):
        if marker not in source:
            raise RuntimeError(f"patched optimizer marker missing: {marker}")
    print(
        json.dumps(
            {
                "status": "ready",
                "attempt_root": str(ROOT),
                "config_path": str(CONFIG),
                "config_sha256": sha256(CONFIG),
                "table_path": str(TABLE),
                "table_artifact_sha256": sha256(TABLE),
                "table_canonical_sha256": canonical_sha,
                "optimizer_code_sha256": sha256(CODE),
                "replay_bridge_sha256": sha256(REPLAY),
                "counters": counters,
                "formal_qps_source": feedback["formal_qps_source"],
                "correctness_required": True,
                "p95_ratio_max": feedback["max_p95_ratio"],
                "p99_ratio_max": feedback["max_p99_ratio"],
                "initial_replay_top_k": 5,
                "maximum_replay_top_k": 10,
                "accepted_target": 10,
                "baseline_profile": exact_baseline_profile,
                "manifest_path": str(MANIFEST),
                "manifest_sha256": sha256(MANIFEST),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
