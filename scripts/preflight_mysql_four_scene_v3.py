#!/usr/bin/env python3
"""Fail-closed host preflight for the 2026-08-09 MySQL four-scene freeze."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FREEZE_DIR = ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260809_v3"
FREEZE_PATH = FREEZE_DIR / "four_scene_freeze.json"
INHERITED_PATH = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260809_v1/worker_resource_freeze.json"
)

RELEVANT_PROCESS_PATTERNS = (
    re.compile(r"binary_plan_resource_tuner\.py"),
    re.compile(r"formal_mysql_sgd_campaign\.py"),
    re.compile(r"mysql_parameter_optimizer\.py"),
    re.compile(r"generic_workload_shim\.py"),
    re.compile(r"com\.hybench\.HyBench"),
    re.compile(r"benchbase\.jar"),
    re.compile(r"pg_algorithm_c_lane_entry\.py"),
    re.compile(r"run_pg_algorithm_c_calibrate_then_scene\.py"),
    re.compile(r"\bpsql\b.*postgresql://"),
    re.compile(r"postgres: .*\b(?:SELECT|COPY|INSERT|UPDATE|DELETE)\b"),
)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _cpus(value: str) -> set[int]:
    result: set[int] = set()
    for field in value.split(","):
        start, separator, end = field.strip().partition("-")
        if not start:
            continue
        result.update(range(int(start), int(end) + 1) if separator else [int(start)])
    return result


def _compress(values: set[int]) -> str:
    ordered = sorted(values)
    if not ordered:
        return ""
    result: list[str] = []
    start = previous = ordered[0]
    for value in ordered[1:]:
        if value == previous + 1:
            previous = value
            continue
        result.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = value
    result.append(str(start) if start == previous else f"{start}-{previous}")
    return ",".join(result)


def _ancestry() -> set[int]:
    owned = {os.getpid()}
    current = os.getpid()
    while current > 1:
        try:
            status = Path(f"/proc/{current}/status").read_text(encoding="utf-8")
        except OSError:
            break
        match = re.search(r"^PPid:\s+(\d+)$", status, re.MULTILINE)
        if match is None:
            break
        current = int(match.group(1))
        owned.add(current)
    return owned


def _process_inventory(
    target_cpus: set[int], target_mems: set[int] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    owned = _ancestry()
    inventory: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) in owned:
            continue
        try:
            command = (
                (entry / "cmdline")
                .read_bytes()
                .replace(b"\0", b" ")
                .decode("utf-8", errors="replace")
                .strip()
            )
            if not command or not any(pattern.search(command) for pattern in RELEVANT_PROCESS_PATTERNS):
                continue
            status = (entry / "status").read_text(encoding="utf-8", errors="replace")
            allowed_match = re.search(r"^Cpus_allowed_list:\s*(\S+)$", status, re.MULTILINE)
            mems_match = re.search(r"^Mems_allowed_list:\s*(\S+)$", status, re.MULTILINE)
            ppid_match = re.search(r"^PPid:\s*(\d+)$", status, re.MULTILINE)
            allowed_text = allowed_match.group(1) if allowed_match else ""
            overlap = target_cpus & _cpus(allowed_text)
            mems_text = mems_match.group(1) if mems_match else ""
            memory_overlap = (
                set() if target_mems is None else target_mems & _cpus(mems_text)
            )
            row = {
                "pid": int(entry.name),
                "ppid": int(ppid_match.group(1)) if ppid_match else None,
                "command": command,
                "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
                "cpus_allowed_list": allowed_text,
                "mems_allowed_list": mems_text,
                "overlap_cpus": _compress(overlap),
                "overlap_mems": _compress(memory_overlap),
            }
            inventory.append(row)
            if overlap:
                blockers.append({**row, "reason": "active_benchmark_or_controller_cpu_overlap"})
            elif memory_overlap:
                blockers.append({
                    **row,
                    "reason": "active_benchmark_or_database_numa_memory_overlap",
                })
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError, ValueError):
            continue
    return inventory, blockers


def _docker_inventory(target_cpus: set[int], intended_names: set[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    completed = subprocess.run(
        ["docker", "ps", "--no-trunc", "--format", "{{.Names}}"],
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    inventory: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    for name in completed.stdout.splitlines():
        inspected = subprocess.run(
            ["docker", "inspect", name],
            text=True,
            capture_output=True,
            check=True,
            timeout=30,
        )
        document = json.loads(inspected.stdout)[0]
        host = document.get("HostConfig") or {}
        mounts = document.get("Mounts") or []
        cpuset_text = str(host.get("CpusetCpus") or "0-511")
        overlap = target_cpus & _cpus(cpuset_text)
        disk2_sources = sorted(
            str(row.get("Source")) for row in mounts
            if str(row.get("Source") or "").startswith("/disk2/")
        )
        command = " ".join(
            [str(document.get("Path") or ""), *map(str, document.get("Args") or [])]
        ).strip()
        database_like = bool(
            re.search(r"(?:mysqld|postgres|gaussdb|mongod|redis-server)", command, re.I)
        )
        row = {
            "name": name,
            "container_id": document.get("Id"),
            "pid": (document.get("State") or {}).get("Pid"),
            "command": command,
            "cpuset_cpus": cpuset_text,
            "cpuset_mems": str(host.get("CpusetMems") or ""),
            "overlap_cpus": _compress(overlap),
            "disk2_bind_sources": disk2_sources,
            "database_like": database_like,
            "intended_successor_container": name in intended_names,
        }
        inventory.append(row)
        if name in intended_names:
            continue
        if database_like and disk2_sources:
            blockers.append({**row, "reason": "co_resident_database_uses_target_disk2"})
    return inventory, blockers


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as stream:
        stream.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            stream.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _static_readback(freeze: dict[str, Any], inherited: dict[str, Any]) -> dict[str, Any]:
    storage = freeze["storage"]
    findmnt = subprocess.run(
        ["findmnt", "-T", storage["mount"], "-n", "-o", "SOURCE,FSTYPE"],
        text=True,
        capture_output=True,
        check=True,
        timeout=10,
    ).stdout.split()
    if isinstance(freeze.get("tpch_sf4_storage_manifest"), dict):
        sf4_contract = freeze["tpch_sf4_storage_manifest"]
    else:
        sf4_contract = freeze["workload_contracts"]["tpch_sf4_memory"]
        sf4_contract = {
            "path": sf4_contract["storage_manifest"],
            "sha256": sf4_contract["storage_manifest_sha256"],
        }
    sf4 = Path(sf4_contract["path"])
    expected_sf4_sha = sf4_contract["sha256"]
    workers = (
        list(freeze["workers"])
        if isinstance(freeze.get("workers"), list)
        else list(inherited["workers"]) + list(freeze["xp_io_workers"])
    )
    ports = sorted(int(row["host_port"]) for row in workers)
    return {
        "disk2_source": findmnt[0] if findmnt else None,
        "disk2_filesystem": findmnt[1] if len(findmnt) > 1 else None,
        "disk2_matches_freeze": findmnt == [storage["device"], storage["filesystem"]],
        "swap_entries": len(Path("/proc/swaps").read_text(encoding="utf-8").splitlines()[1:]),
        "sf4_storage_manifest_path": str(sf4),
        "sf4_storage_manifest_sha256": _sha(sf4),
        "sf4_storage_manifest_matches_freeze": _sha(sf4) == expected_sf4_sha,
        "ports": {str(port): {"free": _port_is_free(port)} for port in ports},
    }


def run(
    output: Path,
    window_seconds: int,
    interval_seconds: float,
    freeze_path: Path = FREEZE_PATH,
) -> dict[str, Any]:
    freeze_path = freeze_path.resolve(strict=True)
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    if isinstance(freeze.get("workers"), list):
        workers = list(freeze["workers"])
        inherited: dict[str, Any] = {"workers": []}
    else:
        inherited = json.loads(INHERITED_PATH.read_text(encoding="utf-8"))
        workers = list(inherited["workers"]) + list(freeze["xp_io_workers"])
    target_cpus: set[int] = set()
    intended_names: set[str] = set()
    for row in workers:
        target_cpus.update(_cpus(str(row["database_cpuset"])))
        target_cpus.update(_cpus(str(row["client_cpuset"])))
        intended_names.add(str(row["container"]))

    static = _static_readback(freeze, inherited)
    samples: list[dict[str, Any]] = []
    started = time.monotonic()
    deadline = started + window_seconds
    while True:
        processes, process_blockers = _process_inventory(target_cpus)
        containers, container_blockers = _docker_inventory(target_cpus, intended_names)
        blockers = process_blockers + container_blockers
        samples.append({
            "captured_at": _now(),
            "process_inventory": processes,
            "container_inventory": containers,
            "blockers": blockers,
        })
        if blockers or time.monotonic() >= deadline:
            break
        time.sleep(min(interval_seconds, max(0.0, deadline - time.monotonic())))

    static_passed = (
        static["disk2_matches_freeze"]
        and static["swap_entries"] == 0
        and static["sf4_storage_manifest_matches_freeze"]
        and all(row["free"] for row in static["ports"].values())
    )
    blockers = samples[-1]["blockers"]
    passed = static_passed and not blockers and time.monotonic() - started >= window_seconds
    result = {
        "schema_version": "mysql_four_scene_runtime_preflight_v3",
        "status": "PASS" if passed else "BLOCKED_FAIL_CLOSED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_path": str(freeze_path),
        "freeze_sha256": _sha(freeze_path),
        "preflight_source_path": str(Path(__file__).resolve()),
        "preflight_source_sha256": _sha(Path(__file__).resolve()),
        "target_cpuset_union": _compress(target_cpus),
        "required_clean_window_seconds": window_seconds,
        "observed_seconds": time.monotonic() - started,
        "static_readback": static,
        "samples": samples,
        "blockers": blockers,
        "formal_launch_authorized": passed,
        "launch_performed": False,
        "accepted_rounds": {scene: 0 for scene in freeze["active_scenes"]},
        "completed_at": _now(),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.with_name(output.name + ".sha256").write_text(
        f"{_sha(output)}  {output.name}\n", encoding="ascii"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--freeze", type=Path, default=FREEZE_PATH)
    parser.add_argument("--window-seconds", type=int, default=60)
    parser.add_argument("--interval-seconds", type=float, default=1.0)
    args = parser.parse_args()
    if args.window_seconds < 0 or args.interval_seconds <= 0:
        parser.error("window and interval must be non-negative/positive")
    result = run(
        args.output.resolve(),
        args.window_seconds,
        args.interval_seconds,
        args.freeze,
    )
    print(json.dumps({
        "status": result["status"],
        "formal_launch_authorized": result["formal_launch_authorized"],
        "blocker_count": len(result["blockers"]),
        "output": str(args.output.resolve()),
    }, ensure_ascii=False))
    return 0 if result["formal_launch_authorized"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
