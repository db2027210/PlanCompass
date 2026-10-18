#!/usr/bin/env python3
"""Run the versioned 15-second continuation gate after a sealed XP reset."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v1 as coordination
from scripts import preflight_mysql_four_scene_live_v18 as release
from scripts import watch_mysql_formal_external_postgres_interference_v5 as watchdog


base = release.implementation
XP_IDS = {"xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2"}
SCENES = ["job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io"]
XP_PREPARED_VALUE = os.environ.get("MYSQL_V18_RECOVERED_XP_BINDING")
if not XP_PREPARED_VALUE:
    raise SystemExit("MYSQL_V18_RECOVERED_XP_BINDING is required")
XP_PREPARED = Path(XP_PREPARED_VALUE).resolve()


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular non-symlink JSON required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--window-seconds", type=int, default=60)
    parser.add_argument("--interval-seconds", type=float, default=1.0)
    parser.add_argument("--coordinated-workload-lease-holder-pid", type=int, required=True)
    parser.add_argument("--coordination-contract", type=Path, required=True)
    args = parser.parse_args()
    if args.window_seconds < 15 or args.interval_seconds <= 0:
        parser.error("continuation live window must be at least 15 seconds")

    watchdog.install()
    freeze = load(base.FREEZE)
    predecessor_freeze = load(base.V10_FREEZE)
    predecessor_preflight = load(base.V10_PREFLIGHT)
    non_xp = load(base.NON_XP_PREPARED)
    xp = load(XP_PREPARED)
    contract_path = args.coordination_contract.expanduser().resolve()
    if (
        contract_path != coordination.CONTRACT_PATH.resolve()
        or not coordination.lock_held_exclusively_by(
            args.coordinated_workload_lease_holder_pid
        )
    ):
        raise RuntimeError("coordinated workload lease proof differs")
    contract = load(contract_path)
    xp_rows = {
        str(row.get("worker_id")): dict(row)
        for row in xp.get("workers") or []
        if row.get("worker_id") in XP_IDS
    }
    non_xp_rows = {
        str(row.get("worker_id")): dict(row)
        for row in non_xp.get("workers") or []
    }
    if (
        freeze.get("schema_version") != base.FREEZE_SCHEMA
        or not release._v18_change(
            freeze.get("successor_resource_envelope_change") or {}
        )
        or predecessor_freeze.get("schema_version")
        != base.PREDECESSOR_FREEZE_SCHEMA
        or (freeze.get("predecessor") or {}).get("sha256") != sha(base.V10_FREEZE)
        or predecessor_preflight.get("status") != "PASS"
        or float(predecessor_preflight.get("observed_seconds") or 0) < 60
        or non_xp.get("status") != "PREPARED"
        or xp.get("status") != "PASS"
        or xp.get("freeze_sha256") != sha(base.FREEZE)
        or set(xp_rows) != XP_IDS
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("release_version") != "v57"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
    ):
        raise RuntimeError("v18 recovered-XP live-preflight authority differs")

    frozen = {str(row["worker_id"]): dict(row) for row in freeze["workers"]}
    target_cpus: set[int] = set()
    target_mems: set[int] = set()
    intended_names: set[str] = set()
    live_readback = []
    xp_io_profile = freeze.get("xp_innodb_io_profile") or {}
    for worker_id, worker in frozen.items():
        expected = (xp_rows if worker_id in XP_IDS else non_xp_rows)[worker_id]
        inspected = base.docker_json(str(worker["container"]))
        host = inspected.get("HostConfig") or {}
        expected_host = {
            "CpusetCpus": worker["database_cpuset"],
            "CpusetMems": worker["database_cpuset_mems"],
            "NanoCpus": int(round(float(worker["cpu_quota_cores"]) * 1_000_000_000)),
            "Memory": int(worker["memory_bytes"]),
            "PidsLimit": int(worker["pids_max"]),
        }
        if worker_id in XP_IDS:
            expected_host["Init"] = bool(xp_io_profile.get("container_init"))
        if (
            inspected.get("Id") != expected.get("container_id")
            or (inspected.get("State") or {}).get("Running") is not True
            or any(host.get(key) != value for key, value in expected_host.items())
        ):
            raise RuntimeError(f"{worker_id} recovered-XP live resource readback differs")
        values = subprocess.run([
            "docker", "exec", str(worker["container"]), "mysql",
            "--defaults-extra-file=/run/ra-credentials/selected.cnf", "-Nse",
            "SELECT @@GLOBAL.innodb_buffer_pool_size,"
            "@@GLOBAL.innodb_use_native_aio,@@GLOBAL.innodb_flush_method;",
        ], capture_output=True, text=True, check=True, timeout=60).stdout.strip().split("\t")
        if len(values) != 3 or int(values[0]) != int(worker["innodb_buffer_pool_bytes"]):
            raise RuntimeError(f"{worker_id} recovered-XP InnoDB readback differs")
        native_aio = values[1] == "1"
        flush_method = values[2]
        if worker_id in XP_IDS and (
            native_aio is not bool(xp_io_profile.get("innodb_use_native_aio"))
            or flush_method.lower()
            != str(xp_io_profile.get("innodb_flush_method") or "").lower()
        ):
            raise RuntimeError(f"{worker_id} frozen XP InnoDB I/O profile differs")
        target_cpus.update(base.cpus(str(worker["database_cpuset"])))
        target_cpus.update(base.cpus(str(worker["client_cpuset"])))
        target_mems.update(base.cpus(str(worker["database_cpuset_mems"])))
        target_mems.update(base.cpus(str(worker["client_cpuset_mems"])))
        intended_names.add(str(worker["container"]))
        live_readback.append({
            "worker_id": worker_id,
            "container": worker["container"],
            "container_id": inspected["Id"],
            "resources": expected_host,
            "innodb_buffer_pool_bytes": int(values[0]),
            "innodb_use_native_aio": native_aio,
            "innodb_flush_method": flush_method,
        })

    spec = importlib.util.spec_from_file_location(
        "v18_recovered_xp_preflight_helpers", base.HELPERS,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load frozen process inventory helpers")
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    samples = []
    started = time.monotonic()
    clean_since: float | None = None
    d_state_streaks: dict[int, int] = {}
    maximum_wait = float(args.window_seconds + 60)
    terminal_errors: list[str] = []
    while True:
        # Snapshot the short-lived PostgreSQL clients/backends on both sides of
        # the slower full /proc inventory.  A single after-only snapshot can
        # miss a process that was alive when helpers._process_inventory()
        # recorded it but exited before external_process_inventory() reached
        # that PID, falsely classifying an isolated PG query as an unknown NUMA
        # overlap.
        inventory_before = coordination.external_process_inventory(
            "v18-recovered-xp-preflight", target_mems,
        )
        processes, process_blockers = helpers._process_inventory(
            target_cpus, target_mems,
        )
        # Validate workload-shaped blockers immediately while their /proc
        # cgroup membership is still readable.  HyBench and JOB can create PG
        # backends that live for less time than the subsequent Docker and
        # external-process scans.  The exact v57 evaluator remains fail-closed
        # on CPU, storage, memory, command/profile, and container identity.
        provisional_workloads = [
            dict(row) for row in process_blockers
            if coordination.command_kind(str(row.get("command") or ""))
            == "workload"
        ]
        provisional_isolation = (
            coordination.evaluate_co_resident_pg_isolation(
                provisional_workloads
            )
            if provisional_workloads
            else {"status": "PASS_NO_PROVISIONAL_WORKLOAD", "passed": True,
                  "errors": []}
        )
        containers, container_blockers = helpers._docker_inventory(
            target_cpus, intended_names,
        )
        inventory_after = coordination.external_process_inventory(
            "v18-recovered-xp-preflight", target_mems,
        )
        inventory = {"workloads": [], "controllers": []}
        for kind in inventory:
            rows_by_pid = {
                int(row["pid"]): row
                for snapshot in (inventory_before, inventory_after)
                for row in snapshot[kind]
            }
            inventory[kind] = [rows_by_pid[pid] for pid in sorted(rows_by_pid)]
        isolation = coordination.evaluate_co_resident_pg_isolation(
            inventory["workloads"]
        )
        external_pids = {
            int(row["pid"])
            for row in inventory["workloads"] + inventory["controllers"]
        }
        provisional_pids = {
            int(row["pid"]) for row in provisional_workloads
        } if provisional_isolation.get("passed") is True else set()
        blockers = [
            row for row in process_blockers
            if not (
                isolation.get("passed") is True
                and int(row.get("pid") or 0)
                in (external_pids | provisional_pids)
            )
        ] + list(container_blockers)
        pressure = base.host_pressure()
        same_run = coordination.same_run_process_pids(
            "v18-recovered-xp-preflight"
        )
        current_d_state = {
            int(pid) for pid in pressure["blocking_d_state_pids"]
        } - same_run
        d_state_streaks = {
            pid: d_state_streaks.get(pid, 0) + 1
            for pid in current_d_state
        }
        persistent_d_state = sorted(
            pid for pid, streak in d_state_streaks.items() if streak >= 3
        )
        errors = []
        if inventory["workloads"] and isolation.get("passed") is not True:
            errors.append("co_resident_pg_isolation_failed")
        if blockers:
            errors.append("unisolated_process_or_container_overlap")
        if persistent_d_state:
            errors.append("persistent_host_uninterruptible_sleep_present")
        current = time.monotonic()
        if errors:
            clean_since = None
        elif clean_since is None:
            clean_since = current
        clean_elapsed = 0.0 if clean_since is None else current - clean_since
        samples.append({
            "captured_at": now(),
            "errors": errors,
            "process_inventory": processes,
            "container_inventory": containers,
            "remaining_overlap_blockers": blockers,
            "co_resident_pg_isolation": isolation,
            "provisional_co_resident_pg_isolation": {
                "status": provisional_isolation.get("status"),
                "passed": provisional_isolation.get("passed"),
                "errors": provisional_isolation.get("errors") or [],
                "workload_count": len(provisional_workloads),
                "workload_pids": sorted(provisional_pids),
                "authority": (
                    "same_v57_evaluator_at_process_inventory_capture_time"
                ),
            },
            "external_workload_pids": sorted(
                int(row["pid"]) for row in inventory["workloads"]
            ),
            "host_pressure": pressure,
            "transient_d_state_pids": sorted(current_d_state),
            "persistent_d_state_pids": persistent_d_state,
            "d_state_consecutive_sample_streaks": {
                str(pid): streak for pid, streak in sorted(d_state_streaks.items())
            },
            "global_load1_policy": (
                "diagnostic_only_after_direct_cpu_storage_memory_isolation"
            ),
            "clean_elapsed_seconds": clean_elapsed,
        })
        if "co_resident_pg_isolation_failed" in errors or blockers:
            terminal_errors = errors
            break
        if clean_elapsed >= args.window_seconds:
            break
        if current - started >= maximum_wait:
            terminal_errors = errors or ["rolling_clean_window_timeout"]
            break
        time.sleep(max(0.2, args.interval_seconds))

    observed = time.monotonic() - started
    passed = not terminal_errors and samples[-1]["clean_elapsed_seconds"] >= args.window_seconds
    document = {
        "schema_version": "mysql_four_scene_live_runtime_preflight_v18_recovered_xp_continuation_v2",
        "status": "PASS" if passed else "BLOCKED_FAIL_CLOSED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_path": str(base.FREEZE),
        "freeze_sha256": sha(base.FREEZE),
        "recovered_xp_binding_path": str(XP_PREPARED),
        "recovered_xp_binding_sha256": sha(XP_PREPARED),
        "rolling_predecessor_path": str(base.V10_PREFLIGHT),
        "rolling_predecessor_sha256": sha(base.V10_PREFLIGHT),
        "coordination_contract_path": str(contract_path),
        "coordination_contract_sha256": sha(contract_path),
        "isolation_source_path": str(Path(watchdog.isolation.__file__).resolve()),
        "isolation_source_sha256": sha(Path(watchdog.isolation.__file__).resolve()),
        "required_clean_window_seconds": args.window_seconds,
        "continuation_of_campaign_with_passed_60s_launch_preflight": True,
        "observed_seconds": observed,
        "maximum_wait_seconds": maximum_wait,
        "persistent_d_state_required_consecutive_samples": 3,
        "live_resource_readback": sorted(live_readback, key=lambda row: row["worker_id"]),
        "samples": samples,
        "blockers": samples[-1]["errors"],
        "formal_launch_authorized": passed,
        "launch_performed": False,
        "accepted_rounds": {scene: 0 for scene in SCENES},
        "external_mutation": False,
        "completed_at": now(),
    }
    output = args.output.expanduser().resolve()
    if output.exists():
        raise RuntimeError(f"fresh output exists: {output}")
    atomic(output, document)
    print(json.dumps({
        "status": document["status"],
        "observed_seconds": observed,
        "clean_elapsed_seconds": samples[-1]["clean_elapsed_seconds"],
        "blockers": len(document["blockers"]),
    }, sort_keys=True))
    return 0 if passed else 3


if __name__ == "__main__":
    raise SystemExit(main())
