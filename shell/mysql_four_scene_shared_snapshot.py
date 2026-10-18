#!/usr/bin/env python3
"""Write one same-token MySQL/PG/benchmark snapshot for XP capture readiness."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import stat as stat_module
import subprocess
from typing import Any, Mapping


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_sidecar(path: Path) -> Mapping[str, Any]:
    sidecar = path.with_name(path.name + ".sha256")
    if (
        path.is_symlink()
        or not path.is_file()
        or not sidecar.is_file()
        or sidecar.read_text(encoding="ascii")
        != f"{sha256(path)}  {path.name}\n"
    ):
        raise RuntimeError(f"invalid same-token input: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def run(*argv: str) -> str:
    return subprocess.run(
        list(argv),
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    ).stdout


def device_for_path(path: str) -> str:
    output = run("findmnt", "-n", "-o", "MAJ:MIN", "-T", path).strip()
    if not output or "\n" in output:
        raise RuntimeError(f"device authority is ambiguous: {path}")
    return output


def _device_from_stat(path: Path) -> str:
    metadata = path.stat()
    return f"{os.major(metadata.st_dev)}:{os.minor(metadata.st_dev)}"


def _open_regular_devices(directory: Path) -> list[str]:
    devices: set[str] = set()
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


def _has_data_plane_descendant(
    observer_pid: int,
    entries: Mapping[int, Mapping[str, Any]],
    parent_by_pid: Mapping[int, int],
) -> list[int]:
    descendants: list[int] = []
    for pid, entry in entries.items():
        if entry.get("category") not in {"mysql", "postgresql", "benchmark"}:
            continue
        current = int(parent_by_pid.get(pid, 0))
        visited: set[int] = set()
        while current > 0 and current not in visited:
            if current == observer_pid:
                descendants.append(pid)
                break
            visited.add(current)
            current = int(parent_by_pid.get(current, 0))
    return sorted(descendants)


def proc_entries(
    classifier: Any,
    *,
    target_cpus: set[int],
    target_device: str,
    proc_root: Path = Path("/proc"),
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    parent_by_pid: dict[int, int] = {}
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
            stat = (directory / "stat").read_text(encoding="ascii")
            rest = stat[stat.rfind(")") + 2 :].split()
            pid = int(directory.name)
            ppid = int(rest[1])
            parent_by_pid[pid] = ppid
            try:
                cwd = os.readlink(directory / "cwd")
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                cwd = ""
            category = classifier(command, argv=argv, cwd=cwd or None)
            if category is None:
                continue
            cwd_device = ""
            executable_device = ""
            try:
                cwd_device = _device_from_stat(directory / "cwd")
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                pass
            try:
                executable_device = _device_from_stat(directory / "exe")
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                pass
            entries.append(
                {
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
                    "cgroup": (directory / "cgroup").read_text(
                        encoding="utf-8", errors="replace"
                    ),
                }
            )
        except (
            FileNotFoundError,
            PermissionError,
            ProcessLookupError,
            ValueError,
        ):
            continue
    by_pid = {int(entry["pid"]): entry for entry in entries}
    observer_inventory: dict[str, dict[str, Any]] = {}
    for entry in entries:
        argv = list(entry.get("argv") or [])
        if (
            entry["category"] != "observer"
            or not argv
            or Path(argv[-1]).name != "benchmark_monitor.py"
        ):
            continue
        pid = int(entry["pid"])
        descendants = _has_data_plane_descendant(pid, by_pid, parent_by_pid)
        devices = {
            str(entry.get("cwd_device_major_minor") or ""),
            str(entry.get("executable_device_major_minor") or ""),
            *(
                str(value)
                for value in entry.get("open_regular_file_devices") or []
            ),
        }
        devices.discard("")
        reasons = []
        if descendants:
            reasons.append("owns_data_plane_descendant")
        if int(entry["processor"]) in target_cpus:
            reasons.append("executing_on_target_cpu")
        if target_device in devices:
            reasons.append("open_path_on_target_device")
        if (
            not entry.get("cwd")
            or not entry.get("cwd_device_major_minor")
            or not entry.get("executable_device_major_minor")
        ):
            reasons.append("observer_path_device_evidence_incomplete")
        proof = {
            "pid": pid,
            "identity": "frozen_benchmark_monitor_sha256",
            "command": entry["command"],
            "cwd": entry["cwd"],
            "processor": entry["processor"],
            "path_devices": sorted(devices),
            "data_plane_descendant_pids": descendants,
            "target_cpu_hit": "executing_on_target_cpu" in reasons,
            "target_device_hit": "open_path_on_target_device" in reasons,
            "accepted_as_observer": not reasons,
            "rejection_reasons": reasons,
        }
        if reasons:
            entry["category"] = "benchmark"
            entry["observer_rejection_reasons"] = reasons
        else:
            entry["observer_identity"] = proof["identity"]
            observer_inventory[str(pid)] = proof
    return entries, observer_inventory


def container_device(inspect: Mapping[str, Any]) -> str:
    mounts = list(inspect.get("Mounts") or [])
    preferred = [
        mount
        for mount in mounts
        if mount.get("Type") == "bind"
        and mount.get("Source")
        and (
            mount.get("Destination") in {
                "/var/lib/mysql",
                "/var/lib/postgresql/data",
            }
            or "mysql" in str(mount.get("Destination")).lower()
            or "postgres" in str(mount.get("Destination")).lower()
        )
    ]
    if preferred:
        return device_for_path(str(preferred[0]["Source"]))
    return device_for_path("/var/lib/docker")


def inferred_host_device(entry: Mapping[str, Any]) -> str | None:
    command = str(entry.get("command") or "")
    lowered = command.lower()
    if "/disk1/" in command or "59630" in command:
        return device_for_path("/disk1")
    if (
        "/disk2/" in command
        or "tpch_v" in lowered
        or "tpch_local_r0_v" in lowered
    ):
        return device_for_path("/disk2")
    if "/disk3/" in command:
        return device_for_path("/disk3")
    marker = " -D "
    if marker in command:
        data_directory = command.split(marker, 1)[1].split()[0]
        if Path(data_directory).exists():
            return device_for_path(data_directory)
    if "postgresql/12/main" in command:
        return device_for_path("/var/lib/postgresql/12/main")
    return None


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise RuntimeError(f"fresh snapshot output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.partial")
    with temporary.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha256(path)}  {path.name}\n", encoding="ascii"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-spec", type=Path, required=True)
    parser.add_argument("--capture-request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bundle-capture-dir", type=Path, required=True)
    args = parser.parse_args()

    if socket.gethostname() != "compute00":
        raise RuntimeError("shared snapshot is restricted to local compute00")
    spec = load_sidecar(args.capture_spec)
    request = load_sidecar(args.capture_request)
    token = str(spec.get("launch_token") or "")
    if (
        not token
        or request.get("launch_token") != token
        or request.get("workload_started") is not False
        or request.get("target_mysql_port") != 65204
    ):
        raise RuntimeError("capture request/spec identity differs")

    import sys

    capture_dir = args.bundle_capture_dir.resolve(strict=True)
    sys.path.insert(0, str(capture_dir))
    from mysql_process_classification import classify_process

    readback = load_sidecar(Path(str(request["resource_readback_path"])))
    resource_source = json.loads(
        Path(str(readback["source_resource_readback_path"])).read_text(
            encoding="utf-8"
        )
    )
    target_id = str(resource_source.get("container_id") or "")
    if len(target_id) < 12:
        raise RuntimeError("target XP container ID is absent")
    envelope = spec.get("frozen_resource_envelope")
    if not isinstance(envelope, Mapping):
        raise RuntimeError("capture spec resource envelope is absent")
    target_cpus: set[int] = set()
    for piece in str(envelope.get("cpuset_cpus") or "").split(","):
        if not piece:
            continue
        if "-" in piece:
            low, high = (int(value) for value in piece.split("-", 1))
            target_cpus.update(range(low, high + 1))
        else:
            target_cpus.add(int(piece))
    target_device = str(envelope.get("io_device") or "")
    if not target_cpus or not target_device:
        raise RuntimeError("capture spec CPU/device envelope is incomplete")
    entries, observer_inventory = proc_entries(
        classify_process,
        target_cpus=target_cpus,
        target_device=target_device,
    )
    by_pid = {int(entry["pid"]): entry for entry in entries}

    identifiers = [
        value
        for value in run(
            "docker", "ps", "-a", "--no-trunc", "--format", "{{.ID}}"
        ).splitlines()
        if value
    ]
    inspections = [
        json.loads(run("docker", "inspect", identifier))[0]
        for identifier in identifiers
    ]
    container_devices: dict[str, str] = {}
    groups: dict[str, list[dict[str, Any]]] = {
        "old_mysql_xp_containers": [],
        "postgresql_tpcc_containers": [],
        "postgresql_xp_containers": [],
    }
    target_pid: int | None = None
    for inspect in inspections:
        identifier = str(inspect["Id"])
        name = str(inspect.get("Name") or "").lstrip("/")
        state = inspect.get("State") or {}
        running = bool(state.get("Running"))
        pid = int(state.get("Pid") or 0)
        command = (
            str(by_pid.get(pid, {}).get("command") or "")
            if running and pid
            else " ".join(
                str(value)
                for value in (
                    inspect.get("Config", {}).get("Entrypoint") or []
                )
                + (inspect.get("Config", {}).get("Cmd") or [])
            )
        )
        category = classify_process(command)
        if identifier == target_id:
            if not running or pid not in by_pid or category != "mysql":
                raise RuntimeError("target XP container does not reconcile to mysqld")
            target_pid = pid
            continue
        identity_text = " ".join(
            (
                name,
                str(inspect.get("Config", {}).get("Image") or ""),
                command,
            )
        ).lower()
        if category not in {"mysql", "postgresql"}:
            if "mysql" in identity_text:
                category = "mysql"
            elif "postgres" in identity_text:
                category = "postgresql"
            else:
                continue
        if running and (pid not in by_pid or by_pid[pid]["category"] != category):
            raise RuntimeError(f"running DB container is not reconciled: {name}")
        status = "running" if running else (
            "removed" if state.get("Dead") else "exited"
        )
        row: dict[str, Any] = {
            "name": name,
            "status": status,
            "category": category,
        }
        if running:
            row["pid"] = pid
            container_devices[identifier] = container_device(inspect)
        if category == "mysql":
            groups["old_mysql_xp_containers"].append(row)
        elif "tpcc" in name.lower():
            groups["postgresql_tpcc_containers"].append(row)
        else:
            groups["postgresql_xp_containers"].append(row)

    if target_pid is None:
        raise RuntimeError("fresh target XP PID is absent")
    process_devices: dict[str, str] = {}
    host_process_tree_authorities: dict[str, dict[str, Any]] = {}
    missing: list[dict[str, Any]] = []

    def resolve_process_device(
        entry: Mapping[str, Any],
    ) -> tuple[str | None, int | None]:
        current = entry
        visited: set[int] = set()
        while True:
            current_pid = int(current["pid"])
            if current_pid in visited:
                return None, None
            visited.add(current_pid)
            for identifier, candidate in container_devices.items():
                cgroup = str(current["cgroup"])
                if identifier in cgroup or identifier[:12] in cgroup:
                    return candidate, current_pid
            candidate = inferred_host_device(current)
            if candidate is not None:
                return candidate, current_pid
            parent_pid = int(current["ppid"])
            parent = by_pid.get(parent_pid)
            if parent is None:
                return None, None
            current = parent

    for entry in entries:
        pid = int(entry["pid"])
        if pid == target_pid or entry["category"] not in {
            "mysql", "postgresql", "benchmark"
        }:
            continue
        device, authority_pid = resolve_process_device(entry)
        if device is None:
            missing.append(entry)
        else:
            process_devices[str(pid)] = device
            if authority_pid is not None and authority_pid != pid:
                host_process_tree_authorities[str(pid)] = {
                    "authority_pid": authority_pid,
                    "authority_command": str(
                        by_pid[authority_pid]["command"]
                    ),
                    "device": device,
                }
    if missing:
        raise RuntimeError(
            "missing process device authority: "
            + json.dumps(
                [
                    {
                        "pid": row["pid"],
                        "category": row["category"],
                        "command": row["command"][:300],
                    }
                    for row in missing
                ],
                sort_keys=True,
            )
        )

    document = {
        "schema_version": "mysql_four_scene_shared_snapshot_v1",
        "hostname": "compute00",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "transport": "local_no_ssh",
        "capture_only": True,
        "outer_execution_authorized": False,
        "launch_token": token,
        "inventory_complete": True,
        "single_collection_per_control_cycle": True,
        "container_groups": groups,
        "process_inventory": entries,
        "observer_inventory": observer_inventory,
        "process_devices": process_devices,
        "host_process_tree_authorities": host_process_tree_authorities,
        "target_xp": {
            "container_id": target_id,
            "pid": target_pid,
            "excluded_from_process_devices": True,
        },
    }
    atomic_json(args.output, document)
    print(json.dumps({"snapshot": str(args.output), "target_pid": target_pid}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
