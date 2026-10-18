#!/usr/bin/env python3
"""Resume TPC-H Algorithm C from the completed v196 outer-3 boundary."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v10 as checkpoint_v10
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v101 as controller


HISTORY = ROOT / "history/mysql_four_scene_20260809"
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v101.py"
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_tpch_recovery_20260929_v198/"
    "tpch_recovery_contract.json"
)
CHECKPOINT = HISTORY / (
    "c_v197_tpch_v196_outer3_gate_checkpoint_v10b_20260929/prepared_binding.json"
)
QUARANTINE = HISTORY / (
    "c_v197_tpch_v196_outer3_gate_quarantine_v10b_20260929/replay_quarantine.json"
)
SOURCE_ROOT = HISTORY / "c_v196_pg_exact_final_four_scene_20260929_tpch"
SOURCE_STATE = SOURCE_ROOT / "run_state.json"
BLOCKED_GATE = SOURCE_ROOT / "outer_004/top5_batch_1_dynamic_noninterference_gate.json"
AUTHORITY = HISTORY / "c_v156_candidate_authority_v36_20260923/authority"
FRESH_R0 = HISTORY / "c_v156_candidate_fresh_r0_v36_20260923/fresh_r0_binding.json"
FORMAL_R0_SET = HISTORY / "c_v152_formal_r0_set_v36_20260923/formal_r0_set.json"
FORMAL_BASELINE = HISTORY / (
    "c_v148_paper_baseline_calibration_20260923_tpch/"
    "formal_incumbent_baseline/batch_result.json"
)
AFFINITY = "8-15,264-271"
SCENE = "tpch_sf4_memory"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def cpus(value: str) -> set[int]:
    result: set[int] = set()
    for item in value.split(","):
        left, separator, right = item.partition("-")
        result.update(range(int(left), int(right) + 1) if separator else [int(left)])
    return result


def validate() -> tuple[dict[str, Any], dict[str, Any]]:
    controller.validate_contract()
    contract = load(CONTRACT)
    checkpoint = checkpoint_v10.verify_checkpoint(CHECKPOINT)
    quarantine = load(QUARANTINE)
    source = load(SOURCE_STATE)
    blocked = load(BLOCKED_GATE)
    preflight = load(AUTHORITY / "transition_preflight.json")
    prepared = load(AUTHORITY / "prepared_binding.json")
    accounts = load(AUTHORITY / "benchmark_accounts_binding.json")
    fresh_r0 = load(FRESH_R0)
    decisions = [
        load(SOURCE_ROOT / f"outer_{outer:03d}/{SCENE}.decision.json")
        for outer in range(1, 4)
    ]
    if (
        contract.get("release_version") != "v198-tpch-v196-outer3-gate-recovery"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("completed_outer_attempts_preserved") != 3
        or contract.get("discarded_incomplete_outer") != 4
        or contract.get("discarded_incomplete_outer_credit") != 0
        or contract.get("fresh_search_reset_on_resume") is not False
        or contract.get("workload_or_candidate_policy_changed") is not False
        or contract.get("resource_envelope_changed") is not False
        or checkpoint.get("scene") != SCENE
        or int(checkpoint.get("completed_outer_attempts") or 0) != 3
        or int(checkpoint["reconstructed_state"]["accepted_rounds"][SCENE]) != 3
        or int(checkpoint["reconstructed_state"]["rejected_outer_attempts"][SCENE]) != 0
        or quarantine.get("status") != "PASS"
        or int(quarantine.get("resume_checkpoint_outer_attempt") or 0) != 3
        or quarantine.get("evidence", {}).get("checkpoint", {}).get("sha256")
        != sha(CHECKPOINT)
        or source.get("status") != "FAILED_CLOSED"
        or source.get("error") != "RuntimeError: dynamic non-interference gate failed closed"
        or int(source.get("outer_attempt") or 0) != 3
        or source.get("fresh_search") is not True
        or int(source.get("completed_predecessor_outer_attempts", -1)) != 0
        or Path(str(source.get("formal_incumbent_baseline", {}).get("path") or "")).resolve()
        != FORMAL_BASELINE.resolve()
        or source.get("formal_incumbent_baseline", {}).get("sha256")
        != sha(FORMAL_BASELINE)
        or blocked.get("status") != "BLOCKED_FAIL_CLOSED"
        or "dynamic_noninterference_window_timeout" not in list(blocked.get("errors") or [])
        or any(row.get("accepted") is not True for row in decisions)
        or any(
            row.get("rejection_cycle", {}).get("policy_id")
            != "mysql_c_pg_half_interval_two_reject_shrink_v3"
            for row in decisions
        )
        or preflight.get("status") != "PASS"
        or preflight.get("formal_launch_authorized") is not True
        or prepared.get("status") != "PREPARED"
        or accounts.get("status") != "PASS"
        or fresh_r0.get("status") != "PASS"
        or fresh_r0.get("formal_outer_launch_authorized") is not True
    ):
        raise RuntimeError("v198 TPC-H recovery authority differs")
    for name, raw in dict(contract.get("source_bindings") or {}).items():
        path = ROOT / str(raw.get("path") or "")
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"v198 recovery source differs: {name}: {path}")
    for name, path in {
        "checkpoint": CHECKPOINT,
        "quarantine": QUARANTINE,
        "source_state": SOURCE_STATE,
        "blocked_gate": BLOCKED_GATE,
        "fresh_r0": FRESH_R0,
        "formal_r0_set": FORMAL_R0_SET,
        "formal_baseline": FORMAL_BASELINE,
    }.items():
        if sha(path) != contract.get("evidence_bindings", {}).get(name, {}).get("sha256"):
            raise RuntimeError(f"v198 recovery evidence differs: {name}")
    return contract, checkpoint


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-prefix", required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,120}", args.run_prefix) is None:
        parser.error("unsafe run prefix")
    contract, checkpoint = validate()
    campaign = HISTORY / args.run_prefix
    run_id = f"{args.run_prefix}_tpch"
    output = HISTORY / run_id
    barrier = Path("/tmp/mysql_async_outer_start") / args.run_prefix / "algorithm_c"
    if campaign.exists() or output.exists() or barrier.exists():
        raise RuntimeError("fresh v198 TPC-H recovery output required")
    campaign.mkdir(parents=True, mode=0o700)
    barrier.mkdir(parents=True, mode=0o700)
    log_path = campaign / "tpch.controller.log"
    log = log_path.open("w")
    environment = os.environ.copy()
    environment["MYSQL_TWO_WORKER_AUTHORITY_ROOT"] = str(AUTHORITY)
    command = [
        sys.executable, str(CONTROLLER),
        "--run-id", run_id,
        "--checkpoint", str(CHECKPOINT),
        "--runtime-preflight", str(AUTHORITY / "transition_preflight.json"),
        "--prepared-binding", str(AUTHORITY / "prepared_binding.json"),
        "--benchmark-accounts", str(AUTHORITY / "benchmark_accounts_binding.json"),
        "--fresh-r0", str(FRESH_R0),
        "--replay-quarantine", str(QUARANTINE),
        "--scene", SCENE,
        "--scene-start-barrier", str(barrier),
        "--formal-baseline", str(FORMAL_BASELINE),
        "--formal-r0-set", str(FORMAL_R0_SET),
    ]
    started = time.monotonic()
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=environment,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        preexec_fn=lambda: os.sched_setaffinity(0, cpus(AFFINITY)),
    )
    log.close()
    manifest = {
        "schema_version": "mysql_algorithm_c_v198_tpch_recovery_launch_v1",
        "release_version": contract["release_version"],
        "status": "RUNNING",
        "created_at": datetime.now().astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene": SCENE,
        "run_id": run_id,
        "pid": process.pid,
        "controller_cpuset": AFFINITY,
        "started_offset_seconds": time.monotonic() - started,
        "log": str(log_path),
        "controller": str(CONTROLLER),
        "controller_sha256": sha(CONTROLLER),
        "contract": str(CONTRACT),
        "contract_sha256": sha(CONTRACT),
        "checkpoint": str(CHECKPOINT),
        "checkpoint_sha256": sha(CHECKPOINT),
        "quarantine": str(QUARANTINE),
        "quarantine_sha256": sha(QUARANTINE),
        "completed_outer_attempts_preserved": int(checkpoint["completed_outer_attempts"]),
        "discarded_incomplete_outer": 4,
        "discarded_incomplete_outer_credit": 0,
        "fresh_search_lineage_preserved": True,
        "fresh_search_reset_on_resume": False,
        "sibling_scene_wait": False,
        "other_scene_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
    }
    atomic(campaign / "launch_manifest.json", manifest)
    print(json.dumps({
        "status": "RUNNING",
        "campaign": str(campaign),
        "run_id": run_id,
        "pid": process.pid,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
