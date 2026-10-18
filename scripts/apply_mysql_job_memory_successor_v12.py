#!/usr/bin/env python3
"""Apply and read back the frozen v12 JOB memory-envelope successor."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
BASE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260809_v11"
    / "four_scene_effective_resource_successor.json"
)
SUCCESSOR = (
    ROOT / "config/formal/frozen_mysql_four_scene_job_memory_20260810_v13"
    / "resource_successor.json"
)
NON_XP_PREPARED = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_adopt_v7_20260809T194100p0800/prepared_binding.json"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def inspect(container: str) -> dict:
    value = json.loads(subprocess.run(
        ["docker", "inspect", container], capture_output=True, text=True,
        check=True, timeout=30,
    ).stdout)
    if not isinstance(value, list) or len(value) != 1:
        raise RuntimeError(f"Docker inspect cardinality differs: {container}")
    return value[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,128}", args.run_id) is None:
        parser.error("unsafe run-id")
    base = load(BASE)
    successor = load(SUCCESSOR)
    prepared = load(NON_XP_PREPARED)
    calibration_path = ROOT / successor["calibration_evidence"]["path"]
    calibration = load(calibration_path)
    if (
        successor.get("status") != "FROZEN"
        or successor.get("formal_scope") != "mysql_only"
        or successor.get("predecessor_resource_freeze", {}).get("sha256") != sha(BASE)
        or successor.get("calibration_evidence", {}).get("sha256") != sha(calibration_path)
        or calibration.get("status") != "R0_FAILED_CLOSED"
        or any((calibration.get("accepted_rounds") or {}).values())
        or prepared.get("status") != "PREPARED"
    ):
        raise RuntimeError("v12 JOB memory successor authority differs")
    output = ROOT / "history/mysql_four_scene_20260809" / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh output exists: {output}")
    output.mkdir(parents=True, mode=0o700)
    base_workers = {row["worker_id"]: row for row in base["workers"]}
    prepared_workers = {row["worker_id"]: row for row in prepared["workers"]}
    override = successor["overrides"]["job_memory"]
    memory = int(override["memory_bytes"]["to"])
    buffer_pool = int(override["innodb_buffer_pool_bytes"]["to"])
    swap_readback = override["swap_readback"]
    host_swap_devices = [
        line for line in Path("/proc/swaps").read_text(encoding="ascii").splitlines()[1:]
        if line.strip()
    ]
    if host_swap_devices:
        raise RuntimeError("v13 JOB memory successor requires a swap-free host")
    rows = []
    failures = []
    for worker_id in override["worker_ids"]:
        worker = base_workers[worker_id]
        adopted = prepared_workers[worker_id]
        container = str(worker["container"])
        try:
            before = inspect(container)
            if (
                before.get("Id") != adopted.get("container_id")
                or (before.get("State") or {}).get("Running") is not True
            ):
                raise RuntimeError("live container identity differs")
            active = subprocess.run(
                ["docker", "exec", container, "mysql",
                 "--defaults-file=/run/ra-credentials/selected.cnf", "-NBe",
                 "SELECT COUNT(*) FROM information_schema.processlist "
                 "WHERE ID<>CONNECTION_ID() AND COMMAND<>'Daemon' "
                 "AND USER<>'event_scheduler'"],
                capture_output=True, text=True, check=True, timeout=30,
            ).stdout.strip()
            if active != "0":
                raise RuntimeError("non-system sessions remain")
            before_host = before.get("HostConfig") or {}
            if int(before_host.get("Memory", 0)) != memory:
                subprocess.run(
                    ["docker", "update", "--memory", str(memory), container],
                    capture_output=True, text=True, check=True, timeout=60,
                )
            current_buffer = subprocess.run(
                ["docker", "exec", container, "mysql",
                 "--defaults-file=/run/ra-credentials/selected.cnf", "-NBe",
                 "SELECT @@global.innodb_buffer_pool_size"],
                capture_output=True, text=True, check=True, timeout=60,
            ).stdout.strip()
            if int(current_buffer) != buffer_pool:
                subprocess.run(
                    ["docker", "exec", container, "mysql",
                     "--defaults-file=/run/ra-credentials/selected.cnf", "-NBe",
                     f"SET GLOBAL innodb_buffer_pool_size={buffer_pool}"],
                    capture_output=True, text=True, check=True, timeout=300,
                )
            readback = subprocess.run(
                ["docker", "exec", container, "mysql",
                 "--defaults-file=/run/ra-credentials/selected.cnf", "-NBe",
                 "SELECT @@global.innodb_buffer_pool_size,"
                 "@@global.innodb_buffer_pool_instances"],
                capture_output=True, text=True, check=True, timeout=60,
            ).stdout.strip().split("\t")
            after = inspect(container)
            host = after.get("HostConfig") or {}
            if (
                len(readback) != 2
                or int(readback[0]) != buffer_pool
                or int(host.get("Memory", 0)) != memory
                or int(host.get("MemorySwap", 0))
                != int(swap_readback["docker_host_config_memory_swap"])
                or after.get("Id") != before.get("Id")
                or after.get("RestartCount") != before.get("RestartCount")
                or (after.get("State") or {}).get("OOMKilled") is True
            ):
                raise RuntimeError("v12 JOB memory readback differs")
            rows.append({
                "worker_id": worker_id,
                "container": container,
                "container_id": after["Id"],
                "memory_bytes": memory,
                "docker_host_config_memory_swap": int(host["MemorySwap"]),
                "kernel_memsw_controller_available": False,
                "host_swap_devices": [],
                "effective_swap_bytes": 0,
                "innodb_buffer_pool_bytes": int(readback[0]),
                "innodb_buffer_pool_instances": int(readback[1]),
                "restart_count": after.get("RestartCount"),
                "oom_killed": (after.get("State") or {}).get("OOMKilled"),
                "ready": True,
            })
        except BaseException as error:
            failures.append({
                "worker_id": worker_id,
                "error": f"{type(error).__name__}: {error}",
            })
    document = {
        "schema_version": "mysql_four_scene_job_memory_runtime_readback_v12",
        "status": "PASS" if len(rows) == 2 and not failures else "FAILED_CLOSED",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "base_freeze_path": str(BASE),
        "base_freeze_sha256": sha(BASE),
        "resource_successor_path": str(SUCCESSOR),
        "resource_successor_sha256": sha(SUCCESSOR),
        "predecessor_prepared_path": str(NON_XP_PREPARED),
        "predecessor_prepared_sha256": sha(NON_XP_PREPARED),
        "workers": sorted(rows, key=lambda row: row["worker_id"]),
        "failures": failures,
        "workload_started": False,
        "accepted_rounds": {scene: 0 for scene in base["active_scenes"]},
    }
    binding = output / "job_memory_runtime_binding.json"
    binding.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    binding.with_name(binding.name + ".sha256").write_text(
        f"{sha(binding)}  {binding.name}\n", encoding="ascii"
    )
    print(json.dumps({"status": document["status"], "binding": str(binding)}))
    return 0 if document["status"] == "PASS" else 4


if __name__ == "__main__":
    raise SystemExit(main())
