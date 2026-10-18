#!/usr/bin/env python3
"""Run the v11 60-second live rolling non-interference and resource gate."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import time


if str(Path(__file__).resolve().parents[1]) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import mysql_cross_project_wave_coordination_v1 as coordination


ROOT = Path(__file__).resolve().parents[1]
FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260810_v12"
    / "four_scene_effective_resource_successor.json"
)
V10_FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260809_v11"
    / "four_scene_effective_resource_successor.json"
)
V10_PREFLIGHT = ROOT / "preflight/mysql_four_scene_20260809/runtime_preflight_v15.json"
NON_XP_PREPARED = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_adopt_v7_20260809T194100p0800/prepared_binding.json"
)
XP_PREPARED = (
    ROOT / "history/mysql_four_scene_20260809"
    / "xp_restore_after_failed_formal_v15_retry1_20260810T143400p0800"
    / "prepared_binding.json"
)
HELPERS = ROOT / "scripts/preflight_mysql_four_scene_v3.py"
XP_IDS = {"xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2"}
FREEZE_SCHEMA = "mysql_disk2_four_scene_two_worker_effective_resource_successor_v12"
PREDECESSOR_FREEZE_SCHEMA = (
    "mysql_disk2_four_scene_two_worker_effective_resource_successor_v11"
)
OUTPUT_SCHEMA = "mysql_four_scene_live_runtime_preflight_v12"
AUTHORITY_VERSION = "v12"
XP_ALLOWED_STATUSES = {"PREPARED"}


def successor_change_matches(change: dict) -> bool:
    return (
        change.get("job_memory_bytes") == {"from": 6174015488, "to": 10737418240}
        and change.get("job_innodb_buffer_pool_bytes")
        == {"from": 4294967296, "to": 8589934592}
    )


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def cpus(value: str) -> set[int]:
    result = set()
    for field in value.split(","):
        start, separator, end = field.strip().partition("-")
        result.update(range(int(start), int(end) + 1) if separator else [int(start)])
    return result


def compress(values: set[int]) -> str:
    ordered = sorted(values)
    groups = []
    for value in ordered:
        if not groups or value != groups[-1][-1] + 1:
            groups.append([value])
        else:
            groups[-1].append(value)
    return ",".join(str(group[0]) if len(group) == 1 else f"{group[0]}-{group[-1]}" for group in groups)


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def docker_json(name: str) -> dict:
    value = json.loads(subprocess.run(
        ["docker", "inspect", name], capture_output=True, text=True,
        check=True, timeout=60,
    ).stdout)
    if not isinstance(value, list) or len(value) != 1:
        raise RuntimeError(f"Docker inspect cardinality differs: {name}")
    return value[0]


@lru_cache(maxsize=1)
def unrelated_megasas_poll_authority() -> dict:
    """Prove that the MegaSAS status worker belongs to disk3, not target disk2."""
    disk2_source = subprocess.run(
        ["findmnt", "-n", "-o", "SOURCE", "--target", "/disk2"],
        capture_output=True, text=True, check=False, timeout=10,
    ).stdout.strip()
    disk3_source = subprocess.run(
        ["findmnt", "-n", "-o", "SOURCE", "--target", "/disk3"],
        capture_output=True, text=True, check=False, timeout=10,
    ).stdout.strip()
    sda_device = Path("/sys/class/block/sda/device").resolve()
    nvme2_device = Path("/sys/class/block/nvme2n1/device").resolve()
    megasas_hosts = []
    for entry in sorted(Path("/sys/class/scsi_host").glob("host*/proc_name")):
        try:
            if entry.read_text(encoding="ascii").strip() == "megaraid_sas":
                megasas_hosts.append(entry.parent.name)
        except OSError:
            continue
    sda_host = next((part for part in sda_device.parts if re.fullmatch(r"host\d+", part)), None)
    proven = (
        disk2_source == "/dev/nvme2n1"
        and disk3_source == "/dev/sda1"
        and sda_host in megasas_hosts
        and "nvme" in nvme2_device.parts
        and sda_device != nvme2_device
    )
    return {
        "proven": proven,
        "target_mount": "/disk2",
        "target_source": disk2_source,
        "target_device_path": str(nvme2_device),
        "unrelated_mount": "/disk3",
        "unrelated_source": disk3_source,
        "unrelated_device_path": str(sda_device),
        "unrelated_driver": "megaraid_sas" if sda_host in megasas_hosts else None,
        "unrelated_scsi_host": sda_host,
    }


def classify_d_state_name(name: str, authority: dict) -> str:
    target_journal = bool(re.fullmatch(r"jbd2/nvme2n1-\d+", name))
    exempted = bool(
        authority.get("proven") is True
        and name.startswith("kworker/")
        and name.endswith("-poll_megasas0_status")
    )
    if target_journal:
        # jbd2 is the filesystem's own target-device journal worker.  Its D
        # state records an in-flight journal commit, not the identity of an
        # external user-space workload.  Keep it visible in every pressure
        # sample, but let process/cgroup/storage-overlap evidence decide
        # interference instead of treating the kernel worker itself as proof.
        return "observed_target_journal_thread_nonblocking"
    if exempted:
        return "exempt_unrelated_disk3_megasas_status_poll"
    return "blocking_unknown_or_target_relevant"


def host_pressure() -> dict:
    load1 = float(Path("/proc/loadavg").read_text(encoding="ascii").split()[0])
    d_state_details = []
    authority = unrelated_megasas_poll_authority()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            status = (entry / "status").read_text(
                encoding="utf-8", errors="replace",
            )
            if re.search(r"^State:\s+D\b", status, re.MULTILINE):
                name_match = re.search(r"^Name:\s+(.+)$", status, re.MULTILINE)
                name = name_match.group(1).strip() if name_match else ""
                classification = classify_d_state_name(name, authority)
                d_state_details.append({
                    "pid": int(entry.name),
                    "name": name,
                    "classification": classification,
                })
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
            continue
    d_state_details.sort(key=lambda row: row["pid"])
    return {
        "load1": load1,
        "d_state_pids": [row["pid"] for row in d_state_details],
        "blocking_d_state_pids": [
            row["pid"] for row in d_state_details
            if row["classification"] == "blocking_unknown_or_target_relevant"
        ],
        "exempted_unrelated_d_state_pids": [
            row["pid"] for row in d_state_details
            if row["classification"] == "exempt_unrelated_disk3_megasas_status_poll"
        ],
        "observed_target_journal_d_state_pids": [
            row["pid"] for row in d_state_details
            if row["classification"] == "observed_target_journal_thread_nonblocking"
        ],
        "d_state_details": d_state_details,
        "unrelated_megasas_poll_authority": authority,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--window-seconds", type=int, default=60)
    parser.add_argument("--interval-seconds", type=float, default=1.0)
    parser.add_argument("--coordinated-workload-lease-holder-pid", type=int)
    parser.add_argument("--coordination-contract", type=Path)
    args = parser.parse_args()
    if args.window_seconds < 60 or args.interval_seconds <= 0:
        parser.error("live window must be at least 60 seconds")
    freeze = load(FREEZE)
    v10 = load(V10_FREEZE)
    predecessor = load(V10_PREFLIGHT)
    non_xp = load(NON_XP_PREPARED)
    xp = load(XP_PREPARED)
    xp_worker_ids = [
        row.get("worker_id") for row in (xp.get("workers") or [])
        if isinstance(row, dict) and row.get("worker_id") in XP_IDS
    ]
    coordinated = args.coordinated_workload_lease_holder_pid is not None
    if coordinated != (args.coordination_contract is not None):
        parser.error("coordinated lease holder and contract must be provided together")
    coordination_contract = None
    if coordinated:
        contract_path = args.coordination_contract.expanduser().resolve()
        if (
            contract_path != coordination.CONTRACT_PATH.resolve()
            or not contract_path.is_file()
            or contract_path.is_symlink()
            or not coordination.lock_held_exclusively_by(
                args.coordinated_workload_lease_holder_pid
            )
        ):
            raise RuntimeError("coordinated workload lease proof differs")
        coordination_contract = load(contract_path)
        if (
            coordination_contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
            or coordination_contract.get("formal_scope") != "mysql_only"
            or coordination_contract.get("cross_engine_comparison_prohibited") is not True
            or coordination_contract.get("lock_path") != str(coordination.LOCK_PATH)
        ):
            raise RuntimeError("cross-project coordination contract differs")
    if (
        freeze.get("schema_version") != FREEZE_SCHEMA
        or not successor_change_matches(
            freeze.get("successor_resource_envelope_change", {})
        )
        or v10.get("schema_version")
        != PREDECESSOR_FREEZE_SCHEMA
        or (freeze.get("predecessor") or {}).get("sha256") != sha(V10_FREEZE)
        or predecessor.get("status") != "PASS"
        or float(predecessor.get("observed_seconds", 0)) < 60
        or non_xp.get("status") != "PREPARED"
        or xp.get("status") not in XP_ALLOWED_STATUSES
        or xp.get("freeze_sha256") != sha(FREEZE)
        or len(xp_worker_ids) != len(XP_IDS)
        or set(xp_worker_ids) != XP_IDS
    ):
        raise RuntimeError(f"{AUTHORITY_VERSION} live-preflight authority differs")
    spec = importlib.util.spec_from_file_location("v11_preflight_helpers", HELPERS)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load preflight helpers")
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    frozen = {row["worker_id"]: row for row in freeze["workers"]}
    non_xp_rows = {row["worker_id"]: row for row in non_xp["workers"]}
    xp_rows = {row["worker_id"]: row for row in xp["workers"]}
    target_cpus = set()
    target_mems = set()
    intended_names = set()
    live_readback = []
    xp_io_profile = freeze.get("xp_innodb_io_profile") or {}
    for worker_id, worker in frozen.items():
        expected = (xp_rows if worker_id in XP_IDS else non_xp_rows)[worker_id]
        inspect = docker_json(worker["container"])
        host = inspect.get("HostConfig") or {}
        expected_host = {
            "CpusetCpus": worker["database_cpuset"],
            "CpusetMems": worker["database_cpuset_mems"],
            "NanoCpus": int(round(float(worker["cpu_quota_cores"]) * 1_000_000_000)),
            "Memory": int(worker["memory_bytes"]),
            "PidsLimit": int(worker["pids_max"]),
        }
        if worker_id in XP_IDS and xp_io_profile:
            expected_host["Init"] = bool(xp_io_profile.get("container_init"))
        if (
            inspect.get("Id") != expected.get("container_id")
            or (inspect.get("State") or {}).get("Running") is not True
            or any(host.get(key) != value for key, value in expected_host.items())
        ):
            raise RuntimeError(f"{worker_id} v11 live resource readback differs")
        mysql_readback = subprocess.run(
            [
                "docker", "exec", worker["container"], "mysql",
                "--defaults-extra-file=/run/ra-credentials/selected.cnf",
                "-Nse", "SELECT @@GLOBAL.innodb_buffer_pool_size,"
                "@@GLOBAL.innodb_use_native_aio,@@GLOBAL.innodb_flush_method;",
            ], capture_output=True, text=True, check=True, timeout=60,
        ).stdout.strip().split("\t")
        if len(mysql_readback) != 3:
            raise RuntimeError(f"{worker_id} InnoDB readback cardinality differs")
        buffer_pool = int(mysql_readback[0])
        native_aio = mysql_readback[1] == "1"
        flush_method = mysql_readback[2]
        if buffer_pool != int(worker["innodb_buffer_pool_bytes"]):
            raise RuntimeError(f"{worker_id} buffer-pool readback differs")
        if worker_id in XP_IDS and xp_io_profile and (
            native_aio is not bool(xp_io_profile.get("innodb_use_native_aio"))
            or flush_method.lower()
            != str(xp_io_profile.get("innodb_flush_method", "")).lower()
        ):
            raise RuntimeError(f"{worker_id} frozen InnoDB I/O readback differs")
        target_cpus.update(cpus(worker["database_cpuset"]))
        target_cpus.update(cpus(worker["client_cpuset"]))
        target_mems.update(cpus(worker["database_cpuset_mems"]))
        target_mems.update(cpus(worker["client_cpuset_mems"]))
        intended_names.add(worker["container"])
        live_readback.append({
            "worker_id": worker_id,
            "container": worker["container"],
            "container_id": inspect["Id"],
            "resources": expected_host,
            "innodb_buffer_pool_bytes": buffer_pool,
            "innodb_use_native_aio": native_aio,
            "innodb_flush_method": flush_method,
        })
    samples = []
    coordinated_controller_inventory: dict[int, dict] = {}
    started = time.monotonic()
    deadline = started + args.window_seconds
    while True:
        processes, process_blockers = helpers._process_inventory(
            target_cpus, target_mems,
        )
        if coordinated:
            retained_process_blockers = []
            for row in process_blockers:
                command = str(row.get("command") or "")
                kind = coordination.command_kind(command)
                if kind == "controller":
                    coordinated_controller_inventory[int(row["pid"])] = row
                else:
                    retained_process_blockers.append(row)
            process_blockers = retained_process_blockers
        containers, container_blockers = helpers._docker_inventory(target_cpus, intended_names)
        blockers = process_blockers + container_blockers
        pressure = host_pressure()
        if pressure["blocking_d_state_pids"]:
            blockers.append({
                "reason": "host_uninterruptible_sleep_present",
                "d_state_pids": pressure["blocking_d_state_pids"],
            })
        if pressure["load1"] > 16.0:
            blockers.append({
                "reason": "host_idle_load1_exceeds_16",
                "load1": pressure["load1"],
            })
        samples.append({
            "captured_at": now(),
            "process_inventory": processes,
            "container_inventory": containers,
            "host_pressure": pressure,
            "blockers": blockers,
        })
        if blockers or time.monotonic() >= deadline:
            break
        time.sleep(min(args.interval_seconds, max(0.0, deadline - time.monotonic())))
    observed = time.monotonic() - started
    passed = observed >= args.window_seconds and not samples[-1]["blockers"]
    document = {
        "schema_version": OUTPUT_SCHEMA,
        "status": "PASS" if passed else "BLOCKED_FAIL_CLOSED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_path": str(FREEZE),
        "freeze_sha256": sha(FREEZE),
        "resource_envelope_change": freeze["successor_resource_envelope_change"],
        "rolling_predecessor_path": str(V10_PREFLIGHT),
        "rolling_predecessor_sha256": sha(V10_PREFLIGHT),
        "target_cpuset_union": compress(target_cpus),
        "target_memset_union": compress(target_mems),
        "host_pressure_contract": {
            "maximum_idle_load1": 16.0,
            "maximum_unknown_or_target_relevant_d_state_processes": 0,
            "only_exemption": "proven_unrelated_disk3_megasas_status_poll",
            "target_storage_source": "/dev/nvme2n1",
            "derived_from_failed_outer5_batch1_elapsed_seconds": 911.7495001750067,
        },
        "coordinated_external_controller_allowance": {
            "enabled": coordinated,
            "semantics": (
                "idle_controller_inventory_allowed_only_while_parent_holds_"
                "exclusive_cross_project_physical_workload_lease"
            ),
            "lease_holder_pid": args.coordinated_workload_lease_holder_pid,
            "lock_path": str(coordination.LOCK_PATH) if coordinated else None,
            "coordination_contract_path": (
                str(args.coordination_contract.expanduser().resolve())
                if coordinated else None
            ),
            "coordination_contract_sha256": (
                coordination.sha256(args.coordination_contract.expanduser().resolve())
                if coordinated else None
            ),
            "controller_inventory": [
                coordinated_controller_inventory[pid]
                for pid in sorted(coordinated_controller_inventory)
            ],
            "active_physical_workload_processes_remain_blocking": True,
        },
        "required_clean_window_seconds": args.window_seconds,
        "observed_seconds": observed,
        "live_resource_readback": live_readback,
        "samples": samples,
        "blockers": samples[-1]["blockers"],
        "formal_launch_authorized": passed,
        "launch_performed": False,
        "accepted_rounds": {scene: 0 for scene in freeze["active_scenes"]},
        "completed_at": now(),
    }
    output = args.output.resolve()
    if output.exists():
        raise RuntimeError(f"fresh output exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.with_name(output.name + ".sha256").write_text(
        f"{sha(output)}  {output.name}\n", encoding="ascii"
    )
    print(json.dumps({"status": document["status"], "blockers": len(document["blockers"])}))
    return 0 if passed else 3


if __name__ == "__main__":
    raise SystemExit(main())
