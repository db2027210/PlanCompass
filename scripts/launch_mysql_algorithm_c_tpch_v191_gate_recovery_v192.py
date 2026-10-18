#!/usr/bin/env python3
"""Restart only the v191 TPC-H scene after a transient fail-closed gate."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_tpch_gate_recovery_20260929_v192/"
    "tpch_gate_recovery_contract.json"
)
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v98.py"
AUTHORITY = HISTORY / "c_v156_candidate_authority_v36_20260923/authority"
FRESH_R0 = HISTORY / "c_v156_candidate_fresh_r0_v36_20260923/fresh_r0_binding.json"
BASELINE_SET = HISTORY / "c_v152_formal_r0_set_v36_20260923/formal_r0_set.json"
BASELINE = HISTORY / (
    "c_v148_paper_baseline_calibration_20260923_tpch/"
    "formal_incumbent_baseline/batch_result.json"
)
CHECKPOINT = HISTORY / (
    "c_v150_paper_resource_v36_bootstrap_20260923/checkpoint/prepared_binding.json"
)
QUARANTINE = HISTORY / (
    "c_v150_paper_resource_v36_bootstrap_20260923/"
    "replay_quarantine/replay_quarantine.json"
)
RUN_ID = "c_v192_tpch_v191_gate_recovery_20260929_tpch"
CAMPAIGN = HISTORY / "c_v192_tpch_v191_gate_recovery_20260929"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n"
    )


def validate() -> dict[str, Any]:
    value = load(CONTRACT)
    if (
        value.get("release_version") != "v192-tpch-v191-gate-recovery"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("scene") != "tpch_sf4_memory"
        or value.get("fresh_scene_search_required") is not True
        or value.get("source_formal_credit") != 0
        or value.get("other_scene_process_control_or_stop_performed") is not False
    ):
        raise RuntimeError("TPC-H v192 gate recovery contract differs")
    for name, raw in dict(value.get("source_bindings") or {}).items():
        path = ROOT / str(raw.get("path") or "")
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"TPC-H v192 recovery source differs: {name}: {path}")
    state = load(HISTORY / "c_v191_pg_exact_hwloc_four_scene_20260929_tpch/run_state.json")
    if (
        state.get("status") != "FAILED_CLOSED"
        or state.get("error") != "RuntimeError: dynamic non-interference gate failed closed"
        or int((state.get("accepted_rounds") or {}).get("tpch_sf4_memory") or 0) != 0
    ):
        raise RuntimeError("TPC-H v191 source state differs")
    return value


def main() -> int:
    contract = validate()
    output = HISTORY / RUN_ID
    barrier = Path("/tmp/mysql_async_outer_start") / RUN_ID / "algorithm_c"
    if CAMPAIGN.exists() or output.exists() or barrier.exists():
        raise RuntimeError("fresh TPC-H recovery paths required")
    CAMPAIGN.mkdir(parents=True, mode=0o700)
    barrier.mkdir(parents=True, mode=0o700)
    log_path = CAMPAIGN / "tpch.controller.log"
    preflight = AUTHORITY / "transition_preflight.json"
    prepared = AUTHORITY / "prepared_binding.json"
    accounts = AUTHORITY / "benchmark_accounts_binding.json"
    command = [
        sys.executable, str(CONTROLLER), "--run-id", RUN_ID,
        "--checkpoint", str(CHECKPOINT),
        "--runtime-preflight", str(preflight),
        "--prepared-binding", str(prepared),
        "--benchmark-accounts", str(accounts),
        "--fresh-r0", str(FRESH_R0),
        "--replay-quarantine", str(QUARANTINE),
        "--fresh-search", "--scene", "tpch_sf4_memory",
        "--scene-start-barrier", str(barrier),
        "--formal-baseline", str(BASELINE),
        "--formal-r0-set", str(BASELINE_SET),
    ]
    environment = os.environ.copy()
    environment["MYSQL_TWO_WORKER_AUTHORITY_ROOT"] = str(AUTHORITY)
    with log_path.open("w") as log:
        process = subprocess.Popen(
            command, cwd=ROOT, env=environment, stdout=log,
            stderr=subprocess.STDOUT, start_new_session=True,
            preexec_fn=lambda: os.sched_setaffinity(
                0, set(range(8, 16)) | set(range(264, 272))
            ),
        )
    atomic(CAMPAIGN / "launch_manifest.json", {
        "schema_version": "mysql_algorithm_c_tpch_v191_gate_recovery_launch_v1",
        "status": "RUNNING",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "release_version": contract["release_version"],
        "contract": str(CONTRACT),
        "contract_sha256": sha(CONTRACT),
        "source_four_scene_launch": str(value := HISTORY / "c_v191_pg_exact_hwloc_four_scene_20260929/launch_manifest.json"),
        "source_four_scene_launch_sha256": sha(value),
        "run_id": RUN_ID,
        "pid": process.pid,
        "scene": "tpch_sf4_memory",
        "controller_cpuset": "8-15,264-271",
        "fresh_scene_search": True,
        "source_formal_credit": 0,
        "other_scene_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
        "log": str(log_path),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
