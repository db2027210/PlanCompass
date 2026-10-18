#!/usr/bin/env python3
"""Clone a replacement for the unreapable external-baseline XP-CPU w1."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Dict

from shell import prepare_mysql_external_baseline_clones as prep


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-binding", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    binding = prep.load(args.source_binding.resolve())
    workers = {
        str(row["worker_id"]): copy.deepcopy(row)
        for row in binding.get("workers") or []
    }
    source = workers["xp_cpu_w1"]
    runtime_root = args.runtime_root.resolve()
    output = args.output.resolve()
    container = "ra_pgssd_mysql_baseline_xp_cpu_w1_r2_20260826"
    host_port = 65531
    database_cpuset = "112-127"
    if runtime_root.exists() or output.exists():
        raise RuntimeError("replacement runtime/output already exists")
    if prep.port_is_free(host_port) is not True:
        raise RuntimeError(f"replacement host port is occupied: {host_port}")
    existing = prep.run(
        ["docker", "ps", "-aq", "--filter", f"name=^{container}$"],
        timeout=30,
    ).stdout.strip()
    if existing:
        raise RuntimeError(f"replacement container exists: {container}")
    requested = set(range(112, 128))
    running = prep.existing_running_cpuset()
    overlaps = {cpu: running[cpu] for cpu in requested if cpu in running}
    if overlaps:
        raise RuntimeError(f"replacement CPU set overlaps: {overlaps}")

    runtime_root.mkdir(parents=True, mode=0o700)
    copied = prep.copy_worker(source, binding["freeze"], runtime_root)
    worker: Dict[str, Any] = copy.deepcopy(source)
    worker.update(
        {
            "container": container,
            "host_port": host_port,
            "database_cpuset": database_cpuset,
            "database_cpuset_mems": "0",
            "datadir": copied["datadir"],
            "credential_defaults": str(
                Path(copied["credentials"]) / "selected.cnf"
            ),
            "credential_benchmark": str(
                Path(copied["credentials"]) / "benchmark.cnf"
            ),
            "copy_seconds": copied["copy_seconds"],
        }
    )
    runtime_freeze = copy.deepcopy(binding["freeze"])
    runtime_freeze["storage"] = {
        **dict(runtime_freeze["storage"]),
        "device": "/dev/nvme1n1",
        "mount": "/disk1",
        "runtime_root": str(runtime_root),
        "copy_mode": "full_physical_copy_no_overlay_no_reflink",
        "independent_external_baseline_data_plane": True,
    }
    worker = prep.start_worker(worker, runtime_freeze, device="/dev/nvme1n1")
    document = {
        "schema_version": "mysql_external_xp_worker_replacement_v1",
        "status": "PASS",
        "reason": "unreapable_pid_namespace_after_mysqld_semaphore_abort",
        "replaces_container": source["container"],
        "replacement_worker": worker,
        "source_binding": str(args.source_binding.resolve()),
        "source_binding_sha256": prep.sha(args.source_binding.resolve()),
        "old_container_retained_as_failure_evidence": True,
        "workload_started": False,
    }
    prep.atomic(output, document)
    print(json.dumps(document, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
