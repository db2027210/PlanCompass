#!/usr/bin/env python3
"""Fail-closed preflight for attempt4 before its one startup baseline."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


LANE = Path("/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02")
VARIANT = os.environ.get("RA_ATTEMPT4_VARIANT", "attempt_4_formal_dbqps_v1")
RUN_ID = os.environ.get("RA_ATTEMPT4_RUN_ID", "formal_job_outer_attempt4_formal_dbqps_v1")
WORKSPACE_NAME = os.environ.get("RA_ATTEMPT4_WORKSPACE", "workspace_outer_attempt4_formal_dbqps_v1")
LAUNCH_NAME = os.environ.get("RA_ATTEMPT4_LAUNCH", "launch_outer_attempt4_formal_dbqps_v1")
GLOBAL_BEFORE = int(os.environ.get("RA_ATTEMPT4_GLOBAL_BEFORE", "27"))
ROOT = LANE / "history/job/outer" / VARIANT
WORKSPACE = LANE / WORKSPACE_NAME
LAUNCH = LANE / LAUNCH_NAME
CONFIG = ROOT / "generated_config" / RUN_ID / "memory/job.json"
TABLE = WORKSPACE / "config/formal/pg5_common_outer_bgallowed_20260722_v1/experience_table_T_job_memory_v2.json"
CODE = WORKSPACE / "parameter_optimizer.py"
RUNNER = WORKSPACE / "formal_pg_point_runner.py"
ENGINE = WORKSPACE / "formal_pg_engine.py"
REPLAY = LAUNCH / "formal_job_outer_replay.py"
MANIFEST = ROOT / "attempt4_contract_manifest.json"
EXPECTED = {
    "optimizer_code_sha256": os.environ.get(
        "RA_ATTEMPT4_EXPECTED_OPTIMIZER_SHA256",
        "81c01808e08277c1d2f4e047323b1bf469141de9a40423895d5965e860f1a8ba",
    ),
    "formal_runner_sha256": os.environ.get(
        "RA_ATTEMPT4_EXPECTED_RUNNER_SHA256",
        "acb25efff9cdb4e29948430fcd9f0064d9355a4a99660844ee3ac5f2f2d74839",
    ),
    "replay_bridge_sha256": os.environ.get(
        "RA_ATTEMPT4_EXPECTED_BRIDGE_SHA256",
        "9be6f09f3f1b0f9497ea1c64b9125b0d3a1bdb3e52fb4e3d185b3527f602efd2",
    ),
    "formal_engine_sha256": os.environ.get(
        "RA_ATTEMPT4_EXPECTED_ENGINE_SHA256",
        "",
    ),
    "table_artifact_sha256": "0bc09819a693bd1a3de0ab92975efee404ac9f9d36d3ae226ca7476554ac6341",
    "table_canonical_sha256": "c21f73cbce988e0d38e72030e850f5daabf44a142759997a8b6fdf6f00b3786d",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    for path in (CONFIG, TABLE, CODE, RUNNER, ENGINE, REPLAY, MANIFEST):
        if not path.is_file():
            raise RuntimeError(f"required attempt4 artifact missing: {path}")
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    table = json.loads(TABLE.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    actual = {
        "optimizer_code_sha256": sha256(CODE),
        "formal_runner_sha256": sha256(RUNNER),
        "replay_bridge_sha256": sha256(REPLAY),
        "table_artifact_sha256": sha256(TABLE),
        "table_canonical_sha256": table.get("table_sha256"),
    }
    if EXPECTED["formal_engine_sha256"]:
        actual["formal_engine_sha256"] = sha256(ENGINE)
    expected = {key: value for key, value in EXPECTED.items() if value}
    if actual != expected:
        raise RuntimeError(f"attempt4 source SHA contract mismatch: {actual}")
    for key, value in actual.items():
        if manifest.get(key) != value:
            raise RuntimeError(f"attempt4 manifest mismatch for {key}")
    if manifest.get("config_sha256") != sha256(CONFIG):
        raise RuntimeError("attempt4 manifest config SHA mismatch")
    if manifest.get("phase") != "awaiting_new_formal_startup_baseline":
        raise RuntimeError("attempt4 is not in startup-baseline phase")
    if (config.get("startup_baseline_reuse") or {}).get("enabled") is not False:
        raise RuntimeError("old startup baseline reuse is still enabled")
    if (config.get("formal_pg_resource_semantics") or {}).get("db_qps_source") != "formal_benchmark_db_qps":
        raise RuntimeError("attempt4 formal DB-QPS source contract mismatch")
    protocol = config["formal_outer_protocol"]
    counter_expectations = {
        "global_replay_counter_path": GLOBAL_BEFORE,
        "formal_search_replay_counter_path": 0,
        "formal_search_accepted_counter_path": 0,
        "formal_startup_baseline_counter_path": 0,
    }
    counters = {}
    for key, expected in counter_expectations.items():
        path = Path(protocol[key])
        if path.parent != ROOT:
            raise RuntimeError(f"attempt4 counter escaped its root: {path}")
        actual_value = int(path.read_text(encoding="utf-8").strip())
        if actual_value != expected:
            raise RuntimeError(f"attempt4 counter {key}={actual_value}, expected {expected}")
        counters[key] = actual_value
    replay_text = REPLAY.read_text(encoding="utf-8")
    if 'measurement.get("db_qps")' not in replay_text:
        raise RuntimeError("bridge does not read measurement.db_qps")
    if 'client_qps = float(measurement["qps"])' not in replay_text:
        raise RuntimeError("bridge client-QPS separation marker missing")
    print(
        json.dumps(
            {
                "status": "ready_for_one_startup_baseline_only",
                "search_authorized": False,
                "expected_startup_point": GLOBAL_BEFORE + 1,
                "counters": counters,
                **actual,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
