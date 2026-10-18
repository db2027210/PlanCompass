#!/usr/bin/env python3
"""Resume JOB Algorithm C after a zero-credit dynamic-gate failure."""

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

from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v15 as checkpoint_v15
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v114 as controller


HISTORY = ROOT / "history/mysql_four_scene_20260809"
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v114.py"
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_job_dynamic_gate_recovery_20260929_v218/"
    "job_dynamic_gate_recovery_contract.json"
)
CHECKPOINT = HISTORY / (
    "c_v218_job_v213b_outer19_dynamic_gate_checkpoint_20260929/prepared_binding.json"
)
QUARANTINE = HISTORY / (
    "c_v218_job_v213b_outer19_dynamic_gate_quarantine_20260929/replay_quarantine.json"
)
SOURCE_ROOT = HISTORY / "c_v213b_job_legacy_cap_recovery_20260929_job"
SOURCE_STATE = SOURCE_ROOT / "run_state.json"
SOURCE_DECISION = SOURCE_ROOT / "outer_019/job_memory.decision.json"
AUTHORITY = HISTORY / "c_v156_candidate_authority_v36_20260923/authority"
FRESH_R0 = HISTORY / "c_v156_candidate_fresh_r0_v36_20260923/fresh_r0_binding.json"
FORMAL_R0_SET = HISTORY / "c_v152_formal_r0_set_v36_20260923/formal_r0_set.json"
FORMAL_BASELINE = HISTORY / (
    "c_v150_paper_baseline_calibration_20260923_job/"
    "formal_incumbent_baseline/batch_result.json"
)
AFFINITY = "0-7,256-263"
SCENE = "job_memory"


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
    checkpoint = checkpoint_v15.verify_checkpoint(CHECKPOINT)
    quarantine = load(QUARANTINE)
    source = load(SOURCE_STATE)
    decision = load(SOURCE_DECISION)
    preflight = load(AUTHORITY / "transition_preflight.json")
    prepared = load(AUTHORITY / "prepared_binding.json")
    accounts = load(AUTHORITY / "benchmark_accounts_binding.json")
    fresh_r0 = load(FRESH_R0)
    if (
        contract.get("release_version") != "v218-job-dynamic-gate-recovery"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("completed_outer_attempts_preserved") != 19
        or contract.get("actual_accepted_decisions_restored") != 17
        or contract.get("rejected_decisions_preserved") != 2
        or contract.get("legacy_post_target_cap_retired") is not True
        or contract.get("fresh_search_reset_on_resume") is not False
        or contract.get("workload_or_candidate_policy_changed") is not False
        or contract.get("resource_envelope_changed") is not False
        or checkpoint.get("scene") != SCENE
        or int(checkpoint.get("completed_outer_attempts") or 0) != 19
        or int(checkpoint["reconstructed_state"]["accepted_rounds"][SCENE]) != 17
        or int(checkpoint["reconstructed_state"]["rejected_outer_attempts"][SCENE]) != 2
        or checkpoint["reconstructed_state"].get("post_target_stability_confirmation_counts") != {}
        or checkpoint.get("scene_resume_audit", {}).get("incomplete_outer_credit") != 0
        or quarantine.get("status") != "PASS"
        or int(quarantine.get("resume_checkpoint_outer_attempt") or 0) != 19
        or quarantine.get("evidence", {}).get("checkpoint", {}).get("sha256") != sha(CHECKPOINT)
        or source.get("status") != "FAILED_CLOSED"
        or source.get("error") != "RuntimeError: dynamic non-interference gate failed closed"
        or int(source.get("outer_attempt") or 0) != 19
        or source.get("post_target_stability_confirmation_counts") != {}
        or decision.get("accepted") is not True
        or int(decision.get("accepted_state_credit_increment", -1)) != 1
        or Path(str(source.get("formal_incumbent_baseline", {}).get("path") or "")).resolve() != FORMAL_BASELINE.resolve()
        or source.get("formal_incumbent_baseline", {}).get("sha256") != sha(FORMAL_BASELINE)
        or preflight.get("status") != "PASS"
        or preflight.get("formal_launch_authorized") is not True
        or prepared.get("status") != "PREPARED"
        or accounts.get("status") != "PASS"
        or fresh_r0.get("status") != "PASS"
        or fresh_r0.get("formal_outer_launch_authorized") is not True
    ):
        raise RuntimeError("v218 JOB dynamic-gate recovery authority differs")
    for name, raw in dict(contract.get("evidence_bindings") or {}).items():
        path = Path(str(raw.get("path") or ""))
        if not path.is_absolute():
            path = ROOT / path
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"v218 recovery evidence differs: {name}: {path}")
    return contract, checkpoint


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-prefix", required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,120}", args.run_prefix) is None:
        parser.error("unsafe run prefix")
    contract, checkpoint = validate()
    campaign = HISTORY / args.run_prefix
    run_id = f"{args.run_prefix}_job"
    output = HISTORY / run_id
    barrier = Path("/tmp/mysql_async_outer_start") / args.run_prefix / "algorithm_c"
    if campaign.exists() or output.exists() or barrier.exists():
        raise RuntimeError("fresh v218 JOB recovery output required")
    campaign.mkdir(parents=True, mode=0o700)
    barrier.mkdir(parents=True, mode=0o700)
    log_path = campaign / "job.controller.log"
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
        "schema_version": "mysql_algorithm_c_v218_job_dynamic_gate_recovery_launch_v1",
        "release_version": contract["release_version"],
        "status": "RUNNING",
        "created_at": datetime.now().astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene": SCENE, "run_id": run_id, "pid": process.pid,
        "controller_cpuset": AFFINITY, "log": str(log_path),
        "controller": str(CONTROLLER), "controller_sha256": sha(CONTROLLER),
        "contract": str(CONTRACT), "contract_sha256": sha(CONTRACT),
        "checkpoint": str(CHECKPOINT), "checkpoint_sha256": sha(CHECKPOINT),
        "quarantine": str(QUARANTINE), "quarantine_sha256": sha(QUARANTINE),
        "completed_outer_attempts_preserved": 19,
        "actual_accepted_decisions_restored": 17,
        "rejected_decisions_preserved": 2,
        "legacy_post_target_cap_retired": True,
        "fresh_search_lineage_preserved": True,
        "fresh_search_reset_on_resume": False,
        "current_pg_runtime_release": "v58",
        "dynamic_gate_release": "v210",
        "sibling_scene_wait": False,
        "other_scene_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
    }
    atomic(campaign / "launch_manifest.json", manifest)

    state_path = output / "run_state.json"
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        if state_path.is_file():
            state = load(state_path)
            if (
                state.get("status") == "RUNNING"
                and int(state.get("outer_attempt") or -1) == 19
                and int((state.get("accepted_rounds") or {}).get(SCENE, -1)) == 17
                and int((state.get("rejected_outer_attempts") or {}).get(SCENE, -1)) == 2
                and state.get("post_target_stability_confirmation_counts") == {}
                and (state.get("actual_width_credit_release") or {}).get("release_version") == "v218"
            ):
                print(json.dumps({
                    "status": "RUNNING", "campaign": str(campaign),
                    "run_id": run_id, "pid": process.pid,
                    "resumed_after_outer": 19,
                    "actual_accepted_decisions": 17,
                }, sort_keys=True))
                return 0
            if state.get("status") == "FAILED_CLOSED":
                break
        time.sleep(2)
    tail = log_path.read_text(encoding="utf-8", errors="replace")[-8000:]
    raise RuntimeError(
        f"v218 recovery did not reach RUNNING boundary; rc={process.poll()}; tail={tail}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
