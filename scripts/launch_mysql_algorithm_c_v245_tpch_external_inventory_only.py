#!/usr/bin/env python3
"""Resume TPC-H while treating external processes as recorded inventory."""

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

from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v19 as checkpoint_v19
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v136 as controller


HISTORY = ROOT / "history/mysql_four_scene_20260809"
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v136.py"
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_tpch_external_inventory_only_20260929_v245/"
    "tpch_external_inventory_only_contract.json"
)
CHECKPOINT = HISTORY / (
    "c_v244_tpch_v243_outer10_timeout_checkpoint_20260929/prepared_binding.json"
)
QUARANTINE = HISTORY / (
    "c_v244_tpch_v243_outer10_timeout_quarantine_20260929/replay_quarantine.json"
)
AUTHORITY = HISTORY / "c_v156_candidate_authority_v36_20260923/authority"
FRESH_R0 = HISTORY / "c_v156_candidate_fresh_r0_v36_20260923/fresh_r0_binding.json"
FORMAL_R0_SET = HISTORY / "c_v152_formal_r0_set_v36_20260923/formal_r0_set.json"
FORMAL_BASELINE = HISTORY / (
    "c_v148_paper_baseline_calibration_20260923_tpch/"
    "formal_incumbent_baseline/batch_result.json"
)
FAILED_V244 = HISTORY / "c_v244_tpch_fifth_timeout_recovery_20260929_tpch/run_state.json"
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
    checkpoint = checkpoint_v19.verify_checkpoint(CHECKPOINT)
    quarantine = load(QUARANTINE)
    failed = load(FAILED_V244)
    if (
        contract.get("release_version") != "v245-tpch-external-inventory-only"
        or contract.get("external_processes_inventory_only") is not True
        or contract.get("external_processes_can_block") is not False
        or contract.get("strict_external_noninterference_evidence_eligible") is not False
        or contract.get("target_mysql_local_resource_proof_required") is not True
        or int(checkpoint.get("completed_outer_attempts") or -1) != 10
        or int(checkpoint["reconstructed_state"]["accepted_rounds"][SCENE]) != 9
        or int(checkpoint["reconstructed_state"]["rejected_outer_attempts"][SCENE]) != 1
        or int((quarantine.get("permanent_invalid_counts_by_scene") or {}).get(SCENE, -1)) != 18
        or int((quarantine.get("soft_replayed_counts_by_scene") or {}).get(SCENE, -1)) != 9
        or failed.get("status") != "FAILED_CLOSED"
        or int(failed.get("outer_attempt") or -1) != 10
        or not str(failed.get("error") or "").startswith(
            "RuntimeError: fresh R0 point non-interference gate blocked:"
        )
    ):
        raise RuntimeError("v245 TPC-H recovery authority differs")
    for raw in dict(contract.get("evidence_bindings") or {}).values():
        path = ROOT / str(raw.get("path") or "")
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"v245 recovery evidence differs: {path}")
    return contract, checkpoint


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-prefix", required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,120}", args.run_prefix) is None:
        parser.error("unsafe run prefix")
    contract, _checkpoint = validate()
    campaign = HISTORY / args.run_prefix
    run_id = f"{args.run_prefix}_tpch"
    output = HISTORY / run_id
    barrier = Path("/tmp/mysql_async_outer_start") / args.run_prefix / "algorithm_c"
    if campaign.exists() or output.exists() or barrier.exists():
        raise RuntimeError("fresh v245 TPC-H recovery output required")
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
    process = subprocess.Popen(
        command, cwd=ROOT, env=environment,
        stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        preexec_fn=lambda: os.sched_setaffinity(0, cpus(AFFINITY)),
    )
    log.close()
    manifest = {
        "schema_version": "mysql_algorithm_c_v245_tpch_external_inventory_only_launch_v1",
        "release_version": contract["release_version"],
        "status": "RUNNING",
        "created_at": datetime.now().astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "formal_evidence_eligible": False,
        "strict_external_noninterference_evidence_eligible": False,
        "scene": SCENE, "run_id": run_id, "pid": process.pid,
        "controller_cpuset": AFFINITY, "log": str(log_path),
        "controller": str(CONTROLLER), "controller_sha256": sha(CONTROLLER),
        "contract": str(CONTRACT), "contract_sha256": sha(CONTRACT),
        "checkpoint": str(CHECKPOINT), "checkpoint_sha256": sha(CHECKPOINT),
        "quarantine": str(QUARANTINE), "quarantine_sha256": sha(QUARANTINE),
        "completed_outer_attempts_preserved": 10,
        "accepted_decisions_preserved": 9,
        "rejected_decisions_preserved": 1,
        "technical_timeout_candidate_count": 18,
        "permanent_invalid_candidate_count": 18,
        "soft_replayed_candidate_count": 9,
        "quarantined_candidate_count": 27,
        "technical_timeout_state_credit": 0,
        "technical_timeout_rejected_round_increment": 0,
        "bounded_tpch_cleanup_retry": True,
        "tpch_cleanup_attempt_limit": 3,
        "tpch_cleanup_readback_after_error": True,
        "tpch_cleanup_lock_wait_timeout_seconds": 5,
        "permanent_invalid_exclusion_all_candidate_paths": True,
        "legacy_post_target_cap_retired": True,
        "fresh_search_lineage_preserved": True,
        "fresh_search_reset_on_resume": False,
        "current_pg_runtime_release": "v60",
        "external_process_policy_release": "v245-external-process-inventory-only",
        "external_processes_inventory_only": True,
        "external_processes_can_block": False,
        "external_quiet_window_required": False,
        "target_mysql_local_resource_proof_required": True,
        "accepted_state_credit_prohibited": False,
        "strict_external_noninterference_results_must_not_be_merged": True,
        "sibling_scene_wait": False,
        "other_scene_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
    }
    atomic(campaign / "launch_manifest.json", manifest)

    state_path = output / "run_state.json"
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        if state_path.is_file():
            state = load(state_path)
            point = state.get("fresh_r0_point_gate") or {}
            if (
                state.get("status") == "RUNNING"
                and int(state.get("outer_attempt") or -1) == 10
                and int((state.get("accepted_rounds") or {}).get(SCENE, -1)) == 9
                and int((state.get("rejected_outer_attempts") or {}).get(SCENE, -1)) == 1
                and int((state.get("permanent_invalid_quarantine_counts") or {}).get(SCENE, -1)) == 18
                and int((state.get("soft_replay_quarantine_counts") or {}).get(SCENE, -1)) == 9
                and state.get("external_processes_inventory_only") is True
                and state.get("formal_evidence_eligible") is False
                and point.get("external_processes_inventory_only") is True
                and (point.get("target_mysql_local_resource_proof") or {}).get("passed") is True
            ):
                print(json.dumps({
                    "status": "RUNNING", "campaign": str(campaign),
                    "run_id": run_id, "pid": process.pid,
                    "resumed_after_outer": 10,
                    "accepted_decisions": 9,
                    "external_processes_inventory_only": True,
                    "target_mysql_local_resource_proof": "PASS",
                }, sort_keys=True))
                return 0
            if state.get("status") == "FAILED_CLOSED":
                break
        time.sleep(2)
    tail = log_path.read_text(encoding="utf-8", errors="replace")[-8000:]
    raise RuntimeError(
        f"v245 recovery did not reach RUNNING boundary; rc={process.poll()}; tail={tail}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
