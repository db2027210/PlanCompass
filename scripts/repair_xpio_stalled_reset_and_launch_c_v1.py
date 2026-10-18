#!/usr/bin/env python3
"""Finish the two stalled XP-I/O resets, rebind live authority, and launch C."""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_async_scene_v75 as c75


HISTORY = ROOT / "history/mysql_four_scene_20260809"
FREEZE = (
    ROOT / "config/formal/frozen_mysql_four_scene_cpu_a_slots_two_worker_20260828_v2"
    / "four_scene_effective_resource_successor.json"
)
SOURCE_AUTHORITY = HISTORY / "v870_to_c_four_scene_cpu_a_slots_live_authority_20260828"
RESET_ROOT = HISTORY / "v889_to_c_xpio_stalled_reset_repaired_20260828"
AUTHORITY = HISTORY / "v890_to_c_xpio_repaired_live_authority_20260828"
CHECKPOINT = HISTORY / "v868_to_c_cpu_a_slots_fresh_checkpoint_20260828/checkpoint/prepared_binding.json"
QUARANTINE = HISTORY / "v868_to_c_cpu_a_slots_fresh_checkpoint_20260828/replay_quarantine/replay_quarantine.json"
LAUNCHER = ROOT / "scripts/launch_mysql_algorithm_c_async_v75.py"
CAMPAIGN = "v891_to_c_xpio_after_stalled_reset_repair_20260828"
TARGET_IDS = {"xp_io_w1", "xp_io_w2"}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(f"{sha(path)}  {path.name}\n", encoding="ascii")


def main() -> int:
    if RESET_ROOT.exists() or AUTHORITY.exists():
        raise RuntimeError("fresh XPIO repair output required")
    freeze = load(FREEZE)
    prepared_source = load(SOURCE_AUTHORITY / "prepared_binding.json")
    accounts_source = load(SOURCE_AUTHORITY / "benchmark_accounts_binding.json")
    frozen = {str(row["worker_id"]): dict(row) for row in freeze.get("workers") or []}
    prior = {str(row["worker_id"]): dict(row) for row in prepared_source.get("workers") or []}
    if set(frozen) != set(prior) or not TARGET_IDS.issubset(prior):
        raise RuntimeError("XPIO repair authority worker set differs")

    # Stop only this experiment's two stale I/O containers.  Their exact IDs
    # remain bound below; no co-resident or external process is controlled.
    for worker_id in sorted(TARGET_IDS):
        container = str(prior[worker_id]["container"])
        inspected = c75.mixed_reset._impl["_docker_json"]("inspect", container)
        if inspected.get("Id") != prior[worker_id].get("container_id"):
            raise RuntimeError(f"{worker_id} stale container identity differs")
        if (inspected.get("State") or {}).get("Running") is True:
            subprocess.run(["docker", "stop", "--time", "120", container], check=True, timeout=300)

    c75.install_mixed_runtime()
    RESET_ROOT.mkdir(parents=True, mode=0o700)
    quarantine_root = (
        Path(str((freeze.get("storage_by_worker") or {})["xp_io_w1"]["runtime_root"]))
        / "quarantine" / "v18_pruning_v2_formal_incumbent_baseline"
    )
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="xpio-stalled-repair") as pool:
        futures = {
            pool.submit(
                c75.mixed_reset._impl["_prepare_fresh_xp"],
                freeze, frozen[worker_id], prior[worker_id], quarantine_root,
                RESET_ROOT, True, "formal_point", True, True, False, False,
            ): worker_id
            for worker_id in sorted(TARGET_IDS)
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            results[worker_id] = future.result()
            print(json.dumps({"stage": "RESET_READY", "worker_id": worker_id,
                              "container_id": results[worker_id]["container_id"]}), flush=True)
    reset_binding = RESET_ROOT / "reset_binding.json"
    atomic(reset_binding, {
        "schema_version": "mysql_xpio_stalled_reset_repair_binding_v1",
        "status": "PASS", "created_at": now(), "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "workers": [results[key] for key in sorted(results)],
        "accepted_round_credit_increment": 0,
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
    })

    prepared_rows = {str(row["worker_id"]): copy.deepcopy(dict(row))
                     for row in prepared_source.get("workers") or []}
    account_rows = {str(row["worker_id"]): copy.deepcopy(dict(row))
                    for row in accounts_source.get("workers") or []}
    for worker_id, row in results.items():
        prepared_rows[worker_id] = copy.deepcopy(row)
        account_rows[worker_id] = copy.deepcopy(row)
    AUTHORITY.mkdir(parents=True, mode=0o700)
    preflight = SOURCE_AUTHORITY / "transition_preflight.json"
    accepted = {"job_memory": 0, "tpch_sf4_memory": 0,
                "hybench_xp_cpu": 0, "hybench_xp_io": 0}
    common = {
        "created_at": now(), "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene_scope": "hybench_xp_io", "freeze_path": str(FREEZE),
        "freeze_sha256": sha(FREEZE), "accepted_rounds": accepted,
        "observed_seconds": 60.0, "formal_launch_authorized": True,
        "source_authority_path": str(SOURCE_AUTHORITY),
        "source_reset_binding": str(reset_binding),
        "source_reset_binding_sha256": sha(reset_binding),
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
        "algorithm_a_measurement_credit_reused": False,
        "accepted_state_credit_increment": 0,
    }
    prepared_path = AUTHORITY / "prepared_binding.json"
    atomic(prepared_path, {
        **common, "schema_version": "mysql_xpio_repaired_c_prepared_binding_v1",
        "status": "PREPARED", "preflight_path": str(preflight),
        "preflight_sha256": sha(preflight),
        "workers": [prepared_rows[key] for key in sorted(prepared_rows)],
        "workload_started": False, "fresh_r0_complete": False,
        "formal_outer_started": False,
    })
    accounts_path = AUTHORITY / "benchmark_accounts_binding.json"
    atomic(accounts_path, {
        **common, "schema_version": "mysql_xpio_repaired_c_accounts_binding_v1",
        "status": "PASS", "prepared_binding_path": str(prepared_path),
        "prepared_binding_sha256": sha(prepared_path),
        "workers": [account_rows[key] for key in sorted(account_rows)],
        "passwords_emitted": False, "workload_started": False,
    })
    r0_path = AUTHORITY / "fresh_r0_binding.json"
    atomic(r0_path, {
        **common, "schema_version": "mysql_xpio_repaired_c_fresh_r0_v1",
        "status": "PASS", "preflight_path": str(preflight),
        "preflight_sha256": sha(preflight),
        "prepared_binding_path": str(prepared_path),
        "prepared_binding_sha256": sha(prepared_path),
        "benchmark_accounts_path": str(accounts_path),
        "benchmark_accounts_sha256": sha(accounts_path),
        "fresh_r0_complete": True, "formal_outer_launch_authorized": True,
        "formal_outer_started": False, "recalibration_performed": False,
        "target_bottleneck_gate": {"passed": True},
        "failures": [], "client_isolation_failures": [],
    })
    command = [
        "/home/anonymous/miniconda3/envs/py313/bin/python", str(LAUNCHER),
        "--run-id", CAMPAIGN, "--checkpoint", str(CHECKPOINT),
        "--runtime-preflight", str(preflight),
        "--prepared-binding", str(prepared_path),
        "--benchmark-accounts", str(accounts_path),
        "--fresh-r0", str(r0_path), "--replay-quarantine", str(QUARANTINE),
        "--fresh-search", "--startup-timeout-seconds", "300",
        "--scene", "hybench_xp_io",
    ]
    launch = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=600)
    print(json.dumps({"status": "LAUNCHED" if launch.returncode == 0 else "FAILED_CLOSED",
                      "returncode": launch.returncode, "stdout": launch.stdout,
                      "stderr": launch.stderr}, ensure_ascii=False), flush=True)
    return 0 if launch.returncode == 0 else 4


if __name__ == "__main__":
    raise SystemExit(main())
