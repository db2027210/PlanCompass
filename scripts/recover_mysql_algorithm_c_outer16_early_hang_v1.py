#!/usr/bin/env python3
"""Recover four XP planes after a proven early incumbent-baseline hang."""

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
from scripts.build_mysql_algorithm_c_outer15_xp_rollback_checkpoint_v1 import resource_window
from scripts.recover_mysql_algorithm_c_outer15_technical_timeout_v3 import (
    XP_IDS, atomic, load, recorded_client_pids, sha, unit_fields,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--failed-root", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.run_id) is None:
        parser.error("safe run id required")
    failed_root = args.failed_root.resolve()
    state_path, receipt_path = failed_root / "run_state.json", failed_root / "launch_receipt.json"
    samples_path = failed_root / "formal_incumbent_baseline/resource_samples.jsonl"
    state, receipt = load(state_path), load(receipt_path)
    prepared_path = Path(str(state.get("prepared_binding_path") or "")).resolve()
    prepared = load(prepared_path)
    freeze_path = Path(str(state.get("freeze_path") or "")).resolve()
    freeze = load(freeze_path)
    workers = {str(row["worker_id"]): dict(row) for row in prepared.get("workers") or []
               if row.get("worker_id") in XP_IDS}
    frozen = {str(row["worker_id"]): dict(row) for row in freeze.get("workers") or []
              if row.get("worker_id") in XP_IDS}
    window = resource_window(samples_path)
    fields = unit_fields(str(receipt.get("systemd_unit") or ""))
    clients = recorded_client_pids(failed_root / "formal_incumbent_baseline")
    live_clients = sorted(pid for pid in clients if Path(f"/proc/{pid}").exists())
    if (
        failed_root.parent != ROOT / "history/mysql_four_scene_20260809"
        or state.get("status") != "RUNNING"
        or state.get("stage") != "FORMAL_INCUMBENT_BASELINE_MEASURING"
        or int(state.get("outer_attempt") or 0) != 15
        or (failed_root / "formal_incumbent_baseline/batch_result.json").exists()
        or fields.get("ActiveState") != "inactive"
        or int(fields.get("MainPID") or 0) != 0
        or live_clients
        or set(window) != {"job_w1", "job_w2", "tpch_w1", "tpch_w2", *XP_IDS}
        or any(window[w]["seconds"] < 25 or window[w]["cpu_cores"] >= 0.01 for w in XP_IDS)
        or any(window[w]["cpu_cores"] <= 1.0
               for w in {"job_w1", "job_w2", "tpch_w1", "tpch_w2"})
        or prepared.get("status") != "PREPARED"
        or prepared.get("freeze_sha256") != sha(freeze_path)
        or state.get("freeze_sha256") != sha(freeze_path)
        or set(workers) != XP_IDS or set(frozen) != XP_IDS
    ):
        raise RuntimeError("early-hang recovery authority differs")

    output = ROOT / "history/mysql_four_scene_20260809" / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh recovery output exists: {output}")
    stage = output / "xp_resets/outer16_early_hang_recovery_v1"
    stage.mkdir(parents=True, mode=0o700)
    quarantine = Path(str(freeze["storage"]["runtime_root"])) / "quarantine" / output.name / stage.name
    started = time.monotonic()
    recovered = {}
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="early-hang-xp") as pool:
        futures = {
            pool.submit(reset._prepare_fresh_xp, freeze, frozen[w], workers[w],
                        quarantine, stage, False, "formal_point", True,
                        False, False, False): w for w in sorted(XP_IDS)
        }
        for future in as_completed(futures):
            worker = futures[future]
            recovered[worker] = future.result()
            print(json.dumps({"worker_id": worker, "reset_complete": True,
                              "elapsed_seconds": time.monotonic() - started}), flush=True)
    account_root = stage / "accounts"
    account_root.mkdir()
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="early-hang-users") as pool:
        futures = {pool.submit(users._run_worker, recovered[w], account_root, True, False): w
                   for w in sorted(XP_IDS)}
        for future in as_completed(futures):
            worker = futures[future]
            recovered[worker].update(future.result())
    if any(row.get("ready") is not True
           or algorithm.xp_active_client_session_count(row) != 0
           for row in recovered.values()):
        raise RuntimeError("early-hang recovered XP readback differs")

    binding = output / "recovered_xp_binding.json"
    atomic(binding, {
        "schema_version": "mysql_algorithm_c_outer16_early_hang_recovery_v1",
        "status": "PASS", "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "failed_state": {"path": str(state_path), "sha256": sha(state_path)},
        "failed_launch_receipt": {"path": str(receipt_path), "sha256": sha(receipt_path)},
        "early_hang_resource_samples": {"path": str(samples_path), "sha256": sha(samples_path),
                                         "last_30_second_window": window},
        "terminal_unit_readback": fields,
        "prepared_binding": {"path": str(prepared_path), "sha256": sha(prepared_path)},
        "freeze_path": str(freeze_path), "freeze_sha256": sha(freeze_path),
        "recorded_client_pids": sorted(clients), "live_recorded_client_pids": [],
        "reset_worker_ids": sorted(XP_IDS), "workers": [recovered[w] for w in sorted(XP_IDS)],
        "workload_started": False, "formal_state_credit_increment": 0,
        "accepted_rounds_mutation": False, "rejected_rounds_mutation": False,
        "interval_convergence_mutation": False, "external_container_mutation": False,
        "elapsed_seconds": time.monotonic() - started,
        "recovery_source_path": str(Path(__file__).resolve()),
        "recovery_source_sha256": sha(Path(__file__).resolve()),
    })
    cleanup = reset.purge_completed_discard_quarantine(
        quarantine_root=quarantine, reset_workers=recovered,
        binding_path=binding, cleanup_path=output / "quarantine_cleanup.json")
    print(json.dumps({"status": "PASS", "binding": str(binding),
                      "elapsed_seconds": time.monotonic() - started,
                      "quarantine_deleted_bytes": cleanup["deleted_bytes"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
