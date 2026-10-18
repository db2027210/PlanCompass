#!/usr/bin/env python3
"""Single-command, local-only XP inventory and 60-second interference collector."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat as stat_module
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from local_capture.xp_capture_readiness import (
    INVENTORY_SCHEMA,
    ROLLING_GATE_SCHEMA,
)
from mysql_process_classification import classify_process


RELEVANT = ("mysqld", "postgres", "hybench", "benchbase", "dbworkload",
            "mysql_algorithm", "manual_mysql", "formal_job")
DATA_PLANE_CATEGORIES = frozenset({"mysql", "postgresql", "benchmark"})


def _is_data_plane(entry: Mapping[str, Any]) -> bool:
    return entry.get("category") in DATA_PLANE_CATEGORIES


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_sidecar(path: Path) -> Mapping[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"unsafe evidence input: {path}")
    sidecar = path.with_name(path.name + ".sha256")
    if (
        not sidecar.is_file()
        or sidecar.read_text(encoding="ascii")
        != f"{_sha(path)}  {path.name}\n"
    ):
        raise ValueError(f"invalid evidence input sidecar: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"evidence input is not an object: {path}")
    return value


def _atomic(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError(f"collector output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.partial")
    with temporary.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{_sha(path)}  {path.name}\n", encoding="ascii"
    )


def _parse_list(value: str) -> List[int]:
    result = set()
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            low, high = (int(part) for part in token.split("-", 1))
            result.update(range(low, high + 1))
        else:
            result.add(int(token))
    return sorted(result)


def _path_device(path: Path) -> str:
    metadata = path.stat()
    return f"{os.major(metadata.st_dev)}:{os.minor(metadata.st_dev)}"


def _open_regular_devices(directory: Path) -> List[str]:
    devices = set()
    try:
        descriptors = list((directory / "fd").iterdir())
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return []
    for descriptor in descriptors:
        try:
            metadata = descriptor.stat()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if stat_module.S_ISREG(metadata.st_mode):
            devices.add(
                f"{os.major(metadata.st_dev)}:{os.minor(metadata.st_dev)}"
            )
    return sorted(devices)


def _proc_sample(proc_root: Path = Path("/proc")) -> Dict[str, Any]:
    entries = []
    parent_by_pid: Dict[int, int] = {}
    for directory in proc_root.iterdir():
        if not directory.name.isdigit():
            continue
        try:
            raw_cmdline = (directory / "cmdline").read_bytes()
            argv = [
                item.decode("utf-8", errors="replace")
                for item in raw_cmdline.split(b"\0")
                if item
            ]
            command = " ".join(argv).strip()
            status = (directory / "status").read_text(encoding="utf-8")
            values = {}
            for line in status.splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    values[key] = value.strip()
            stat = (directory / "stat").read_text(encoding="ascii")
            rest = stat[stat.rfind(")") + 2 :].split()
            pid = int(directory.name)
            ppid = int(rest[1])
            parent_by_pid[pid] = ppid
            try:
                cwd = os.readlink(directory / "cwd")
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                cwd = ""
            category = classify_process(
                command, argv=argv, cwd=cwd or None
            )
            if category is None:
                continue
            io_values = {}
            for line in (directory / "io").read_text(encoding="ascii").splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    io_values[key] = int(value.strip())
            cwd_device = ""
            executable_device = ""
            try:
                cwd_device = _path_device(directory / "cwd")
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                pass
            try:
                executable_device = _path_device(directory / "exe")
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                pass
            row = {
                "pid": pid,
                "ppid": ppid,
                "category": category,
                "command": command,
                "argv": argv,
                "cwd": cwd,
                "processor": int(rest[36]),
                "cwd_device_major_minor": cwd_device,
                "executable_device_major_minor": executable_device,
                "open_regular_file_devices": _open_regular_devices(directory),
                "cpuset_cpus": values.get("Cpus_allowed_list", ""),
                "cpuset_mems": values.get("Mems_allowed_list", ""),
                "cgroup": (directory / "cgroup").read_text(
                    encoding="utf-8", errors="replace"
                ),
                "cpu_ticks": int(rest[11]) + int(rest[12]),
                "io_bytes": int(io_values.get("read_bytes", 0))
                + int(io_values.get("write_bytes", 0)),
            }
            if (
                category == "observer"
                and argv
                and Path(argv[-1]).name == "benchmark_monitor.py"
            ):
                row["observer_identity"] = "frozen_benchmark_monitor_sha256"
            entries.append(row)
        except (FileNotFoundError, ProcessLookupError, PermissionError, ValueError):
            continue
    return {
        "monotonic": time.monotonic(),
        "entries": entries,
        "process_tree_ppids": {
            str(pid): ppid for pid, ppid in parent_by_pid.items()
        },
    }


def _observer_violations(
    sample: Mapping[str, Any],
    *,
    xp_cpus: set[int],
    xp_device: str,
) -> List[Dict[str, Any]]:
    entries = {
        int(entry["pid"]): entry for entry in sample.get("entries", [])
    }
    parent_by_pid = {
        int(pid): int(ppid)
        for pid, ppid in (
            sample.get("process_tree_ppids") or {}
        ).items()
    }
    result: List[Dict[str, Any]] = []
    observers = {
        pid: entry
        for pid, entry in entries.items()
        if entry.get("observer_identity")
        == "frozen_benchmark_monitor_sha256"
    }
    for observer_pid, observer in observers.items():
        descendants = []
        for pid, entry in entries.items():
            if entry.get("category") not in {
                "mysql", "postgresql", "benchmark"
            }:
                continue
            current = int(parent_by_pid.get(pid, entry.get("ppid") or 0))
            visited = set()
            while current > 0 and current not in visited:
                if current == observer_pid:
                    descendants.append(pid)
                    break
                visited.add(current)
                current = int(parent_by_pid.get(current, 0))
        devices = {
            str(observer.get("cwd_device_major_minor") or ""),
            str(observer.get("executable_device_major_minor") or ""),
            *(
                str(value)
                for value in observer.get("open_regular_file_devices") or []
            ),
        }
        devices.discard("")
        reasons = []
        if descendants:
            reasons.append("owns_data_plane_descendant")
        if int(observer.get("processor") or -1) in xp_cpus:
            reasons.append("executing_on_target_cpu")
        if xp_device in devices:
            reasons.append("open_path_on_target_device")
        if (
            not observer.get("cwd")
            or not observer.get("cwd_device_major_minor")
            or not observer.get("executable_device_major_minor")
        ):
            reasons.append("observer_path_device_evidence_incomplete")
        result.append({
            "pid": observer_pid,
            "identity": observer["observer_identity"],
            "processor": observer.get("processor"),
            "path_devices": sorted(devices),
            "data_plane_descendant_pids": sorted(descendants),
            "passed": not reasons,
            "reasons": reasons,
        })
    return sorted(result, key=lambda row: int(row["pid"]))


def _same_identity(value: Mapping[str, Any], token: str) -> bool:
    return (
        value.get("launch_token") == token
        and value.get("hostname") == "compute00"
        and value.get("formal_scope") == "mysql_only"
        and value.get("transport") == "local_no_ssh"
    )


def _tree_device(
    pid: int,
    entries: Mapping[int, Mapping[str, Any]],
    authorities: Mapping[str, Any],
    process_devices: Mapping[int, str],
) -> Optional[str]:
    seen = set()
    current = pid
    while current > 0 and current not in seen:
        seen.add(current)
        if current in process_devices:
            return str(process_devices[current])
        record = authorities.get(str(current))
        row = entries.get(current)
        if record is not None:
            if (
                not isinstance(record, Mapping)
                or row is None
                or row.get("category") != "controller"
                or record.get("binding_verified") is not True
                or record.get("controller_command_sha256")
                != hashlib.sha256(
                    str(row.get("command") or "").encode("utf-8")
                ).hexdigest()
                or not str(record.get("device_major_minor") or "")
            ):
                return None
            return str(record["device_major_minor"])
        if row is None:
            return None
        current = int(row.get("ppid") or 0)
    return None


def collect_evidence(
    *,
    capture_spec_path: Path,
    capture_request_path: Path,
    shared_snapshot_path: Path,
    sample_provider: Callable[[], Mapping[str, Any]] = _proc_sample,
    sleeper: Callable[[float], None] = time.sleep,
    sample_count: int = 60,
) -> Dict[str, Any]:
    spec = _load_sidecar(capture_spec_path)
    request = _load_sidecar(capture_request_path)
    shared = _load_sidecar(shared_snapshot_path)
    token = str(spec.get("launch_token") or "")
    if (
        not token
        or request.get("schema_version")
        != "mysql_xp_capture_readiness_request_v1"
        or not _same_identity(request, token)
        or request.get("workload_started") is not False
        or request.get("target_mysql_port") != 65204
        or shared.get("schema_version") != "mysql_four_scene_shared_snapshot_v1"
        or shared.get("launch_token") != token
        or shared.get("hostname") != "compute00"
        or shared.get("formal_scope") != "mysql_only"
        or shared.get("cross_engine_comparison_prohibited") is not True
        or shared.get("transport") != "local_no_ssh"
        or shared.get("capture_only") is not True
        or shared.get("outer_execution_authorized") is not False
        or shared.get("inventory_complete") is not True
        or not isinstance(shared.get("container_groups"), Mapping)
        or not isinstance(shared.get("process_inventory"), list)
        or not isinstance(shared.get("observer_inventory"), Mapping)
        or not isinstance(shared.get("process_devices"), Mapping)
        or not all(
            isinstance(shared["container_groups"].get(name), list)
            for name in (
                "old_mysql_xp_containers",
                "postgresql_tpcc_containers",
                "postgresql_xp_containers",
            )
        )
    ):
        raise ValueError("collector inputs do not satisfy the same-token contract")
    readback = _load_sidecar(Path(str(request["resource_readback_path"])))
    source = json.loads(
        Path(str(readback["source_resource_readback_path"])).read_text(
            encoding="utf-8"
        )
    )
    container_id = str(source.get("container_id") or "")
    if len(container_id) < 12:
        raise ValueError("fresh target container id is absent")
    xp_cpus = set(_parse_list(spec["frozen_resource_envelope"]["cpuset_cpus"]))
    xp_device = str(spec["frozen_resource_envelope"]["io_device"])
    samples = [dict(sample_provider())]
    for _ in range(sample_count):
        sleeper(1.0)
        samples.append(dict(sample_provider()))
    ticks_per_second = float(os.sysconf("SC_CLK_TCK"))
    rows = []
    inventory_entries = list(samples[0]["entries"])
    inventory_by_pid = {
        int(entry["pid"]): entry for entry in inventory_entries
    }
    initial_observer_validations = _observer_violations(
        samples[0], xp_cpus=xp_cpus, xp_device=xp_device
    )
    shared_observer_pids = {
        int(pid) for pid in shared["observer_inventory"]
    }
    initial_observer_pids = {
        int(row["pid"]) for row in initial_observer_validations
    }
    if not shared_observer_pids.issubset(initial_observer_pids):
        raise ValueError(
            "shared snapshot observer identity does not reconcile to first sample"
        )
    if any(not row["passed"] for row in initial_observer_validations):
        raise ValueError(
            "exact observer violates CPU/device/descendant isolation: "
            + json.dumps(initial_observer_validations, sort_keys=True)
        )
    for group_name, containers in shared["container_groups"].items():
        for container in containers:
            if (
                not isinstance(container, Mapping)
                or not str(container.get("name") or "")
                or container.get("category") not in {
                    "mysql", "postgresql", "benchmark", "controller"
                }
                or container.get("status") not in {
                    "running", "stopped", "exited", "removed"
                }
            ):
                raise ValueError(
                    f"shared container inventory row is incomplete: {group_name}"
                )
            if container["status"] == "running":
                pid = container.get("pid")
                if (
                    isinstance(pid, bool)
                    or not isinstance(pid, int)
                    or pid not in inventory_by_pid
                    or inventory_by_pid[pid]["category"] != container["category"]
                ):
                    raise ValueError(
                        f"running shared container is not reconciled to /proc: "
                        f"{container['name']}"
                    )
    target_pids = {
        int(entry["pid"]) for sample in samples for entry in sample["entries"]
        if entry["category"] == "mysql" and container_id in entry["cgroup"]
    }
    if len(target_pids) != 1:
        raise ValueError("fresh target mysqld PID is absent or ambiguous")
    target_pid = next(iter(target_pids))
    shared_target = shared.get("target_xp")
    if (
        not isinstance(shared_target, Mapping)
        or shared_target.get("container_id") != container_id
        or shared_target.get("pid") != target_pid
        or shared_target.get("excluded_from_process_devices") is not True
        or str(target_pid) in shared["process_devices"]
    ):
        raise ValueError("shared snapshot target does not match fresh XP readback")
    process_devices = {
        int(pid): str(device)
        for pid, device in shared["process_devices"].items()
    }
    tree_authorities = shared.get("host_process_tree_authorities") or {}
    if not isinstance(tree_authorities, Mapping):
        raise ValueError("shared snapshot host process tree authority is invalid")
    initial_devices = dict(process_devices)
    for pid, entry in inventory_by_pid.items():
        if (
            pid != target_pid
            and entry["category"] in {"mysql", "postgresql", "benchmark"}
            and pid not in initial_devices
        ):
            resolved = _tree_device(
                pid, inventory_by_pid, tree_authorities, initial_devices
            )
            if resolved is not None:
                initial_devices[pid] = resolved
    missing_devices = [
        int(entry["pid"]) for entry in inventory_entries
        if int(entry["pid"]) != target_pid
        and entry["category"] in {"mysql", "postgresql", "benchmark"}
        and int(entry["pid"]) not in initial_devices
    ]
    if missing_devices:
        details = {
            str(pid): {
                "ppid": inventory_by_pid[pid].get("ppid"),
                "category": inventory_by_pid[pid].get("category"),
                "command": str(
                    inventory_by_pid[pid].get("command") or ""
                )[:500],
            }
            for pid in sorted(missing_devices)
        }
        raise ValueError(
            "shared snapshot lacks device authority for non-target processes: "
            + json.dumps(details, sort_keys=True)
        )
    max_gap = 0.0
    for before, after in zip(samples, samples[1:]):
        elapsed = float(after["monotonic"]) - float(before["monotonic"])
        if elapsed <= 0:
            raise ValueError("non-monotonic collector sample")
        max_gap = max(max_gap, elapsed)
        old = {int(entry["pid"]): entry for entry in before["entries"]}
        new = {int(entry["pid"]): entry for entry in after["entries"]}
        common = set(old) & set(new) - {target_pid}
        resolved_devices = dict(process_devices)
        for pid in common:
            if pid not in resolved_devices:
                resolved = _tree_device(
                    pid, new, tree_authorities, resolved_devices
                )
                if resolved is not None:
                    resolved_devices[pid] = resolved
        unproven_new_relevant = {
            pid for pid in common
            if new[pid]["category"] in {"mysql", "postgresql", "benchmark"}
            and pid not in resolved_devices
        }
        interfering = {
            pid for pid in common
            if _is_data_plane(new[pid])
            and (
                xp_cpus.intersection(_parse_list(new[pid]["cpuset_cpus"]))
                or resolved_devices.get(pid) == xp_device
            )
        }
        ticks = sum(
            max(0, int(new[pid]["cpu_ticks"]) - int(old[pid]["cpu_ticks"]))
            for pid in interfering
        )
        io_bytes = sum(
            max(0, int(new[pid]["io_bytes"]) - int(old[pid]["io_bytes"]))
            for pid in interfering
        )
        overlap_pids = {
            pid for pid, entry in new.items()
            if pid != target_pid
            and entry["category"] in {"mysql", "postgresql", "benchmark"}
            and xp_cpus.intersection(_parse_list(entry["cpuset_cpus"]))
        }
        observer_validations = _observer_violations(
            after, xp_cpus=xp_cpus, xp_device=xp_device
        )
        observer_violations = [
            row for row in observer_validations if not row["passed"]
        ]
        rows.append({
            "elapsed_seconds": elapsed,
            "co_resident_cpu_pct": ticks / ticks_per_second / elapsed * 100.0,
            "co_resident_io_bps": io_bytes / elapsed,
            "cpuset_overlap_process_count": len(overlap_pids),
            "shared_device_process_count": sum(
                _is_data_plane(new[pid])
                and resolved_devices.get(pid) == xp_device
                for pid in common
            ),
            "unexpected_db_or_benchmark_process_count":
                len(overlap_pids | unproven_new_relevant),
            "observer_isolation_violation_count": len(observer_violations),
            "observer_validations": observer_validations,
            "target_mysql_present": target_pid in new,
        })
    thresholds = {
        "max_co_resident_cpu_pct": 5.0,
        "max_co_resident_io_bps": 1000000,
        "max_cpuset_overlap_process_count": 0,
        "max_shared_device_process_count": 0,
        "max_unexpected_db_or_benchmark_process_count": 0,
        "max_observer_isolation_violation_count": 0,
    }
    observed = {
        "max_co_resident_cpu_pct": max(row["co_resident_cpu_pct"] for row in rows),
        "max_co_resident_io_bps": max(row["co_resident_io_bps"] for row in rows),
        "max_cpuset_overlap_process_count":
            max(row["cpuset_overlap_process_count"] for row in rows),
        "max_shared_device_process_count":
            max(row["shared_device_process_count"] for row in rows),
        "max_unexpected_db_or_benchmark_process_count":
            max(row["unexpected_db_or_benchmark_process_count"] for row in rows),
        "max_observer_isolation_violation_count":
            max(row["observer_isolation_violation_count"] for row in rows),
        "maximum_gap_seconds": max_gap,
        "target_mysql_present_all_samples":
            all(row["target_mysql_present"] for row in rows),
    }
    passed = (
        len(rows) >= 60
        and max_gap <= 1.5
        and observed["target_mysql_present_all_samples"] is True
        and all(
            observed[name] <= limit for name, limit in thresholds.items()
        )
    )
    common = {
        "hostname": "compute00",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "transport": "local_no_ssh",
        "launch_token": token,
    }
    unexpected = [
        entry for entry in inventory_entries
        if int(entry["pid"]) != target_pid
        and entry["category"] in {"mysql", "postgresql", "benchmark"}
        and xp_cpus.intersection(_parse_list(entry["cpuset_cpus"]))
    ]
    inventory = {
        **common,
        "schema_version": INVENTORY_SCHEMA,
        "complete": True,
        "all_database_and_benchmark_processes_inventoried": True,
        "inventory_scope": [
            "mysql", "postgresql", "benchmark", "controller", "observer"
        ],
        "entries": inventory_entries,
        "observer_validations": initial_observer_validations,
        "shared_snapshot_process_inventory": shared["process_inventory"],
        "shared_snapshot_observer_inventory": shared["observer_inventory"],
        "shared_snapshot_containers": shared["container_groups"],
        "shared_snapshot_process_devices": shared["process_devices"],
        "shared_snapshot_host_process_tree_authorities":
            shared.get("host_process_tree_authorities") or {},
        "shared_snapshot_container_reconciliation_verified": True,
        "unexpected_unisolated_processes": unexpected,
        "target_mysql_ready": target_pid in {
            int(entry["pid"]) for entry in inventory_entries
        },
        "target_mysql_port": 65204,
    }
    rolling = {
        **common,
        "schema_version": ROLLING_GATE_SCHEMA,
        "passed": passed,
        "window_complete": len(rows) >= 60,
        "thresholds_version": "mysql_xp_local_capture_dynamic_v1",
        "window_seconds": 60,
        "sample_count": len(rows),
        "thresholds": thresholds,
        "observed_metrics": observed,
        "samples": rows,
    }
    inventory_path = Path(str(spec["inventory_path"]))
    rolling_path = Path(str(spec["rolling_gate_path"]))
    _atomic(inventory_path, inventory)
    _atomic(rolling_path, rolling)
    return {
        "inventory_path": str(inventory_path),
        "rolling_gate_path": str(rolling_path),
        "passed": passed,
        "observed_metrics": observed,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-spec", type=Path, required=True)
    parser.add_argument("--capture-readiness-request", type=Path, required=True)
    parser.add_argument("--shared-snapshot", type=Path, required=True)
    args = parser.parse_args(argv)
    result = collect_evidence(
        capture_spec_path=args.capture_spec,
        capture_request_path=args.capture_readiness_request,
        shared_snapshot_path=args.shared_snapshot,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
