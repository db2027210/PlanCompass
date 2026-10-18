#!/usr/bin/env python3
"""Capture and validate the live cgroup/MySQL envelope of one R0 campaign."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    descriptor, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    digest = hashlib.sha256(payload).hexdigest()
    path.with_name(path.name + ".sha256").write_text(
        f"{digest}  {path.name}\n", encoding="ascii"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument(
        "--from-preparation-readback",
        action="store_true",
        help="package the same run's already-verified prepare-time live readback",
    )
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    state = json.loads((run_dir / "campaign_runtime.json").read_text(encoding="utf-8"))
    envelope = state["resource_envelope"]
    host = state["host"]
    container = state["container"]
    if args.from_preparation_readback:
        source_path = run_dir / "resource_readback.json"
        source = json.loads(source_path.read_text(encoding="utf-8"))
        if source.get("verified") is not True:
            raise RuntimeError("prepare-time resource readback is not verified")
        cgroup = source.get("effective_cgroup")
        mysql = source.get("effective_mysql")
        if not isinstance(cgroup, dict) or not isinstance(mysql, dict):
            raise RuntimeError("prepare-time effective readback is incomplete")
        effective = {
            "container_id": source["container_id"],
            "cpu_quota_us": int(cgroup["cpu_quota_us"]),
            "cpu_period_us": int(cgroup["cpu_period_us"]),
            "cpuset_cpus": cgroup["cpuset_cpus"],
            "cpuset_mems": cgroup["cpuset_mems"],
            "memory_bytes": int(cgroup["memory_bytes"]),
            "memory_swap_bytes": int(cgroup["memory_swap_bytes"]),
            "pids_max": int(cgroup["pids_max"]),
            "buffer_pool_bytes": int(mysql["innodb_buffer_pool_bytes"]),
            "io_device_major_minor": cgroup["device_major_minor"],
            "io_read_bps": int(cgroup["io_read_bps"]),
            "io_write_bps": int(cgroup["io_write_bps"]),
        }
        document = {
            "schema_version": "mysql_sgd_effective_resource_readback_v2",
            "scenario": state["scenario"],
            "benchmark": state["benchmark"],
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "run_id": state["run_id"],
            "host": host,
            "container": container,
            "captured_at_utc": datetime.fromtimestamp(
                source_path.stat().st_mtime, timezone.utc
            ).isoformat(),
            "requested": envelope,
            "effective": effective,
            "verified": True,
            "packaging_source": {
                "kind": "same_run_prepare_time_live_readback",
                "path": str(source_path),
                "verified": True,
            },
        }
        target = run_dir / "effective_resource_readback_v2.json"
        atomic_json(target, document)
        print(target)
        return 0
    script = r'''
set -euo pipefail
name=$1
cid=$(docker inspect -f '{{.Id}}' "$name")
test "$(docker inspect -f '{{.State.Running}}' "$name")" = true
cpu=/sys/fs/cgroup/cpu/docker/$cid
cpuset=/sys/fs/cgroup/cpuset/docker/$cid
memory=/sys/fs/cgroup/memory/docker/$cid
pids=/sys/fs/cgroup/pids/docker/$cid
blkio=/sys/fs/cgroup/blkio/docker/$cid
printf 'container_id=%s\n' "$cid"
printf 'cpu_quota_us=%s\n' "$(cat "$cpu/cpu.cfs_quota_us")"
printf 'cpu_period_us=%s\n' "$(cat "$cpu/cpu.cfs_period_us")"
printf 'cpuset_cpus=%s\n' "$(cat "$cpuset/cpuset.cpus")"
printf 'cpuset_mems=%s\n' "$(cat "$cpuset/cpuset.mems")"
printf 'memory_bytes=%s\n' "$(cat "$memory/memory.limit_in_bytes")"
printf 'memory_swap_bytes=%s\n' "$(cat "$memory/memory.memsw.limit_in_bytes")"
printf 'pids_max=%s\n' "$(cat "$pids/pids.max")"
printf 'io_read=%s\n' "$(tr '\n' ',' <"$blkio/blkio.throttle.read_bps_device" | sed 's/,$//')"
printf 'io_write=%s\n' "$(tr '\n' ',' <"$blkio/blkio.throttle.write_bps_device" | sed 's/,$//')"
printf 'buffer_pool_bytes=%s\n' "$(docker exec "$name" mysql --defaults-file=/run/ra-credentials/selected.cnf -NBe 'SELECT @@GLOBAL.innodb_buffer_pool_size')"
'''
    completed = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", host, "bash", "-s", "--", container],
        input=script, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=30, check=True,
    )
    fields = dict(
        line.split("=", 1)
        for line in completed.stdout.splitlines()
        if "=" in line
    )
    numeric_names = {
        "cpu_quota_us", "cpu_period_us", "memory_bytes", "memory_swap_bytes",
        "pids_max", "buffer_pool_bytes",
    }
    effective: dict[str, Any] = {
        name: int(value) if name in numeric_names else value
        for name, value in fields.items()
    }
    read_entries = dict(
        (device, int(value))
        for device, value in (
            item.split(" ", 1) for item in fields["io_read"].split(",") if item
        )
    )
    write_entries = dict(
        (device, int(value))
        for device, value in (
            item.split(" ", 1) for item in fields["io_write"].split(",") if item
        )
    )
    matching = [
        device for device in read_entries
        if read_entries[device] == int(envelope["io_read_bps"])
        and write_entries.get(device) == int(envelope["io_write_bps"])
    ]
    errors: list[str] = []
    quota = effective["cpu_quota_us"] / effective["cpu_period_us"]
    if quota != float(envelope["cpu_quota_cores"]):
        errors.append("cpu_quota")
    if effective["cpuset_cpus"] != envelope["cpuset_cpus"]:
        errors.append("cpuset_cpus")
    if effective["cpuset_mems"] != envelope["cpuset_mems"]:
        errors.append("cpuset_mems")
    if not 0 <= int(envelope["memory_bytes"]) - effective["memory_bytes"] < 4096:
        errors.append("memory_bytes")
    if effective["memory_swap_bytes"] != effective["memory_bytes"]:
        errors.append("memory_swap_bytes")
    if effective["pids_max"] != int(envelope["pids_max"]):
        errors.append("pids_max")
    if effective["buffer_pool_bytes"] != int(envelope["innodb_buffer_pool_bytes"]):
        errors.append("buffer_pool_bytes")
    if len(matching) != 1:
        errors.append("io_read_write_bps")
    if errors:
        raise RuntimeError("effective envelope mismatch: " + ",".join(errors))
    document = {
        "schema_version": "mysql_sgd_effective_resource_readback_v2",
        "scenario": state["scenario"],
        "benchmark": state["benchmark"],
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "run_id": state["run_id"],
        "host": host,
        "container": container,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "requested": envelope,
        "effective": {
            **effective,
            "io_device_major_minor": matching[0],
            "io_read_bps": read_entries[matching[0]],
            "io_write_bps": write_entries[matching[0]],
        },
        "verified": True,
    }
    target = run_dir / "effective_resource_readback_v2.json"
    atomic_json(target, document)
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
