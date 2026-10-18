#!/usr/bin/env python3
"""Run all four v18 scenes concurrently at migrated incumbent parameters, zero credit."""

from __future__ import annotations

import argparse
import fcntl
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_four_scene_algorithm_c_v2 as alg


HISTORY = ROOT / "history/mysql_four_scene_20260809"
FREEZE = ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v18/four_scene_effective_resource_successor.json"
PREPARED = HISTORY / "four_scene_live_rebind_v18_pilot_20260813T1605p0800/prepared_binding.json"
ACCOUNTS = HISTORY / "four_scene_benchmark_accounts_v18_pilot_20260813T1610p0800/benchmark_accounts_binding.json"
CHECKPOINT = HISTORY / "algorithm_c_outer4_checkpoint_v2_optimized_20260812/prepared_binding.json"
INTERFERENCE = ROOT / "preflight/mysql_four_scene_20260809/runtime_preflight_v18_attempt1_20260813T1530p0800.json"
CALIBRATION_RELEASE = ROOT / "artifacts/mysql_xp_io_v18_calibration_release_20260813/release.json"
SELECTED = {
    "job_memory": "job_w1",
    "tpch_sf4_memory": "tpch_w1",
    "hybench_xp_cpu": "xp_cpu_w1",
    "hybench_xp_io": "xp_io_w1",
}


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.run_id) is None:
        parser.error("unsafe run id")
    output = HISTORY / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh output exists: {output}")
    freeze, prepared, accounts, checkpoint, interference, release = map(
        load, (FREEZE, PREPARED, ACCOUNTS, CHECKPOINT, INTERFERENCE, CALIBRATION_RELEASE)
    )
    if (
        freeze.get("schema_version") != "mysql_disk2_four_scene_two_worker_effective_resource_successor_v18"
        or prepared.get("status") != "PREPARED"
        or prepared.get("freeze_sha256") != alg.sha(FREEZE)
        or prepared.get("preflight_formal_launch_authorized") is not False
        or accounts.get("status") != "PASS"
        or accounts.get("freeze_sha256") != alg.sha(FREEZE)
        or accounts.get("prepared_binding_sha256") != alg.sha(PREPARED)
        or interference.get("status") != "BLOCKED_FAIL_CLOSED"
        or interference.get("freeze_sha256") != alg.sha(FREEZE)
        or release.get("status") != "RELEASED"
        or release.get("freeze", {}).get("sha256") != alg.sha(FREEZE)
    ):
        raise RuntimeError("v18 incumbent pilot authority differs")
    frozen = {row["worker_id"]: row for row in freeze["workers"]}
    adopted = {row["worker_id"]: row for row in prepared["workers"]}
    account_rows = {row["worker_id"]: row for row in accounts["workers"]}
    workers = {
        worker_id: {**frozen[worker_id], **adopted[worker_id], **account_rows[worker_id]}
        for worker_id in alg.WORKER_IDS
    }
    for worker_id, worker in workers.items():
        if worker["container"] != alg.reset.XP_CONTAINER_ALLOWLIST.get(worker_id, worker["container"]):
            raise RuntimeError(f"{worker_id} container outside allowlist")
        if "0708" in worker["container"]:
            raise RuntimeError(f"{worker_id} forbidden 0708 container")
    params = checkpoint["reconstructed_state"]["params_by_scene"]
    assignments = {
        worker_id: {"rank": 0, "params": params[scene]}
        for scene, worker_id in SELECTED.items()
    }
    output.mkdir(parents=True, mode=0o700)
    state_path = output / "pilot_state.json"
    marker = {
        "schema_version": "mysql_four_scene_v18_incumbent_pilot_v1",
        "status": "RUNNING",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "formal_evidence_eligible": False,
        "accepted_state_credit_prohibited": True,
        "accepted_rounds_credit": 0,
        "interference_evidence_path": str(INTERFERENCE),
        "interference_evidence_sha256": alg.sha(INTERFERENCE),
        "freeze_path": str(FREEZE),
        "freeze_sha256": alg.sha(FREEZE),
        "calibration_release_path": str(CALIBRATION_RELEASE),
        "calibration_release_sha256": alg.sha(CALIBRATION_RELEASE),
        "assignments": assignments,
        "started_at": alg.now(),
    }
    alg.atomic(state_path, marker)
    lock = Path("/tmp/mysql_four_scene_algorithm_c_v1.lock").open("w")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        raise RuntimeError("another four-scene controller owns the lock") from error
    alg.FREEZE = FREEZE
    try:
        measurements = alg.measure_batch(
            freeze, workers, assignments, output / "incumbent_replay",
            f"{args.run_id}_incumbent", state_path=state_path,
            stage_prefix="V18_INCUMBENT_PILOT",
        )
        batch = load(output / "incumbent_replay/batch_result.json")
        marker.update({
            "status": "PASS" if batch.get("status") == "PASS" else "FAILED_CLOSED",
            "completed_at": alg.now(),
            "elapsed_seconds": batch.get("elapsed_seconds"),
            "hard_gate_passed": {key: row.get("hard_gate_passed") for key, row in measurements.items()},
            "accepted_rounds_credit": 0,
        })
        alg.atomic(state_path, marker)
        return 0 if marker["status"] == "PASS" else 3
    except BaseException as error:
        marker.update({"status": "FAILED_CLOSED", "failed_at": alg.now(), "error": f"{type(error).__name__}: {error}"})
        alg.atomic(state_path, marker)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
