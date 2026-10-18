#!/usr/bin/env python3
"""Rebind a recreated XP worker after exact resource-envelope readback."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Mapping


WORKER_ID = "xp_cpu_w2"
CONTAINER = "ra_mysql_disk2_algc_xp_cpu_w2_20260809"
EXPECTED = {
    "scene": "hybench_xp_cpu",
    "host_port": 65332,
    "nano_cpus": 1_650_000_000,
    "cpu_quota_us": 165_000,
    "memory_bytes": 8_589_934_592,
    "memory_swap": -1,
    "memory_swappiness": 0,
    "pids_max": 1024,
    "cpuset_cpus": "178-179,434-435",
    "cpuset_mems": "1",
    "device": "259:6",
    "read_bps": 1_205_862_400,
    "write_bps": 1_205_862_400,
}


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def one_line(path: Path) -> str:
    return path.read_text(encoding="ascii").strip()


def throttle(path: Path, device: str) -> int:
    rows = {
        fields[0]: int(fields[1])
        for line in path.read_text(encoding="ascii").splitlines()
        if len(fields := line.split()) == 2
    }
    if device not in rows:
        raise RuntimeError(f"device throttle missing: {device}: {path}")
    return rows[device]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authority-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    source = args.authority_root.resolve()
    output = args.output_root.resolve()
    if output.exists() or output.is_symlink():
        raise RuntimeError("fresh identity-rebind authority output required")
    prepared_path = source / "prepared_binding.json"
    accounts_path = source / "benchmark_accounts_binding.json"
    preflight_path = source / "transition_preflight.json"
    prepared = load(prepared_path)
    accounts = load(accounts_path)
    preflight = load(preflight_path)
    prepared_rows = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in prepared.get("workers") or []
    }
    account_rows = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in accounts.get("workers") or []
    }
    if (
        prepared.get("status") != "PREPARED"
        or accounts.get("status") != "PASS"
        or preflight.get("status") != "PASS"
        or set(prepared_rows) != set(account_rows)
        or len(prepared_rows) != 8
        or accounts.get("prepared_binding_sha256") != sha(prepared_path)
    ):
        raise RuntimeError("source authority chain differs")
    old_id = str(prepared_rows[WORKER_ID]["container_id"])
    inspected = json.loads(subprocess.run(
        ["docker", "inspect", CONTAINER], check=True, capture_output=True,
        text=True, timeout=30,
    ).stdout)[0]
    live_id = str(inspected.get("Id") or "")
    host = inspected.get("HostConfig") or {}
    state = inspected.get("State") or {}
    labels = (inspected.get("Config") or {}).get("Labels") or {}
    ports = ((host.get("PortBindings") or {}).get("3306/tcp") or [])
    binds = set(host.get("Binds") or [])
    required_binds = {
        "/disk2/resource_adaptive_mysql_algorithm_c_disk2_v4/xp_cpu_w2/datadir:/var/lib/mysql:rw",
        "/disk2/resource_adaptive_mysql_algorithm_c_disk2_v4/xp_cpu_w2/credentials:/run/ra-credentials:ro",
        "/disk2/resource_adaptive_mysql_algorithm_c_disk2_v4/xp_cpu_w2/credentials/reset-init.sql:/run/ra-init/reset.sql:ro",
    }
    if (
        not live_id or live_id == old_id
        or state.get("Running") is not True
        or state.get("Restarting") is True
        or labels.get("ra.owner") != "mysql_four_scene_v5"
        or labels.get("ra.scenario") != EXPECTED["scene"]
        or labels.get("ra.worker_id") != WORKER_ID
        or int(host.get("NanoCpus") or 0) != EXPECTED["nano_cpus"]
        or int(host.get("Memory") or 0) != EXPECTED["memory_bytes"]
        or int(host.get("MemorySwap") or 0) != EXPECTED["memory_swap"]
        or int(host.get("PidsLimit") or 0) != EXPECTED["pids_max"]
        or host.get("CpusetCpus") != EXPECTED["cpuset_cpus"]
        or host.get("CpusetMems") != EXPECTED["cpuset_mems"]
        or {str(row.get("HostPort")) for row in ports} != {str(EXPECTED["host_port"])}
        or binds != required_binds
    ):
        raise RuntimeError("recreated XP worker Docker envelope differs")
    relative = Path("docker-limited") / live_id
    roots = {
        "cpu": Path("/sys/fs/cgroup/cpu,cpuacct") / relative,
        "cpuset": Path("/sys/fs/cgroup/cpuset") / relative,
        "memory": Path("/sys/fs/cgroup/memory") / relative,
        "pids": Path("/sys/fs/cgroup/pids") / relative,
        "blkio": Path("/sys/fs/cgroup/blkio") / relative,
    }
    readback = {
        "cpu_quota_us": int(one_line(roots["cpu"] / "cpu.cfs_quota_us")),
        "cpuset_cpus": one_line(roots["cpuset"] / "cpuset.cpus"),
        "cpuset_mems": one_line(roots["cpuset"] / "cpuset.mems"),
        "memory_bytes": int(one_line(roots["memory"] / "memory.limit_in_bytes")),
        "memory_swappiness": int(one_line(roots["memory"] / "memory.swappiness")),
        "pids_max": int(one_line(roots["pids"] / "pids.max")),
        "read_bps": throttle(
            roots["blkio"] / "blkio.throttle.read_bps_device", EXPECTED["device"]
        ),
        "write_bps": throttle(
            roots["blkio"] / "blkio.throttle.write_bps_device", EXPECTED["device"]
        ),
    }
    for field, value in readback.items():
        if value != EXPECTED[field]:
            raise RuntimeError(f"recreated XP worker {field} differs: {value}")
    credentials = Path(str(prepared_rows[WORKER_ID]["credential_defaults"])).resolve()
    mysql_version = subprocess.run([
        "mysql", f"--defaults-file={credentials}", "--host=127.0.0.1",
        f"--port={EXPECTED['host_port']}", "--connect-timeout=5", "-NBe",
        "SELECT VERSION()",
    ], check=True, capture_output=True, text=True, timeout=15).stdout.strip()
    if not mysql_version.startswith("8.0.45"):
        raise RuntimeError("recreated XP worker MySQL version differs")
    output.mkdir(parents=True, mode=0o700)
    output_preflight = output / "transition_preflight.json"
    output_preflight.write_bytes(preflight_path.read_bytes())
    output_preflight.with_name(output_preflight.name + ".sha256").write_text(
        f"{sha(output_preflight)}  {output_preflight.name}\n", encoding="ascii"
    )
    audit = {
        "schema_version": "mysql_algorithm_c_xp_container_identity_rebind_v1",
        "status": "PASS",
        "created_at": datetime.now().astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "worker_id": WORKER_ID,
        "container": CONTAINER,
        "old_container_id": old_id,
        "new_container_id": live_id,
        "same_container_name": True,
        "same_image_id": str(inspected.get("Image") or ""),
        "same_labels_mounts_port_and_resource_envelope": True,
        "resource_readback": readback,
        "mysql_version": mysql_version,
        "workload_started": False,
        "accepted_rounds_mutated": False,
        "source_prepared": {"path": str(prepared_path), "sha256": sha(prepared_path)},
        "source_accounts": {"path": str(accounts_path), "sha256": sha(accounts_path)},
        "builder_source_path": str(Path(__file__).resolve()),
        "builder_source_sha256": sha(Path(__file__).resolve()),
    }
    audit_path = output / "container_identity_rebind_audit.json"
    atomic(audit_path, audit)
    row = prepared_rows[WORKER_ID]
    row["container_id"] = live_id
    row["container_pid"] = int(state.get("Pid") or 0)
    row["technical_identity_refresh_after_xp_reset"] = True
    row["identity_rebind_audit_path"] = str(audit_path)
    row["identity_rebind_audit_sha256"] = sha(audit_path)
    row["effective_resources"]["NanoCpus"] = EXPECTED["nano_cpus"]
    row["effective_resources"]["MemorySwappiness"] = EXPECTED["memory_swappiness"]
    for field in ("cgroup_path", "read_throttle_file", "write_throttle_file"):
        row["blkio_authority"][field] = str(row["blkio_authority"][field]).replace(
            old_id, live_id
        )
    prepared_successor = copy.deepcopy(prepared)
    prepared_successor["workers"] = [prepared_rows[str(row["worker_id"])] for row in prepared["workers"]]
    prepared_successor["preflight_path"] = str(output_preflight)
    prepared_successor["preflight_sha256"] = sha(output_preflight)
    prepared_successor["predecessor_prepared_path"] = str(prepared_path)
    prepared_successor["predecessor_prepared_sha256"] = sha(prepared_path)
    prepared_successor["container_identity_rebind_audit_path"] = str(audit_path)
    prepared_successor["container_identity_rebind_audit_sha256"] = sha(audit_path)
    output_prepared = output / "prepared_binding.json"
    atomic(output_prepared, prepared_successor)
    account_rows[WORKER_ID]["container_id"] = live_id
    accounts_successor = copy.deepcopy(accounts)
    accounts_successor["workers"] = [account_rows[str(row["worker_id"])] for row in accounts["workers"]]
    accounts_successor["prepared_binding_path"] = str(output_prepared)
    accounts_successor["prepared_binding_sha256"] = sha(output_prepared)
    accounts_successor["predecessor_accounts_path"] = str(accounts_path)
    accounts_successor["predecessor_accounts_sha256"] = sha(accounts_path)
    accounts_successor["container_identity_rebind_audit_path"] = str(audit_path)
    accounts_successor["container_identity_rebind_audit_sha256"] = sha(audit_path)
    output_accounts = output / "benchmark_accounts_binding.json"
    atomic(output_accounts, accounts_successor)
    print(json.dumps({
        "status": "PASS", "output_root": str(output),
        "old_container_id": old_id, "new_container_id": live_id,
        "prepared_sha256": sha(output_prepared),
        "accounts_sha256": sha(output_accounts),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
