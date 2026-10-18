#!/usr/bin/env python3
"""Seal live XP binding after the v22 post-baseline early termination."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import reset_mysql_xp_data_planes_v6 as reset
from scripts import run_mysql_four_scene_algorithm_c_v2 as algorithm
from scripts.recover_mysql_algorithm_c_outer15_technical_timeout_v3 import (
    XP_IDS, atomic, load, recorded_client_pids, sha, unit_fields,
)


HISTORY = ROOT / "history/mysql_four_scene_20260809"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.run_id) is None:
        parser.error("safe run id required")
    source = args.source_root.expanduser().resolve()
    state_path, receipt_path = source / "run_state.json", source / "launch_receipt.json"
    baseline_path = source / "formal_incumbent_baseline/batch_result.json"
    reset_path = (
        source / "xp_resets/v18_pruning_v2_formal_incumbent_baseline"
        / "reset_binding.json"
    )
    state, receipt, baseline, binding = (
        load(state_path), load(receipt_path), load(baseline_path), load(reset_path)
    )
    checkpoint_path = Path(str(receipt.get("checkpoint_path") or "")).resolve()
    checkpoint = load(checkpoint_path)
    reconstructed = checkpoint.get("reconstructed_state") or {}
    workers = {str(row.get("worker_id")): dict(row) for row in binding.get("workers") or []}
    measurements = baseline.get("measurements") or {}
    fields = unit_fields(str(receipt.get("systemd_unit") or ""))
    watchdog_fields = unit_fields(str(receipt.get("runtime_watchdog_unit") or ""))
    client_pids = recorded_client_pids(source / "formal_incumbent_baseline")
    live_clients = sorted(pid for pid in client_pids if Path(f"/proc/{pid}").exists())
    credit_fields = (
        "accepted_rounds", "interval_stable_rounds", "converged",
        "rejected_outer_attempts", "acceptance_tier", "consecutive_failed_rounds",
        "stable_intervals", "full_plan_replay_history_v2",
    )
    authority = {
        "history_root": source.parent == HISTORY,
        "terminal_status": state.get("status") == "NON_CONVERGED",
        "terminal_stage": state.get("stage") == "FINISHED",
        "no_error": state.get("error") in (None, ""),
        "outer_unchanged": int(state.get("outer_attempt") or -1) == 15,
        "termination_exact": state.get("termination_audit") == {
            "rejected_retry_budget_exhausted": [],
            "accepted_target_reached_without_convergence": ["job_memory"],
        },
        "baseline_pass": baseline.get("status") == "PASS",
        "baseline_failures_empty": baseline.get("failures") in ([], None),
        "baseline_hash": (state.get("formal_incumbent_baseline") or {}).get("sha256") == sha(baseline_path),
        "eight_measurements": set(measurements) == {
            "job_w1", "job_w2", "tpch_w1", "tpch_w2", *XP_IDS,
        },
        "measurement_gates": all(
            row.get("hard_gate_passed") is True and row.get("coverage_complete") is True
            for row in measurements.values()
        ),
        "no_outer16_artifacts": not (source / "outer_016").exists(),
        "controller_terminal": fields.get("ActiveState") == "failed" and int(fields.get("MainPID") or 0) == 0,
        "watchdog_terminal": watchdog_fields.get("ActiveState") in {"inactive", "failed"} and int(watchdog_fields.get("MainPID") or 0) == 0,
        "clients_gone": not live_clients,
        "reset_pass": binding.get("status") == "PASS",
        "reset_schema": binding.get("schema_version") == "mysql_four_scene_algorithm_c_xp_point_reset_v1",
        "worker_set": set(workers) == XP_IDS,
        "workers_ready": all(row.get("ready") is True for row in workers.values()),
        "freeze_hash": binding.get("freeze_sha256") == state.get("freeze_sha256"),
        "checkpoint_hash": receipt.get("checkpoint_sha256") == sha(checkpoint_path),
        "credit_unchanged": all(state.get(key) == reconstructed.get(key) for key in credit_fields),
    }
    failed = sorted(key for key, passed in authority.items() if not passed)
    if failed:
        raise RuntimeError("post-baseline recovery authority differs: " + ",".join(failed))

    live_readback = {}
    for worker_id in sorted(XP_IDS):
        worker = workers[worker_id]
        inspected = reset._docker_json("inspect", str(worker["container"]))
        host, container_state = inspected.get("HostConfig") or {}, inspected.get("State") or {}
        expected = worker.get("docker_host_resources") or {}
        row = {
            "container_id": inspected.get("Id"),
            "container_pid": int(container_state.get("Pid") or 0),
            "running": container_state.get("Running") is True,
            "cpuset_cpus": host.get("CpusetCpus"),
            "cpuset_mems": host.get("CpusetMems"),
            "memory_bytes": int(host.get("Memory") or 0),
            "nano_cpus": int(host.get("NanoCpus") or 0),
            "pids_limit": int(host.get("PidsLimit") or 0),
            "active_client_sessions": algorithm.xp_active_client_session_count(worker),
        }
        if (
            row["container_id"] != worker.get("container_id")
            or row["running"] is not True
            or row["cpuset_cpus"] != expected.get("CpusetCpus")
            or row["cpuset_mems"] != expected.get("CpusetMems")
            or row["memory_bytes"] != int(expected.get("Memory") or 0)
            or row["nano_cpus"] != int(expected.get("NanoCpus") or 0)
            or row["pids_limit"] != int(expected.get("PidsLimit") or 0)
            or row["active_client_sessions"] != 0
        ):
            raise RuntimeError(f"{worker_id} live identity/resource readback differs")
        live_readback[worker_id] = row

    output = HISTORY / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh recovery output exists: {output}")
    output.mkdir(parents=True, mode=0o700)
    recovered = output / "recovered_xp_binding.json"
    atomic(recovered, {
        "schema_version": "mysql_algorithm_c_post_baseline_early_termination_recovery_v1",
        "status": "PASS", "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "source_state": {"path": str(state_path), "sha256": sha(state_path)},
        "source_launch_receipt": {"path": str(receipt_path), "sha256": sha(receipt_path)},
        "passed_formal_baseline": {"path": str(baseline_path), "sha256": sha(baseline_path)},
        "checkpoint": {"path": str(checkpoint_path), "sha256": sha(checkpoint_path)},
        "reset_binding": {"path": str(reset_path), "sha256": sha(reset_path)},
        "terminal_unit_readback": fields,
        "terminal_watchdog_unit_readback": watchdog_fields,
        "freeze_path": state.get("freeze_path"), "freeze_sha256": state.get("freeze_sha256"),
        "recorded_client_pids": sorted(client_pids), "live_recorded_client_pids": [],
        "reset_worker_ids": sorted(XP_IDS), "workers": [workers[w] for w in sorted(XP_IDS)],
        "live_worker_readback": live_readback,
        "workload_started": False, "formal_state_credit_increment": 0,
        "accepted_rounds_mutation": False, "rejected_rounds_mutation": False,
        "interval_convergence_mutation": False,
        "physical_data_plane_reset_performed": False,
        "external_container_mutation": False,
        "recovery_source_path": str(Path(__file__).resolve()),
        "recovery_source_sha256": sha(Path(__file__).resolve()),
    })
    print(recovered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
