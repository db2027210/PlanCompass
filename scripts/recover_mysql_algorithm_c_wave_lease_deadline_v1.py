#!/usr/bin/env python3
"""Seal a no-reset recovery after the formal wave lease killed a finished batch.

The failed wave completed its four XP resets and its workload clients exited,
but the controller was interrupted before aggregation because the old 1140 s
lease included quiet-gate and reset time.  This recovery is read-only: it
proves zero optimizer-state credit, validates the still-live reset identities,
and emits a recovered XP binding without replacing any data plane.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import reset_mysql_xp_data_planes_v6 as reset
from scripts import run_mysql_four_scene_algorithm_c_v2 as algorithm
from scripts.recover_mysql_algorithm_c_outer15_technical_timeout_v3 import (
    XP_IDS, atomic, load, recorded_client_pids, sha, unit_fields,
)


HISTORY = ROOT / "history/mysql_four_scene_20260809"
EXPECTED_CREDIT = {
    "accepted_rounds": {
        "job_memory": 10, "tpch_sf4_memory": 10,
        "hybench_xp_cpu": 9, "hybench_xp_io": 8,
    },
    "interval_stable_rounds": {
        "job_memory": 1, "tpch_sf4_memory": 2,
        "hybench_xp_cpu": 1, "hybench_xp_io": 0,
    },
    "converged": {
        "job_memory": False, "tpch_sf4_memory": True,
        "hybench_xp_cpu": False, "hybench_xp_io": False,
    },
}


def jsonl_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular non-symlink JSONL required: {path}")
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise RuntimeError(f"JSONL object required: {path}")
            rows.append(value)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--failed-root", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.run_id) is None:
        parser.error("safe run id required")

    failed_root = args.failed_root.expanduser().resolve()
    state_path = failed_root / "run_state.json"
    receipt_path = failed_root / "launch_receipt.json"
    watchdog_path = failed_root / "runtime_external_interference_watchdog.jsonl"
    samples_path = failed_root / "formal_incumbent_baseline/resource_samples.jsonl"
    batch_path = failed_root / "formal_incumbent_baseline/batch_result.json"
    reset_path = (
        failed_root / "xp_resets/v18_pruning_v2_formal_incumbent_baseline"
        / "reset_binding.json"
    )
    state, receipt, reset_binding = load(state_path), load(receipt_path), load(reset_path)
    watchdog_rows = jsonl_rows(watchdog_path)
    deadline_rows = [
        row for row in watchdog_rows
        if row.get("event") == "cross_project_workload_lease_deadline_exceeded"
    ]
    checkpoint_path = Path(str(receipt.get("checkpoint_path") or "")).resolve()
    checkpoint = load(checkpoint_path)
    checkpoint_state = checkpoint.get("reconstructed_state") or {}
    workers = {
        str(row.get("worker_id")): dict(row)
        for row in (reset_binding.get("workers") or [])
    }
    fields = unit_fields(str(receipt.get("systemd_unit") or ""))
    watchdog_fields = unit_fields(str(receipt.get("runtime_watchdog_unit") or ""))
    client_pids = recorded_client_pids(failed_root / "formal_incumbent_baseline")
    live_clients = sorted(pid for pid in client_pids if Path(f"/proc/{pid}").exists())

    deadline = deadline_rows[0] if len(deadline_rows) == 1 else {}
    authority = {
        "history_root": failed_root.parent == HISTORY,
        "failed_status": state.get("status") == "FAILED_CLOSED",
        "failed_stage": state.get("stage") == "FAILED",
        "keyboard_interrupt": state.get("error") == "KeyboardInterrupt: ",
        "outer_boundary": int(state.get("outer_attempt") or -1) == 15,
        "batch_result_absent": not batch_path.exists(),
        "receipt_started": receipt.get("status") == "STARTED",
        "mysql_only": receipt.get("formal_scope") == "mysql_only",
        "comparison_prohibited": receipt.get("cross_engine_comparison_prohibited") is True,
        "checkpoint_hash": receipt.get("checkpoint_sha256") == sha(checkpoint_path),
        "checkpoint_prepared": checkpoint.get("status") == "PREPARED",
        "one_deadline_event": len(deadline_rows) == 1,
        "deadline_phase": deadline.get("coordination_phase") == "measuring",
        "deadline_stage": deadline.get("runtime_stage") == "FORMAL_INCUMBENT_BASELINE_MEASURING",
        "deadline_action": deadline.get("action") == "SIGINT_then_bounded_systemd_stop",
        "deadline_budget": abs(float(deadline.get("maximum_wave_lease_seconds") or 0) - 1140.0) <= 1e-9,
        "deadline_elapsed": 1140.0 <= float(deadline.get("lease_elapsed_seconds") or 0) < 1160.0,
        "formal_inactive": fields.get("ActiveState") == "inactive",
        "formal_dead": fields.get("SubState") == "dead",
        "formal_pid_zero": int(fields.get("MainPID") or 0) == 0,
        "watchdog_terminal": watchdog_fields.get("ActiveState") in {"inactive", "failed"},
        "watchdog_pid_zero": int(watchdog_fields.get("MainPID") or 0) == 0,
        "client_pids_gone": not live_clients,
        "reset_schema": reset_binding.get("schema_version") == "mysql_four_scene_algorithm_c_xp_point_reset_v1",
        "reset_pass": reset_binding.get("status") == "PASS",
        "reset_worker_ids": set(reset_binding.get("reset_worker_ids") or []) == XP_IDS,
        "worker_set": set(workers) == XP_IDS,
        "workers_ready": all(row.get("ready") is True for row in workers.values()),
        "freeze_hash": reset_binding.get("freeze_sha256") == state.get("freeze_sha256"),
        "samples_regular": samples_path.is_file() and not samples_path.is_symlink(),
    }
    failed_authority = sorted(key for key, passed in authority.items() if not passed)
    if failed_authority:
        raise RuntimeError(
            "wave-lease deadline recovery authority differs: "
            + ",".join(failed_authority)
        )

    for key, expected in EXPECTED_CREDIT.items():
        if state.get(key) != expected or checkpoint_state.get(key) != expected:
            raise RuntimeError(f"optimizer state credit differs for {key}")
    for key in (
        "acceptance_tier", "consecutive_failed_rounds", "rejected_outer_attempts",
        "stable_intervals", "full_plan_replay_history_v2",
    ):
        if state.get(key) != checkpoint_state.get(key):
            raise RuntimeError(f"optimizer state differs from checkpoint for {key}")

    live_readback: dict[str, dict[str, Any]] = {}
    for worker_id in sorted(XP_IDS):
        worker = workers[worker_id]
        inspected = reset._docker_json("inspect", str(worker["container"]))
        host = inspected.get("HostConfig") or {}
        state_row = inspected.get("State") or {}
        resources = worker.get("docker_host_resources") or {}
        current = {
            "container_id": inspected.get("Id"),
            "container_pid": int(state_row.get("Pid") or 0),
            "running": state_row.get("Running") is True,
            "cpuset_cpus": host.get("CpusetCpus"),
            "cpuset_mems": host.get("CpusetMems"),
            "memory_bytes": int(host.get("Memory") or 0),
            "memory_swap_bytes": int(host.get("MemorySwap") or 0),
            "nano_cpus": int(host.get("NanoCpus") or 0),
            "pids_limit": int(host.get("PidsLimit") or 0),
            "active_client_sessions": algorithm.xp_active_client_session_count(worker),
        }
        if (
            current["container_id"] != worker.get("container_id")
            or current["running"] is not True
            or current["cpuset_cpus"] != resources.get("CpusetCpus")
            or current["cpuset_mems"] != resources.get("CpusetMems")
            or current["memory_bytes"] != int(resources.get("Memory") or 0)
            or current["nano_cpus"] != int(resources.get("NanoCpus") or 0)
            or current["pids_limit"] != int(resources.get("PidsLimit") or 0)
            or current["active_client_sessions"] != 0
        ):
            raise RuntimeError(f"{worker_id} live reset identity/resource readback differs")
        live_readback[worker_id] = current

    output = HISTORY / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh recovery output exists: {output}")
    output.mkdir(parents=True, mode=0o700)
    binding = output / "recovered_xp_binding.json"
    atomic(binding, {
        "schema_version": "mysql_algorithm_c_wave_lease_deadline_recovery_v1",
        "status": "PASS",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "failed_state": {"path": str(state_path), "sha256": sha(state_path)},
        "failed_launch_receipt": {"path": str(receipt_path), "sha256": sha(receipt_path)},
        "runtime_watchdog": {"path": str(watchdog_path), "sha256": sha(watchdog_path)},
        "deadline_event": deadline,
        "resource_samples": {"path": str(samples_path), "sha256": sha(samples_path)},
        "missing_batch_result": str(batch_path),
        "checkpoint": {"path": str(checkpoint_path), "sha256": sha(checkpoint_path)},
        "reset_binding": {"path": str(reset_path), "sha256": sha(reset_path)},
        "terminal_unit_readback": fields,
        "terminal_watchdog_unit_readback": watchdog_fields,
        "freeze_path": state.get("freeze_path"),
        "freeze_sha256": state.get("freeze_sha256"),
        "recorded_client_pids": sorted(client_pids),
        "live_recorded_client_pids": [],
        "reset_worker_ids": sorted(XP_IDS),
        "workers": [workers[worker_id] for worker_id in sorted(XP_IDS)],
        "live_worker_readback": live_readback,
        "workload_started": False,
        "formal_state_credit_increment": 0,
        "accepted_rounds_mutation": False,
        "rejected_rounds_mutation": False,
        "interval_convergence_mutation": False,
        "physical_data_plane_reset_performed": False,
        "external_container_mutation": False,
        "recovery_source_path": str(Path(__file__).resolve()),
        "recovery_source_sha256": sha(Path(__file__).resolve()),
    })
    print(json.dumps({
        "status": "PASS", "binding": str(binding),
        "physical_data_plane_reset_performed": False,
        "formal_state_credit_increment": 0,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
