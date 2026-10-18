#!/usr/bin/env python3
"""Recover four XP planes after an exact Outer-16 baseline timeout."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import provision_mysql_four_scene_benchmark_users_v5 as users
from scripts import reset_mysql_xp_data_planes_v6 as reset
from scripts import run_mysql_four_scene_algorithm_c_v2 as algorithm
from scripts.recover_mysql_algorithm_c_outer15_technical_timeout_v3 import (
    XP_IDS, atomic, load, recorded_client_pids, sha, unit_fields,
)


EXPECTED_ACCEPTED = {
    "job_memory": 10, "tpch_sf4_memory": 10,
    "hybench_xp_cpu": 10, "hybench_xp_io": 9,
}
EXPECTED_STABLE = {
    "job_memory": 1, "tpch_sf4_memory": 2,
    "hybench_xp_cpu": 1, "hybench_xp_io": 1,
}


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
    batch_path = failed_root / "formal_incumbent_baseline/batch_result.json"
    state, receipt, batch = load(state_path), load(receipt_path), load(batch_path)
    prepared_path = Path(str(state.get("prepared_binding_path") or "")).resolve()
    prepared = load(prepared_path)
    freeze_path = Path(str(state.get("freeze_path") or "")).resolve()
    freeze = load(freeze_path)
    workers = {
        str(row["worker_id"]): dict(row) for row in (prepared.get("workers") or [])
        if row.get("worker_id") in XP_IDS
    }
    frozen = {
        str(row["worker_id"]): dict(row) for row in (freeze.get("workers") or [])
        if row.get("worker_id") in XP_IDS
    }
    fields = unit_fields(str(receipt.get("systemd_unit") or ""))
    client_pids = recorded_client_pids(failed_root / "formal_incumbent_baseline")
    live_clients = sorted(pid for pid in client_pids if Path(f"/proc/{pid}").exists())
    timeout_tasks = {
        str(row.get("task")) for row in (batch.get("failures") or [])
        if str(row.get("error") or "").startswith("TimeoutExpired:")
        and "timed out after 900 seconds" in str(row.get("error") or "")
    }
    measurements = batch.get("measurements") or {}
    if (
        failed_root.parent != ROOT / "history/mysql_four_scene_20260809"
        or state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or state.get("error") != "RuntimeError: formal incumbent baseline failed closed"
        or int(state.get("outer_attempt") or -1) != 15
        or state.get("accepted_rounds") != EXPECTED_ACCEPTED
        or state.get("interval_stable_rounds") != EXPECTED_STABLE
        or (failed_root / "outer_016").exists()
        or batch.get("status") != "FAILED_CLOSED"
        or not (900 <= float(batch.get("elapsed_seconds") or 0) <= 920)
        or timeout_tasks != {"pair_1", "pair_2", *XP_IDS}
        or set(measurements) != {
            "job_w1", "job_w2", "tpch_w1", "tpch_w2", *XP_IDS,
        }
        or any(row.get("coverage_complete") is not False for row in measurements.values())
        or fields.get("ActiveState") not in {"inactive", "failed"}
        or int(fields.get("MainPID") or 0) != 0
        or prepared.get("status") != "PREPARED"
        or prepared.get("freeze_sha256") != sha(freeze_path)
        or state.get("freeze_sha256") != sha(freeze_path)
        or set(workers) != XP_IDS or set(frozen) != XP_IDS
        or live_clients
    ):
        raise RuntimeError("Outer-16 baseline-timeout recovery authority differs")

    output = ROOT / "history/mysql_four_scene_20260809" / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh recovery output exists: {output}")
    stage = output / "xp_resets/outer16_baseline_timeout_recovery_v1"
    stage.mkdir(parents=True, mode=0o700)
    quarantine = (
        Path(str(freeze["storage"]["runtime_root"]))
        / "quarantine" / output.name / stage.name
    )
    started = time.monotonic()
    recovered = {}
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="baseline-xp") as pool:
        futures = {
            pool.submit(
                reset._prepare_fresh_xp, freeze, frozen[worker_id],
                workers[worker_id], quarantine, stage, False, "formal_point",
                True, False, False, False,
            ): worker_id for worker_id in sorted(XP_IDS)
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            recovered[worker_id] = future.result()
            print(json.dumps({"worker_id": worker_id, "reset_complete": True,
                              "elapsed_seconds": time.monotonic() - started}), flush=True)

    account_root = stage / "accounts"
    account_root.mkdir()
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="baseline-users") as pool:
        futures = {
            pool.submit(users._run_worker, recovered[w], account_root, True, False): w
            for w in sorted(XP_IDS)
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            recovered[worker_id].update(future.result())
    if any(
        row.get("ready") is not True
        or algorithm.xp_active_client_session_count(row) != 0
        for row in recovered.values()
    ):
        raise RuntimeError("recovered XP readback differs")

    binding = output / "recovered_xp_binding.json"
    atomic(binding, {
        "schema_version": "mysql_algorithm_c_outer16_baseline_timeout_recovery_v1",
        "status": "PASS",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "failed_state": {"path": str(state_path), "sha256": sha(state_path)},
        "failed_launch_receipt": {"path": str(receipt_path), "sha256": sha(receipt_path)},
        "technical_timeout_batch": {"path": str(batch_path), "sha256": sha(batch_path),
                                     "technical_timeout_excluded": True,
                                     "formal_state_credit_increment": 0},
        "prepared_binding": {"path": str(prepared_path), "sha256": sha(prepared_path)},
        "freeze_path": str(freeze_path), "freeze_sha256": sha(freeze_path),
        "recorded_client_pids": sorted(client_pids), "live_recorded_client_pids": [],
        "reset_worker_ids": sorted(XP_IDS),
        "workers": [recovered[w] for w in sorted(XP_IDS)],
        "workload_started": False, "accepted_rounds_mutation": False,
        "rejected_rounds_mutation": False, "interval_convergence_mutation": False,
        "external_container_mutation": False,
        "elapsed_seconds": time.monotonic() - started,
        "recovery_source_path": str(Path(__file__).resolve()),
        "recovery_source_sha256": sha(Path(__file__).resolve()),
    })
    cleanup = reset.purge_completed_discard_quarantine(
        quarantine_root=quarantine, reset_workers=recovered,
        binding_path=binding, cleanup_path=output / "quarantine_cleanup.json",
    )
    print(json.dumps({"status": "PASS", "binding": str(binding),
                      "elapsed_seconds": time.monotonic() - started,
                      "quarantine_deleted_bytes": cleanup["deleted_bytes"]},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
