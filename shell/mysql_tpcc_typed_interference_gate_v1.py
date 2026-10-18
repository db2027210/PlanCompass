#!/usr/bin/env python3
"""Fail-closed 60-second local non-interference gate for TPC-C trace capture."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import time
import re
from typing import Any, Mapping

try:
    from mysql_process_classification import classify_process
except ModuleNotFoundError:
    # The development tree retains the version suffix; the immutable bundle
    # publishes the same bytes under the closure-bound generic module name.
    try:
        from mysql_process_classification_v10 import classify_process
    except ModuleNotFoundError:
        from shell.mysql_process_classification_v10 import classify_process


SCHEMA = "mysql_tpcc_typed_dynamic_interference_gate_v1"
EXPECTED_CPUSET = "208-215,464-471"
EXPECTED_MEMS = "1"
EXPECTED_DEVICE = "/dev/sda"
EXPECTED_PORT = 65206
DATA_PLANE = frozenset({"mysql", "postgresql", "benchmark"})
RELEVANT = frozenset({*DATA_PLANE, "controller"})


class DiagnosticGateError(RuntimeError):
    """A fail-closed collection error that retains reset evidence."""

    def __init__(self, message: str, diagnostics: Mapping[str, Any]):
        super().__init__(message)
        self.diagnostics = dict(diagnostics)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_cpu_list(value: str) -> set[int]:
    result: set[int] = set()
    for raw in value.split(","):
        token = raw.strip()
        if not token:
            continue
        if "-" in token:
            low, high = (int(part) for part in token.split("-", 1))
            if low > high:
                raise ValueError(f"invalid CPU range: {token}")
            result.update(range(low, high + 1))
        else:
            result.add(int(token))
    if not result:
        raise ValueError("empty CPU list")
    return result


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise RuntimeError(f"fresh gate output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with temporary.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    sidecar = path.with_name(path.name + ".sha256")
    sidecar.write_text(f"{sha256(path)}  {path.name}\n", encoding="ascii")


def load_object(path: Path) -> Mapping[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"required regular file is absent: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def status_values(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            result[key] = value.strip()
    return result


def normalize_device(value: str) -> str:
    sysfs = Path("/sys/dev/block") / value
    if not sysfs.exists():
        return value
    resolved = sysfs.resolve()
    if (resolved / "partition").is_file():
        parent_device = resolved.parent / "dev"
        if parent_device.is_file():
            return parent_device.read_text(encoding="ascii").strip()
    return value


def device_from_stat(value: os.stat_result) -> str:
    number = value.st_rdev if stat.S_ISBLK(value.st_mode) else value.st_dev
    return normalize_device(f"{os.major(number)}:{os.minor(number)}")


def fd_devices(directory: Path) -> list[str]:
    devices: set[str] = set()
    for descriptor in (directory / "fd").iterdir():
        try:
            value = descriptor.stat()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if stat.S_ISREG(value.st_mode) or stat.S_ISBLK(value.st_mode):
            devices.add(device_from_stat(value))
    return sorted(devices)


def anonymous_numa_nodes(directory: Path) -> list[int]:
    nodes: set[int] = set()
    for line in (directory / "numa_maps").read_text(
        encoding="utf-8", errors="replace"
    ).splitlines():
        if " anon=" not in f" {line}" or " file=" in f" {line}":
            continue
        nodes.update(
            int(value)
            for value in re.findall(r"(?:^| )N([0-9]+)=[0-9]+(?: |$)", line)
        )
    return sorted(nodes)


def anonymous_numa_policies(directory: Path) -> list[str]:
    return sorted(
        {
            fields[1]
            for line in (directory / "numa_maps").read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
            if (
                " anon=" in f" {line}"
                and " file=" not in f" {line}"
                and len(fields := line.split()) > 1
            )
        }
    )


def owned_numa_isolated(row: Mapping[str, Any]) -> bool:
    nodes = set(int(value) for value in row.get("anonymous_numa_nodes") or [])
    policies = set(str(value) for value in row.get("anonymous_numa_policies") or [])
    return (
        row.get("cpuset_cpus") == EXPECTED_CPUSET
        and nodes.issubset({1})
        and policies.issubset({"bind:1"})
    )


def process_cwd(directory: Path) -> Path | None:
    """Resolve one live process cwd; absence stays fail-closed for observers."""
    try:
        cwd = (directory / "cwd").resolve(strict=True)
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        return None
    return cwd if cwd.is_dir() else None


def classify_proc_command(
    directory: Path, command: str, argv: list[str]
) -> tuple[str | None, str | None]:
    cwd = process_cwd(directory)
    return (
        classify_process(command, argv=argv, cwd=cwd),
        str(cwd) if cwd is not None else None,
    )


def is_vm_data_plane(command: str, argv: list[str]) -> bool:
    """Treat QEMU/KVM guests as data-plane candidates, never invisible noise."""
    if not argv:
        return False
    executable = Path(argv[0]).name.lower()
    return (
        executable.startswith("qemu-system")
        or executable in {"qemu-kvm", "kvm"}
        or "qemu-system-" in command.lower()
    )


def proc_sample(
    controller_pid: int, *, include_device_authority: bool
) -> dict[str, Any]:
    sample_started = time.monotonic()
    sample_started_epoch_ns = time.time_ns()
    entries: dict[int, dict[str, Any]] = {}
    for directory in Path("/proc").iterdir():
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
            stat_parts = (directory / "stat").read_text(
                encoding="ascii"
            )
            rest = stat_parts[stat_parts.rfind(")") + 2 :].split()
            pid = int(directory.name)
            category, cwd = classify_proc_command(
                directory, command, argv
            )
            vm_data_plane = is_vm_data_plane(command, argv)
            if vm_data_plane:
                category = "benchmark"
            entries[pid] = {
                "pid": pid,
                "ppid": int(rest[1]),
                "start_ticks": int(rest[19]),
                "category": category,
                "command": command,
                "cwd": cwd,
                "vm_data_plane_candidate": vm_data_plane,
            }
        except (
            FileNotFoundError,
            PermissionError,
            ProcessLookupError,
            ValueError,
        ):
            continue
    owned = descendants(entries, controller_pid)
    selected = {
        pid
        for pid, row in entries.items()
        if row.get("category") in RELEVANT or pid in owned
    }
    detail = set(selected)
    for pid in list(selected):
        current = int(entries[pid].get("ppid") or 0)
        visited = {pid}
        while current > 0 and current not in visited and current in entries:
            visited.add(current)
            detail.add(current)
            current = int(entries[current].get("ppid") or 0)
    disappeared: set[int] = set()
    for pid in detail:
        directory = Path("/proc") / str(pid)
        try:
            status = status_values(directory / "status")
            io_values: dict[str, int] = {}
            for line in (directory / "io").read_text(
                encoding="ascii"
            ).splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    io_values[key] = int(value.strip())
            entries[pid].update(
                {
                    "cpuset_cpus": status.get("Cpus_allowed_list", ""),
                    "cpuset_mems": status.get("Mems_allowed_list", ""),
                    "cgroup": (directory / "cgroup").read_text(
                        encoding="utf-8", errors="replace"
                    ),
                    "cpu_ticks": int(
                        (
                            (directory / "stat")
                            .read_text(encoding="ascii")
                            .split(") ", 1)[1]
                            .split()[11]
                        )
                    )
                    + int(
                        (
                            (directory / "stat")
                            .read_text(encoding="ascii")
                            .split(") ", 1)[1]
                            .split()[12]
                        )
                    ),
                    "io_bytes": int(io_values.get("read_bytes", 0))
                    + int(io_values.get("write_bytes", 0)),
                    "fd_devices": (
                        fd_devices(directory)
                        if include_device_authority
                        else []
                    ),
                    "anonymous_numa_nodes": (
                        anonymous_numa_nodes(directory)
                        if pid in owned
                        else []
                    ),
                    "anonymous_numa_policies": (
                        anonymous_numa_policies(directory)
                        if pid in owned
                        else []
                    ),
                }
            )
        except (
            FileNotFoundError,
            PermissionError,
            ProcessLookupError,
            ValueError,
        ):
            disappeared.add(pid)
    for pid in disappeared:
        entries.pop(pid, None)
    sample_finished = time.monotonic()
    return {
        "monotonic": sample_finished,
        "epoch_ns": time.time_ns(),
        "sample_started_epoch_ns": sample_started_epoch_ns,
        "proc_sample_duration_seconds": sample_finished - sample_started,
        "include_device_authority": include_device_authority,
        "entries": list(entries.values()),
    }


def descendants(entries: Mapping[int, Mapping[str, Any]], root: int) -> set[int]:
    owned = {root}
    changed = True
    while changed:
        changed = False
        for pid, entry in entries.items():
            if pid not in owned and int(entry["ppid"]) in owned:
                owned.add(pid)
                changed = True
    return owned


def inherited_devices(
    pid: int,
    entries: Mapping[int, Mapping[str, Any]],
    container_devices: Mapping[str, str],
) -> list[str]:
    row = entries[pid]
    devices = set(str(value) for value in row.get("fd_devices") or [])
    cgroup = str(row.get("cgroup") or "")
    for identifier, device in container_devices.items():
        if identifier in cgroup or identifier[:12] in cgroup:
            devices.add(device)
    current = int(row.get("ppid") or 0)
    visited = {pid}
    while not devices and current > 0 and current not in visited:
        visited.add(current)
        parent = entries.get(current)
        if parent is None:
            break
        devices.update(str(value) for value in parent.get("fd_devices") or [])
        current = int(parent.get("ppid") or 0)
    return sorted(devices)


def path_device(path: Path) -> str:
    return device_from_stat(path.stat())


def docker_inventory() -> tuple[list[dict[str, Any]], dict[str, str]]:
    completed = subprocess.run(
        ["docker", "ps", "-a", "--no-trunc", "--format", "{{.ID}}"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    identifiers = [
        line for line in completed.stdout.splitlines() if line
    ]
    inspections: list[Mapping[str, Any]] = []
    if identifiers:
        inspected = subprocess.run(
            ["docker", "inspect", *identifiers],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
        decoded = json.loads(inspected.stdout)
        if not isinstance(decoded, list) or len(decoded) != len(identifiers):
            raise RuntimeError("Docker inventory cardinality differs")
        inspections = decoded
    rows: list[dict[str, Any]] = []
    device_by_id: dict[str, str] = {}
    for value in inspections:
        state = value.get("State") or {}
        mounts = list(value.get("Mounts") or [])
        preferred = [
            mount
            for mount in mounts
            if mount.get("Source")
            and (
                mount.get("Destination") == "/var/lib/mysql"
                or "postgres" in str(mount.get("Destination") or "").lower()
            )
        ]
        authority_path: str | None = None
        if preferred:
            authority_path = str(preferred[0]["Source"])
        else:
            upper = (
                (value.get("GraphDriver") or {}).get("Data") or {}
            ).get("UpperDir")
            if upper:
                authority_path = str(upper)
        row: dict[str, Any] = {
            "container_id": str(value.get("Id") or ""),
            "name": str(value.get("Name") or "").lstrip("/"),
            "image": str((value.get("Config") or {}).get("Image") or ""),
            "running": state.get("Running") is True,
            "pid": int(state.get("Pid") or 0),
            "authority_path": authority_path,
        }
        if authority_path and Path(authority_path).exists():
            row["device_major_minor"] = path_device(Path(authority_path))
            device_by_id[row["container_id"]] = row["device_major_minor"]
        rows.append(row)
    return rows, device_by_id


def identity(row: Mapping[str, Any]) -> tuple[int, int]:
    return int(row["pid"]), int(row["start_ticks"])


def proven_disjoint_mysql_client(
    row: Mapping[str, Any], target_cpus: set[int]
) -> bool:
    """Exclude only a TCP client whose execution lane is provably disjoint.

    The server/container remains in the relevant identity set and retains
    device-authority checks.  This narrow exception prevents harmless
    reconnects by another isolated MySQL lane's counter sampler from resetting
    the whole rolling window.
    """
    command = str(row.get("command") or "")
    if (
        row.get("category") != "mysql"
        or re.search(r"(?:^|/| )mysqld(?: |$)", command)
        or not re.search(r"(?:^|/| )mysql(?: |$)", command)
    ):
        return False
    port = re.search(r"(?:--port(?:=| )|-P ?)([0-9]+)", command)
    if port is None or int(port.group(1)) == EXPECTED_PORT:
        return False
    try:
        cpus = parse_cpu_list(str(row.get("cpuset_cpus") or ""))
    except ValueError:
        return False
    return not target_cpus.intersection(cpus)


def relevant_identities(
    sample: Mapping[str, Any],
    controller_pid: int,
    target_pid: int,
    target_cpus: set[int],
) -> set[tuple[int, int]]:
    entries = {int(row["pid"]): row for row in sample["entries"]}
    owned = (
        descendants(entries, controller_pid)
        if controller_pid in entries
        else set()
    )
    return {
        identity(row)
        for pid, row in entries.items()
        if row.get("category") in RELEVANT
        and pid != target_pid
        and pid not in owned
        and not proven_disjoint_mysql_client(row, target_cpus)
    }


def identity_document(
    row: Mapping[str, Any],
    entries: Mapping[int, Mapping[str, Any]],
    container_devices: Mapping[str, str],
) -> dict[str, Any]:
    pid = int(row["pid"])
    devices = (
        inherited_devices(pid, entries, container_devices)
        if pid in entries
        else sorted(str(value) for value in row.get("fd_devices") or [])
    )
    return {
        "pid": pid,
        "ppid": int(row.get("ppid") or 0),
        "start_ticks": int(row["start_ticks"]),
        "category": row.get("category"),
        "command": str(row.get("command") or ""),
        "cwd": row.get("cwd"),
        "cpuset_cpus": row.get("cpuset_cpus"),
        "cpuset_mems": row.get("cpuset_mems"),
        "cgroup": str(row.get("cgroup") or ""),
        "device_authority": devices,
        "vm_data_plane_candidate":
            row.get("vm_data_plane_candidate") is True,
    }


def relevant_identity_documents(
    sample: Mapping[str, Any],
    controller_pid: int,
    target_pid: int,
    target_cpus: set[int],
    container_devices: Mapping[str, str],
) -> dict[tuple[int, int], dict[str, Any]]:
    entries = {int(row["pid"]): row for row in sample["entries"]}
    identities = relevant_identities(
        sample, controller_pid, target_pid, target_cpus
    )
    return {
        identity(row): {
            **identity_document(row, entries, container_devices),
            "device_authority_complete":
                sample.get("include_device_authority") is True,
        }
        for row in entries.values()
        if identity(row) in identities
    }


def postgresql_related(row: Mapping[str, Any]) -> bool:
    """Recognize the co-resident PostgreSQL lane without trusting one label.

    Some PostgreSQL backends are intentionally classified as ``benchmark``
    because their command includes the HyBench database name.  Restore and
    measurement wrappers likewise belong to the PostgreSQL data plane even
    though their executable is bash or Python.  Every accepted marker below
    is therefore PostgreSQL-specific rather than a generic ``hybench`` token.
    """
    if row.get("category") == "postgresql":
        return True
    evidence = " ".join(
        str(row.get(name) or "")
        for name in ("command", "cwd", "cgroup")
    ).lower()
    return (
        evidence.startswith("postgres:")
        or " stmt_postgres." in evidence
        or "/pg5_" in evidence
        or "/pg5/" in evidence
        or "pg5v1_" in evidence
        or "safe_restore_hybench" in evidence
    )


def isolation_decision(
    row: Mapping[str, Any],
    *,
    target_cpus: set[int],
    target_device: str,
) -> dict[str, Any]:
    """Return an evidence-bearing decision for one co-resident identity."""
    try:
        cpus = parse_cpu_list(str(row.get("cpuset_cpus") or ""))
    except (TypeError, ValueError):
        return {
            "action": "fail_closed",
            "reason": "cpu_authority_unknown",
        }
    overlap = sorted(target_cpus.intersection(cpus))
    if overlap:
        return {
            "action": "fail_closed",
            "reason": "cpu_overlap",
            "overlap_cpus": overlap,
        }
    devices = sorted(
        set(str(value) for value in row.get("device_authority") or [])
    )
    if not devices:
        return {
            "action": "fail_closed",
            "reason": "device_authority_unknown",
        }
    if target_device in devices:
        return {
            "action": "fail_closed",
            "reason": "shared_target_device",
            "device_authority": devices,
        }
    if postgresql_related(row):
        return {
            "action": "observe_only",
            "reason": "isolated_postgresql_identity",
            "device_authority": devices,
        }
    return {
        "action": "retain_relevant",
        "reason": "non_postgresql_identity",
        "device_authority": devices,
    }


def churn_record(
    *,
    previous_sample: Mapping[str, Any],
    current_sample: Mapping[str, Any],
    previous_identities: Mapping[tuple[int, int], Mapping[str, Any]],
    current_identities: Mapping[tuple[int, int], Mapping[str, Any]],
    target_cpus: set[int],
    target_device: str,
) -> dict[str, Any]:
    previous_keys = set(previous_identities)
    current_keys = set(current_identities)
    added = [
        dict(current_identities[key])
        for key in sorted(current_keys - previous_keys)
    ]
    removed = [
        dict(previous_identities[key])
        for key in sorted(previous_keys - current_keys)
    ]
    events: list[dict[str, Any]] = []
    for direction, rows in (("added", added), ("removed", removed)):
        for row in rows:
            events.append(
                {
                    "direction": direction,
                    "identity": row,
                    "decision": isolation_decision(
                        row,
                        target_cpus=target_cpus,
                        target_device=target_device,
                    ),
                }
            )
    actions = {str(row["decision"]["action"]) for row in events}
    if "fail_closed" in actions:
        action = "fail_closed"
    elif actions == {"observe_only"}:
        action = "observe_only"
    else:
        action = "reset"
    return {
        "epoch_ns": current_sample.get("epoch_ns"),
        "sample_gap_seconds": (
            float(current_sample["monotonic"])
            - float(previous_sample["monotonic"])
        ),
        "action": action,
        "reason": {
            "fail_closed": "unsafe_co_resident_identity_churn",
            "observe_only": "isolated_postgresql_identity_churn",
            "reset": "relevant_identity_churn",
        }[action],
        "events": events,
        "added_identities": added,
        "removed_identities": removed,
        "previous_identity_count": len(previous_keys),
        "current_identity_count": len(current_keys),
    }


def reset_record(
    *,
    reason: str,
    previous_sample: Mapping[str, Any],
    current_sample: Mapping[str, Any],
    stable_samples: list[Mapping[str, Any]],
    previous_identities: Mapping[tuple[int, int], Mapping[str, Any]],
    current_identities: Mapping[tuple[int, int], Mapping[str, Any]],
) -> dict[str, Any]:
    if reason not in {"sample_gap", "relevant_identity_churn"}:
        raise ValueError(f"unsupported reset reason: {reason}")
    previous_keys = set(previous_identities)
    current_keys = set(current_identities)
    stable_duration = (
        float(previous_sample["monotonic"])
        - float(stable_samples[0]["monotonic"])
        if stable_samples
        else 0.0
    )
    return {
        "reset_index": 0,
        "reason": reason,
        "epoch_ns": current_sample.get("epoch_ns"),
        "sample_gap_seconds": (
            float(current_sample["monotonic"])
            - float(previous_sample["monotonic"])
        ),
        "previous_proc_sample_duration_seconds":
            previous_sample.get("proc_sample_duration_seconds"),
        "current_proc_sample_duration_seconds":
            current_sample.get("proc_sample_duration_seconds"),
        "stable_sample_count": len(stable_samples),
        "stable_interval_count": max(0, len(stable_samples) - 1),
        "stable_duration_seconds": stable_duration,
        "added_identities": [
            dict(current_identities[key])
            for key in sorted(current_keys - previous_keys)
        ],
        "removed_identities": [
            dict(previous_identities[key])
            for key in sorted(previous_keys - current_keys)
        ],
        "previous_identity_count": len(previous_keys),
        "current_identity_count": len(current_keys),
    }


def deadline_summary(
    *,
    samples: list[Mapping[str, Any]],
    resets: list[Mapping[str, Any]],
    observed_churn: list[Mapping[str, Any]] | None = None,
    fail_closed_events: list[Mapping[str, Any]] | None = None,
    sample_count: int,
    deadline_seconds: float,
) -> dict[str, Any]:
    reasons = {
        name: sum(
            name in (row.get("triggers") or [row.get("reason")])
            for row in resets
        )
        for name in ("sample_gap", "relevant_identity_churn")
    }
    return {
        "schema_version":
            "mysql_tpcc_typed_gate_deadline_diagnostics_v2",
        "deadline_seconds": deadline_seconds,
        "required_interval_count": sample_count,
        "maximum_sample_gap_seconds": 1.5,
        "reset_count": len(resets),
        "reset_count_by_reason": reasons,
        "observed_isolated_postgresql_churn_count":
            len(observed_churn or []),
        "fail_closed_event_count": len(fail_closed_events or []),
        "current_stable_sample_count": len(samples),
        "current_stable_interval_count": max(0, len(samples) - 1),
        "current_stable_duration_seconds": (
            float(samples[-1]["monotonic"])
            - float(samples[0]["monotonic"])
            if samples
            else 0.0
        ),
        "maximum_proc_sample_duration_seconds": max(
            (
                float(row.get("proc_sample_duration_seconds") or 0.0)
                for row in samples
            ),
            default=0.0,
        ),
        "reset_journal": [dict(row) for row in resets],
        "observed_churn_journal": [
            dict(row) for row in (observed_churn or [])
        ],
        "fail_closed_events": [
            dict(row) for row in (fail_closed_events or [])
        ],
    }


def interval_interference(
    *,
    before: Mapping[int, Mapping[str, Any]],
    after: Mapping[int, Mapping[str, Any]],
    owned: set[int],
    target_pid: int,
    initial_relevant: set[tuple[int, int]],
    target_cpus: set[int],
    target_device: str,
    device_authority: Mapping[tuple[int, int], list[str]],
    elapsed: float,
    ticks_per_second: float,
) -> dict[str, Any]:
    overlap: set[int] = set()
    active_shared_device: set[int] = set()
    shared_device: set[int] = set()
    cpu_ticks = 0
    io_bytes = 0
    for pid in set(before) & set(after):
        row = after[pid]
        if (
            row.get("category") not in RELEVANT
            or pid == target_pid
            or pid in owned
            or identity(row) not in initial_relevant
        ):
            continue
        cpus = parse_cpu_list(str(row.get("cpuset_cpus") or ""))
        if target_cpus.intersection(cpus):
            overlap.add(pid)
            cpu_ticks += max(
                0, int(row["cpu_ticks"]) - int(before[pid]["cpu_ticks"])
            )
        devices = set(device_authority.get(identity(row)) or [])
        if target_device in devices:
            shared_device.add(pid)
            delta = max(
                0, int(row["io_bytes"]) - int(before[pid]["io_bytes"])
            )
            if delta:
                active_shared_device.add(pid)
                io_bytes += delta
    return {
        "cpu_overlap_pids": sorted(overlap),
        "cpu_overlap_pct": cpu_ticks
        / ticks_per_second
        / max(elapsed, 1e-9)
        * 100.0,
        "shared_device_pids": sorted(shared_device),
        "active_shared_device_pids": sorted(active_shared_device),
        "co_resident_shared_device_io_bps":
            io_bytes / max(elapsed, 1e-9),
    }


def collect(
    *,
    readback_path: Path,
    controller_pid: int,
    capture_token: str,
    sample_count: int = 60,
) -> dict[str, Any]:
    if socket.gethostname() != "compute00":
        raise RuntimeError("TPC-C gate is restricted to local compute00")
    if os.geteuid() != 0:
        raise RuntimeError("TPC-C gate requires root /proc evidence access")
    if controller_pid <= 1:
        raise RuntimeError("invalid capture controller PID")
    readback = load_object(readback_path)
    effective = readback.get("effective_cgroup") or {}
    container_id = str(readback.get("container_id") or "")
    target_device = path_device(Path(EXPECTED_DEVICE))
    if (
        len(container_id) < 12
        or readback.get("verified") is not True
        or effective.get("cpuset_cpus") != EXPECTED_CPUSET
        or effective.get("cpuset_mems") != EXPECTED_MEMS
        or effective.get("device_major_minor") != target_device
        or effective.get("io_read_bps") != 1205862400
        or effective.get("io_write_bps") != 1205862400
    ):
        raise RuntimeError("TPC-C resource readback differs from capture envelope")

    containers, container_devices = docker_inventory()
    samples = [
        proc_sample(controller_pid, include_device_authority=True)
    ]
    first_entries = {
        int(row["pid"]): row for row in samples[0]["entries"]
    }
    first_target_rows = [
        row
        for row in first_entries.values()
        if row.get("category") == "mysql"
        and (
            container_id in str(row.get("cgroup") or "")
            or container_id[:12] in str(row.get("cgroup") or "")
        )
    ]
    if len(first_target_rows) != 1:
        raise RuntimeError("target TPC-C mysqld is absent or ambiguous")
    sampling_target_pid = int(first_target_rows[0]["pid"])
    target_cpus = parse_cpu_list(EXPECTED_CPUSET)
    stable_relevant = relevant_identities(
        samples[0], controller_pid, sampling_target_pid, target_cpus
    )
    stable_identity_documents = relevant_identity_documents(
        samples[0],
        controller_pid,
        sampling_target_pid,
        target_cpus,
        container_devices,
    )
    target_device = path_device(Path(EXPECTED_DEVICE))
    initial_unsafe = [
        {
            "identity": document,
            "decision": decision,
        }
        for document in stable_identity_documents.values()
        if (
            decision := isolation_decision(
                document,
                target_cpus=target_cpus,
                target_device=target_device,
            )
        )["action"] == "fail_closed"
    ]
    if initial_unsafe:
        raise DiagnosticGateError(
            "TPC-C gate found unsafe co-resident identity in initial sample",
            deadline_summary(
                samples=samples,
                resets=[],
                observed_churn=[],
                fail_closed_events=initial_unsafe,
                sample_count=sample_count,
                deadline_seconds=150.0,
            ),
        )
    stable_blocking = {
        key
        for key, document in stable_identity_documents.items()
        if isolation_decision(
            document,
            target_cpus=target_cpus,
            target_device=target_device,
        )["action"] != "observe_only"
    }
    rolling_window_resets = 0
    reset_journal: list[dict[str, Any]] = []
    observed_churn_journal: list[dict[str, Any]] = []
    fail_closed_events: list[dict[str, Any]] = []
    sampling_deadline_seconds = 150.0
    sampling_deadline = time.monotonic() + sampling_deadline_seconds
    next_sample_start = time.monotonic() + 0.7
    while (
        len(samples) < sample_count + 1
        or float(samples[-1]["monotonic"])
        - float(samples[0]["monotonic"])
        < 60.0
    ):
        if time.monotonic() >= sampling_deadline:
            raise DiagnosticGateError(
                "TPC-C gate could not obtain 60 consecutive <=1.5s "
                "intervals",
                deadline_summary(
                    samples=samples,
                    resets=reset_journal,
                    observed_churn=observed_churn_journal,
                    fail_closed_events=fail_closed_events,
                    sample_count=sample_count,
                    deadline_seconds=sampling_deadline_seconds,
                ),
            )
        delay = next_sample_start - time.monotonic()
        if delay > 0.0:
            time.sleep(delay)
        sample = proc_sample(
            controller_pid, include_device_authority=True
        )
        next_sample_start += 1.0
        sample_gap = (
            float(sample["monotonic"])
            - float(samples[-1]["monotonic"])
            > 1.5
        )
        live_entries = {
            int(row["pid"]): row for row in sample["entries"]
        }
        live_target = live_entries.get(sampling_target_pid)
        if (
            live_target is None
            or identity(live_target)
            != identity(first_target_rows[0])
            or live_target.get("cpuset_cpus") != EXPECTED_CPUSET
            or live_target.get("cpuset_mems") != EXPECTED_MEMS
        ):
            fail_closed_events.append(
                {
                    "reason": "target_resource_readback_drift",
                    "target_pid": sampling_target_pid,
                    "observed": (
                        identity_document(
                            live_target,
                            live_entries,
                            container_devices,
                        )
                        if live_target is not None
                        else None
                    ),
                }
            )
            raise DiagnosticGateError(
                "TPC-C target mysqld identity or CPU/NUMA readback drifted",
                deadline_summary(
                    samples=samples,
                    resets=reset_journal,
                    observed_churn=observed_churn_journal,
                    fail_closed_events=fail_closed_events,
                    sample_count=sample_count,
                    deadline_seconds=sampling_deadline_seconds,
                ),
            )
        current_documents = relevant_identity_documents(
            sample,
            controller_pid,
            sampling_target_pid,
            target_cpus,
            container_devices,
        )
        current_relevant = set(current_documents)
        unsafe_current = [
            {
                "identity": document,
                "decision": decision,
            }
            for document in current_documents.values()
            if (
                decision := isolation_decision(
                    document,
                    target_cpus=target_cpus,
                    target_device=target_device,
                )
            )["action"] == "fail_closed"
        ]
        if unsafe_current:
            fail_closed_events.extend(unsafe_current)
            raise DiagnosticGateError(
                "TPC-C gate found CPU/device authority interference",
                deadline_summary(
                    samples=samples,
                    resets=reset_journal,
                    observed_churn=observed_churn_journal,
                    fail_closed_events=fail_closed_events,
                    sample_count=sample_count,
                    deadline_seconds=sampling_deadline_seconds,
                ),
            )
        current_blocking = {
            key
            for key, document in current_documents.items()
            if isolation_decision(
                document,
                target_cpus=target_cpus,
                target_device=target_device,
            )["action"] != "observe_only"
        }
        identity_churn = current_blocking != stable_blocking
        all_identity_churn = current_relevant != stable_relevant
        identity_event = (
            churn_record(
                previous_sample=samples[-1],
                current_sample=sample,
                previous_identities=stable_identity_documents,
                current_identities=current_documents,
                target_cpus=target_cpus,
                target_device=target_device,
            )
            if all_identity_churn
            else None
        )
        if identity_event is not None:
            if identity_event["action"] == "fail_closed":
                fail_closed_events.append(identity_event)
                raise DiagnosticGateError(
                    "TPC-C gate found unsafe co-resident identity churn",
                    deadline_summary(
                        samples=samples,
                        resets=reset_journal,
                        observed_churn=observed_churn_journal,
                        fail_closed_events=fail_closed_events,
                        sample_count=sample_count,
                        deadline_seconds=sampling_deadline_seconds,
                    ),
                )
            if identity_event["action"] == "observe_only":
                identity_event["observed_churn_index"] = (
                    len(observed_churn_journal) + 1
                )
                observed_churn_journal.append(identity_event)
        if sample_gap or identity_churn:
            record = reset_record(
                reason=(
                    "sample_gap"
                    if sample_gap
                    else "relevant_identity_churn"
                ),
                previous_sample=samples[-1],
                current_sample=sample,
                stable_samples=samples,
                previous_identities=stable_identity_documents,
                current_identities=current_documents,
            )
            rolling_window_resets += 1
            record["reset_index"] = rolling_window_resets
            record["triggers"] = [
                name
                for name, active in (
                    ("sample_gap", sample_gap),
                    ("relevant_identity_churn", identity_churn),
                )
                if active
            ]
            reset_journal.append(record)
            samples = [sample]
            stable_relevant = current_relevant
            stable_identity_documents = current_documents
            stable_blocking = current_blocking
            next_sample_start = time.monotonic() + 0.7
            continue
        samples.append(sample)
        stable_relevant = current_relevant
        stable_identity_documents = current_documents

    ticks_per_second = float(os.sysconf("SC_CLK_TCK"))
    initial = {int(row["pid"]): row for row in samples[0]["entries"]}
    if controller_pid not in initial:
        raise RuntimeError("capture controller PID is absent from initial /proc sample")
    owned_initial = descendants(initial, controller_pid)
    target_rows = [
        row
        for row in initial.values()
        if row.get("category") == "mysql"
        and (
            container_id in str(row.get("cgroup") or "")
            or container_id[:12] in str(row.get("cgroup") or "")
        )
    ]
    if len(target_rows) != 1:
        raise RuntimeError("target TPC-C mysqld is absent or ambiguous")
    if (
        initial[controller_pid].get("cpuset_cpus") != EXPECTED_CPUSET
        or target_rows[0].get("cpuset_cpus") != EXPECTED_CPUSET
        or target_rows[0].get("cpuset_mems") != EXPECTED_MEMS
        or any(
            not owned_numa_isolated(row)
            for pid, row in initial.items()
            if pid in owned_initial
        )
    ):
        raise RuntimeError("TPC-C controller tree or target CPU/NUMA affinity differs")
    target_identity = identity(target_rows[0])
    target_pid = target_identity[0]
    initial_documents = relevant_identity_documents(
        samples[0],
        controller_pid,
        target_pid,
        target_cpus,
        container_devices,
    )
    initial_relevant = {
        key
        for key, document in initial_documents.items()
        if isolation_decision(
            document,
            target_cpus=target_cpus,
            target_device=target_device,
        )["action"] != "observe_only"
    }
    device_authority = {
        key: list(document.get("device_authority") or [])
        for key, document in initial_documents.items()
    }
    unknown_devices = sorted(
        value for value, devices in device_authority.items() if not devices
    )

    intervals: list[dict[str, Any]] = []
    maximum_gap = 0.0
    for before_sample, after_sample in zip(samples, samples[1:]):
        elapsed = float(after_sample["monotonic"]) - float(
            before_sample["monotonic"]
        )
        maximum_gap = max(maximum_gap, elapsed)
        before = {int(row["pid"]): row for row in before_sample["entries"]}
        after = {int(row["pid"]): row for row in after_sample["entries"]}
        owned = descendants(after, controller_pid) if controller_pid in after else set()
        target = after.get(target_pid)
        target_present = (
            target is not None
            and identity(target) == target_identity
            and target.get("cpuset_cpus") == EXPECTED_CPUSET
            and target.get("cpuset_mems") == EXPECTED_MEMS
        )
        owned_affinity_exact = bool(owned) and all(
            owned_numa_isolated(row)
            for pid, row in after.items()
            if pid in owned
        )
        after_documents = relevant_identity_documents(
            after_sample,
            controller_pid,
            target_pid,
            target_cpus,
            container_devices,
        )
        current_relevant = {
            key
            for key, document in after_documents.items()
            if isolation_decision(
                document,
                target_cpus=target_cpus,
                target_device=target_device,
            )["action"] != "observe_only"
        }
        interference = interval_interference(
            before=before,
            after=after,
            owned=owned,
            target_pid=target_pid,
            initial_relevant=initial_relevant,
            target_cpus=target_cpus,
            target_device=target_device,
            device_authority=device_authority,
            elapsed=elapsed,
            ticks_per_second=ticks_per_second,
        )
        intervals.append(
            {
                "elapsed_seconds": elapsed,
                "target_mysql_present": target_present,
                "controller_present": controller_pid in after,
                "owned_process_affinity_exact": owned_affinity_exact,
                "relevant_identity_set_unchanged": current_relevant
                == initial_relevant,
                **interference,
            }
        )

    initial_inventory = [
        {
            "pid": pid,
            "ppid": int(row["ppid"]),
            "start_ticks": int(row["start_ticks"]),
            "category": row.get("category"),
            "command": str(row.get("command") or ""),
            "cpuset_cpus": row.get("cpuset_cpus"),
            "cpuset_mems": row.get("cpuset_mems"),
            "anonymous_numa_nodes": row.get("anonymous_numa_nodes"),
            "anonymous_numa_policies": row.get(
                "anonymous_numa_policies"
            ),
            "owned_by_capture_controller": pid in owned_initial,
            "device_authority": (
                device_authority.get(identity(row))
                if identity(row) in device_authority
                else []
            ),
        }
        for pid, row in sorted(initial.items())
        if row.get("category") in RELEVANT or pid in {controller_pid, target_pid}
    ]
    observed = {
        "sample_count": len(samples),
        "interval_count": len(intervals),
        "rolling_window_resets": rolling_window_resets,
        "reset_journal": reset_journal,
        "observed_isolated_postgresql_churn_count":
            len(observed_churn_journal),
        "observed_isolated_postgresql_churn_journal":
            observed_churn_journal,
        "fail_closed_events": fail_closed_events,
        "maximum_gap_seconds": maximum_gap,
        "target_mysql_present_all_samples": all(
            row["target_mysql_present"] for row in intervals
        ),
        "controller_present_all_samples": all(
            row["controller_present"] for row in intervals
        ),
        "owned_process_affinity_exact_all_samples": all(
            row["owned_process_affinity_exact"] for row in intervals
        ),
        "relevant_identity_set_unchanged_all_samples": all(
            row["relevant_identity_set_unchanged"] for row in intervals
        ),
        "maximum_cpu_overlap_process_count": max(
            len(row["cpu_overlap_pids"]) for row in intervals
        ),
        "maximum_active_shared_device_process_count": max(
            len(row["active_shared_device_pids"]) for row in intervals
        ),
        "maximum_co_resident_shared_device_io_bps": max(
            row["co_resident_shared_device_io_bps"] for row in intervals
        ),
        "unknown_device_authority_identities": [
            {"pid": pid, "start_ticks": start}
            for pid, start in unknown_devices
        ],
    }
    passed = (
        len(intervals) >= 60
        and maximum_gap <= 1.5
        and observed["target_mysql_present_all_samples"] is True
        and observed["controller_present_all_samples"] is True
        and observed["owned_process_affinity_exact_all_samples"] is True
        and observed["relevant_identity_set_unchanged_all_samples"] is True
        and observed["maximum_cpu_overlap_process_count"] == 0
        and observed["maximum_active_shared_device_process_count"] == 0
        and not unknown_devices
    )
    return {
        "schema_version": SCHEMA,
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "hostname": "compute00",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "transport": "local_no_ssh",
        "capture_only": True,
        "r0": False,
        "candidate": False,
        "top5_replay": False,
        "accepted_round": False,
        "outer_execution_authorized": False,
        "capture_token": capture_token,
        "controller_pid": controller_pid,
        "target_mysql_pid": target_pid,
        "target_container_id": container_id,
        "target_port": EXPECTED_PORT,
        "target_cpuset": EXPECTED_CPUSET,
        "target_mems": EXPECTED_MEMS,
        "target_device": EXPECTED_DEVICE,
        "target_device_major_minor": target_device,
        "resource_readback_path": str(readback_path.resolve()),
        "resource_readback_sha256": sha256(readback_path),
        "policy": {
            "window_seconds": 60,
            "minimum_intervals": 60,
            "maximum_sample_gap_seconds": 1.5,
            "maximum_cpu_overlap_process_count": 0,
            "maximum_active_shared_device_process_count": 0,
            "maximum_co_resident_shared_device_io_bps": 0,
            "unknown_device_authority_count": 0,
            "blocking_relevant_process_identity_changes": 0,
            "isolated_postgresql_identity_churn":
                "inventory_only_no_window_reset",
        },
        "observed": observed,
        "diagnostic_reset_journal": reset_journal,
        "diagnostic_observed_churn_journal": observed_churn_journal,
        "diagnostic_fail_closed_events": fail_closed_events,
        "container_inventory": containers,
        "initial_process_inventory": initial_inventory,
        "intervals": intervals,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource-readback", type=Path, required=True)
    parser.add_argument("--controller-pid", type=int, required=True)
    parser.add_argument("--capture-token", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sample-count", type=int, default=60)
    args = parser.parse_args()
    if args.sample_count < 60:
        raise SystemExit("at least 60 one-second intervals are required")
    try:
        document = collect(
            readback_path=args.resource_readback,
            controller_pid=args.controller_pid,
            capture_token=args.capture_token,
            sample_count=args.sample_count,
        )
    except BaseException as error:
        document = {
            "schema_version": SCHEMA,
            "status": "FAIL_ERROR",
            "passed": False,
            "hostname": socket.gethostname(),
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "transport": "local_no_ssh",
            "capture_only": True,
            "r0": False,
            "candidate": False,
            "top5_replay": False,
            "accepted_round": False,
            "outer_execution_authorized": False,
            "capture_token": args.capture_token,
            "controller_pid": args.controller_pid,
            "error_type": type(error).__name__,
            "error": str(error),
        }
        diagnostics = getattr(error, "diagnostics", None)
        if isinstance(diagnostics, Mapping):
            document["deadline_diagnostics"] = dict(diagnostics)
            document["diagnostic_reset_journal"] = list(
                diagnostics.get("reset_journal") or []
            )
            document["diagnostic_observed_churn_journal"] = list(
                diagnostics.get("observed_churn_journal") or []
            )
            document["diagnostic_fail_closed_events"] = list(
                diagnostics.get("fail_closed_events") or []
            )
    atomic_json(args.output, document)
    print(json.dumps({"output": str(args.output), "passed": document["passed"]}))
    return 0 if document["passed"] is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
