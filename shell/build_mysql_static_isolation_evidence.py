#!/usr/bin/env python3
"""Build, verify, freeze, and optionally promote five static-isolation proofs."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
READINESS = (
    ROOT
    / "config/formal/frozen_mysql_deployment_schedule_20260723_v4_mysql_only"
    / "readiness_manifest.json"
)
OUTPUT = (
    ROOT
    / "config/formal/frozen_mysql_static_isolation_20260724_v1"
)
INVENTORY_ROOT = (
    ROOT
    / "history/mysql_sgd_env_agents_20260723"
    / "static_isolation_20260724T0134"
)
CONTROLLER = ROOT / "shell/manual_mysql_sgd_lane_controller.py"
FREEZE_ID = "frozen_mysql_static_isolation_20260724_v1"
DRIVER_COMMS = {
    "gsql",
    "psql",
    "sysbench",
    "mysqlslap",
    "java",
    "candidate_once",
    "ranked_replays",
    "outer_supervisor",
    "parameter_optimizer",
    "batch_collector",
}
SCENARIOS = {
    "hybench_ap_memory": {
        "host": "host01",
        "device": "/dev/nvme0n1",
        "source": "history/mysql_sgd_env_agents_20260723/ap/formal_mysql_normalized_envelope_readback.json",
        "host_group": "host01",
    },
    "job_memory": {
        "host": "host02",
        "device": "/dev/nvme1n1",
        "source": "history/mysql_sgd_env_agents_20260723/job/formal_mysql_normalized_envelope_readback.json",
        "host_group": "host02",
    },
    "hybench_xp_io": {
        "host": "host02",
        "device": "/dev/nvme0n1p1",
        "source": "history/mysql_sgd_env_agents_20260723/xp/formal_mysql_normalized_envelope_readback.json",
        "host_group": "host02",
    },
    "tpch_memory": {
        "host": "host04",
        "device": "/dev/nvme0n1",
        "source": "history/mysql_sgd_env_agents_20260723/tpch/formal_mysql_normalized_envelope_readback.json",
        "host_group": "host04",
    },
    "tpcc_cpu": {
        "host": "host04",
        "device": "/dev/nvme3n1",
        "source": "history/mysql_sgd_env_agents_20260723/tpcc/formal_mysql_normalized_envelope_readback.json",
        "host_group": "host04",
    },
}


def canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def pointer(path: Path) -> dict[str, str]:
    return {"path": relative(path), "sha256": sha256_file(path)}


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json_with_sidecar(path: Path, value: Mapping[str, Any]) -> str:
    data = canonical_bytes(value)
    digest = hashlib.sha256(data).hexdigest()
    atomic_write(path, data)
    atomic_write(
        path.with_name(path.name + ".sha256"),
        f"{digest}  {path.name}\n".encode("ascii"),
    )
    return digest


def cpu_set(raw: Any) -> set[int]:
    result: set[int] = set()
    for item in str(raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        if "-" in item:
            lower, upper = item.split("-", 1)
            result.update(range(int(lower), int(upper) + 1))
        else:
            result.add(int(item))
    return result


def device_family(raw: str) -> str:
    value = str(raw or "")
    match = re.match(r"^(/dev/nvme\d+n\d+)", value)
    return match.group(1) if match else value


def infer_co_resident_devices(inventory: Mapping[str, Any]) -> list[str]:
    devices: set[str] = set()
    containers = inventory.get("running_containers") or []
    for container in containers:
        for field in ("blkio_read_bps", "blkio_write_bps"):
            for row in container.get(field) or []:
                path = str(row.get("Path") or "")
                if path:
                    devices.add(device_family(path))
        for source in container.get("mount_sources") or []:
            match = re.match(r"^/mnt/(nvme\d+n\d+)", str(source))
            if match:
                devices.add(f"/dev/{match.group(1)}")
    storage = inventory.get("storage") or {}
    for row in storage.get("path_filesystems") or []:
        if row.get("path") != "/var/lib/mysql":
            continue
        for filesystem in row.get("filesystems") or []:
            source = str(filesystem.get("source") or "")
            if source:
                devices.add(device_family(source))
    return sorted(devices)


def controller_mutex_contract() -> bool:
    text = CONTROLLER.read_text(encoding="utf-8")
    required = (
        'HOST_LANE_GROUPS = {',
        '"job_memory": "host02"',
        '"hybench_xp_io": "host02"',
        '"tpch_memory": "host04"',
        '"tpcc_cpu": "host04"',
        "fcntl.LOCK_EX | fcntl.LOCK_NB",
        'HOST_LANE_LOCK_ROOT / f"{host_group}.lock"',
    )
    return all(token in text for token in required)


def build_evidence(
    scenario: str,
    config: Mapping[str, str],
    capture_summary: Mapping[str, Any],
) -> dict[str, Any]:
    inventory_path = INVENTORY_ROOT / f"{config['host']}.inventory.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    source_path = ROOT / config["source"]
    source = json.loads(source_path.read_text(encoding="utf-8"))
    effective = source.get("effective_readback") or {}
    normalized = source.get("normalized_envelope") or {}
    target_cpus_raw = effective.get("cpuset_cpus")
    target_mems_raw = effective.get("cpuset_mems")
    target_cpus = cpu_set(target_cpus_raw)
    target_mems = cpu_set(target_mems_raw)
    processes = inventory.get("processes") or []
    containers = inventory.get("running_containers") or []
    process_cpu_conflicts = [
        {
            "pid": row.get("pid"),
            "comm": row.get("comm"),
            "cpus_allowed_list": row.get("cpus_allowed_list"),
        }
        for row in processes
        if target_cpus & cpu_set(row.get("cpus_allowed_list"))
    ]
    process_numa_conflicts = [
        {
            "pid": row.get("pid"),
            "comm": row.get("comm"),
            "mems_allowed_list": row.get("mems_allowed_list"),
        }
        for row in processes
        if target_mems & cpu_set(row.get("mems_allowed_list"))
    ]
    driver_processes = [
        {
            "pid": row.get("pid"),
            "comm": row.get("comm"),
        }
        for row in processes
        if str(row.get("comm") or "").lower() in DRIVER_COMMS
    ]
    mysql_target_containers = [
        str(row.get("name") or "")
        for row in containers
        if "mysql" in str(row.get("name") or "").lower()
        or "mysql" in str(row.get("image") or "").lower()
    ]
    host_capture = (capture_summary.get("hosts") or {}).get(config["host"]) or {}
    process_inventory_complete = (
        host_capture.get("passed") is True
        and len(processes) > 0
        and all(
            row.get("cpus_allowed_list") and row.get("mems_allowed_list")
            for row in processes
        )
        and all(
            row.get("cpuset_cpus") and row.get("cpuset_mems")
            for row in containers
        )
    )
    cpu_nonoverlap = bool(target_cpus) and not process_cpu_conflicts
    numa_nonoverlap = bool(target_mems) and not process_numa_conflicts
    target_no_swap = (
        int(normalized.get("memory_bytes") or 0) > 0
        and normalized.get("memory_bytes") == normalized.get("memory_swap_bytes")
        and effective.get("memory_limit_bytes")
        == effective.get("memory_memsw_limit_bytes")
    )
    co_resident_no_swap = all(
        int(row.get("memory") or 0) > 0
        and row.get("memory") == row.get("memory_swap")
        for row in containers
    )
    memory_swap_nonoverlap = (
        numa_nonoverlap and target_no_swap and co_resident_no_swap
    )
    co_resident_devices = infer_co_resident_devices(inventory)
    target_device_family = device_family(config["device"])
    physical_disk_nonoverlap = (
        bool(co_resident_devices)
        and target_device_family
        not in {device_family(item) for item in co_resident_devices}
    )
    target_runtime_stopped = not mysql_target_containers
    mutex_enforced = controller_mutex_contract()
    host_admission_passed = all(
        (
            process_inventory_complete,
            cpu_nonoverlap,
            numa_nonoverlap,
            memory_swap_nonoverlap,
            physical_disk_nonoverlap,
            not driver_processes,
            target_runtime_stopped,
            mutex_enforced,
        )
    )
    return {
        "schema_version": "mysql_only_static_isolation_evidence_v1",
        "freeze_id": FREEZE_ID,
        "captured_at": inventory.get("captured_at"),
        "scenario": scenario,
        "engine": "mysql",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "passed": host_admission_passed,
        "cpu_nonoverlap": cpu_nonoverlap,
        "numa_nonoverlap": numa_nonoverlap,
        "memory_swap_nonoverlap": memory_swap_nonoverlap,
        "physical_disk_nonoverlap": physical_disk_nonoverlap,
        "host_admission_passed": host_admission_passed,
        "process_inventory_complete": process_inventory_complete,
        "target_runtime_currently_stopped_or_isolated": target_runtime_stopped,
        "same_host_scenario_mutex_enforced": mutex_enforced,
        "no_active_benchmark_driver_pressure": not driver_processes,
        "target": {
            "cpuset_cpus": target_cpus_raw,
            "cpuset_mems": target_mems_raw,
            "device": config["device"],
            "device_family": target_device_family,
            "memory_bytes": normalized.get("memory_bytes"),
            "memory_swap_bytes": normalized.get("memory_swap_bytes"),
            "runtime_admission": (
                "stopped until static, dynamic, R0 bottleneck, and lease gates pass"
            ),
        },
        "co_resident": {
            "process_count": len(processes),
            "container_count": len(containers),
            "process_cpu_conflicts": process_cpu_conflicts,
            "process_numa_conflicts": process_numa_conflicts,
            "active_driver_processes": driver_processes,
            "target_container_candidates": mysql_target_containers,
            "devices": co_resident_devices,
            "container_names": [
                str(row.get("name") or "") for row in containers
            ],
        },
        "host_lane_mutex": {
            "group": config["host_group"],
            "lock_root": relative(
                ROOT
                / "history/mysql_sgd_env_agents_20260723"
                / "formal_host_lane_mutexes"
            ),
            "policy": "at most one formal MySQL campaign per physical host",
            "controller": pointer(CONTROLLER),
        },
        "sources": {
            "live_host_inventory": pointer(inventory_path),
            "inventory_capture_summary": pointer(
                INVENTORY_ROOT / "capture_summary.json"
            ),
            "target_effective_envelope_readback": pointer(source_path),
        },
    }


def update_checksum_index(bundle: Path, manifest_digest: str) -> None:
    checksum_path = bundle / "checksums.sha256"
    values: dict[str, str] = {}
    for line in checksum_path.read_text(encoding="ascii").splitlines():
        parts = line.split("  ", 1)
        if len(parts) == 2:
            values[parts[1]] = parts[0]
    sidecar = bundle / "readiness_manifest.json.sha256"
    values["readiness_manifest.json"] = manifest_digest
    values["readiness_manifest.json.sha256"] = sha256_file(sidecar)
    atomic_write(
        checksum_path,
        "".join(
            f"{values[name]}  {name}\n" for name in sorted(values)
        ).encode("ascii"),
    )


def promote(entries: Mapping[str, Mapping[str, str]]) -> None:
    manifest = json.loads(READINESS.read_text(encoding="utf-8"))
    for scenario, artifact_pointer in entries.items():
        manifest["scenarios"][scenario]["static_isolation"] = {
            "passed": True,
            "evidence": dict(artifact_pointer),
        }
    digest = write_json_with_sidecar(READINESS, manifest)
    update_checksum_index(READINESS.parent, digest)


def build(promote_evidence: bool) -> dict[str, Any]:
    capture_summary = json.loads(
        (INVENTORY_ROOT / "capture_summary.json").read_text(encoding="utf-8")
    )
    OUTPUT.mkdir(parents=True, exist_ok=True)
    entries: dict[str, dict[str, str]] = {}
    for scenario, config in SCENARIOS.items():
        evidence = build_evidence(scenario, config, capture_summary)
        if evidence["passed"] is not True:
            raise RuntimeError(
                f"{scenario} static admission failed: "
                f"{json.dumps(evidence, ensure_ascii=False, sort_keys=True)}"
            )
        path = OUTPUT / f"{scenario}.static_isolation_v1.json"
        write_json_with_sidecar(path, evidence)
        entries[scenario] = pointer(path)
    bundle = {
        "schema_version": "mysql_only_static_isolation_bundle_v1",
        "freeze_id": FREEZE_ID,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "status": "FROZEN_STATIC_ISOLATION_PASS",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "host_lane_mutex_controller": pointer(CONTROLLER),
        "evidence": entries,
    }
    write_json_with_sidecar(OUTPUT / "manifest.json", bundle)
    lines = [
        f"{sha256_file(path)}  {path.name}\n"
        for path in sorted(OUTPUT.iterdir())
        if path.is_file() and path.name != "checksums.sha256"
    ]
    atomic_write(OUTPUT / "checksums.sha256", "".join(lines).encode("ascii"))
    if promote_evidence:
        promote(entries)
    return {
        "status": "FROZEN_AND_PROMOTED" if promote_evidence else "FROZEN",
        "freeze_id": FREEZE_ID,
        "output": relative(OUTPUT),
        "evidence": entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--promote", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            build(args.promote),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
