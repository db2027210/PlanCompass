#!/usr/bin/env python3
"""Unify TPC-H capability, physical, and runtime resource contracts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any


EXPECTED_SOURCE_SHA = (
    "2155152de797fb8fdf9c7c62ba53513731dfc70e72787e119ba1e82c380e323a"
)
CONTAINER = "pg5_tpch_ap_slot_node01_20260726"
CPU_CORES = 22
MEMORY_BYTES = 1_730_000_000
IO_READ_BPS = 16 * 1024 * 1024
IO_WRITE_BPS = 20 * 1024 * 1024


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--sha-output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists() or args.sha_output.exists():
        raise FileExistsError("refusing to overwrite unified resource contract")

    source_bytes = args.source.read_bytes()
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    if source_sha != EXPECTED_SOURCE_SHA:
        raise RuntimeError(f"source SHA mismatch: {source_sha}")
    config: dict[str, Any] = json.loads(source_bytes)

    capability = config.get("capability")
    physical = config.get("_physical_resource_contract")
    runtime = config.get("_tpch_runtime_resource_contract")
    docker_runtime = (config.get("metrics") or {}).get("docker_runtime")
    if capability != {"cpu": 32, "io": 36, "memory": 2.6263296604156494}:
        raise RuntimeError(f"unexpected stale capability: {capability}")
    expected_physical = {
        "container": CONTAINER,
        "cpu_quota_cores": 32,
        "io_device": "/dev/nvme2n1",
        "io_read_bps": IO_READ_BPS,
        "io_semantics": "(read_bps+write_bps)/(io_read_bps+io_write_bps)",
        "io_total_bps": IO_READ_BPS + IO_WRITE_BPS,
        "io_write_bps": IO_WRITE_BPS,
        "memory_bytes": 2_820_000_000,
    }
    if physical != expected_physical:
        raise RuntimeError(f"unexpected stale physical contract: {physical}")
    if not isinstance(runtime, dict) or {
        "container": runtime.get("container"),
        "cpu_quota_cores": runtime.get("cpu_quota_cores"),
        "memory_limit_bytes": runtime.get("memory_limit_bytes"),
        "memory_swap_limit_bytes": runtime.get("memory_swap_limit_bytes"),
        "io_read_cap_mib_s": runtime.get("io_read_cap_mib_s"),
        "io_write_cap_mib_s": runtime.get("io_write_cap_mib_s"),
        "workers": runtime.get("workers"),
    } != {
        "container": CONTAINER,
        "cpu_quota_cores": CPU_CORES,
        "memory_limit_bytes": MEMORY_BYTES,
        "memory_swap_limit_bytes": MEMORY_BYTES,
        "io_read_cap_mib_s": 16,
        "io_write_cap_mib_s": 20,
        "workers": 36,
    }:
        raise RuntimeError(f"runtime contract mismatch: {runtime}")
    if not isinstance(docker_runtime, dict) or docker_runtime.get("container") != CONTAINER:
        raise RuntimeError("docker runtime container mismatch")

    config["capability"] = {
        "cpu": CPU_CORES,
        "memory": MEMORY_BYTES / (1024 ** 3),
        "io": 36,
    }
    config["_physical_resource_contract"] = {
        "container": CONTAINER,
        "cpu_quota_cores": CPU_CORES,
        "memory_bytes": MEMORY_BYTES,
        "io_device": "/dev/nvme1n1",
        "io_read_bps": IO_READ_BPS,
        "io_write_bps": IO_WRITE_BPS,
        "io_total_bps": IO_READ_BPS + IO_WRITE_BPS,
        "io_semantics": "(read_bps+write_bps)/(io_read_bps+io_write_bps)",
    }
    runtime["version"] = "attempt48-resource-correction-adaptive-v10-unified-stable-r0"
    config["_tpch_runtime_resource_contract"] = runtime
    config["_tpch_formal_stable_r0_contract"] = {
        "schema_version": "tpch_formal_stable_r0_contract_v1",
        "whole_process_startup_average_forbidden_for_baseline_gate": True,
        "required_decision_scope": "latest_six_consecutive_30_second_windows",
        "profile_construction_gate_required": True,
        "formal_resource_gate_required": True,
        "failed_queries_required": 0,
        "worker_cleanup_required": True,
        "qps_role": "outer_audit_only",
    }

    rendered = json.dumps(config, indent=2, sort_keys=True) + "\n"
    digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    os.replace(temporary, args.output)
    args.sha_output.write_text(f"{digest}  {args.output.name}\n", encoding="utf-8")
    print(json.dumps({
        "source_sha256": source_sha,
        "output_sha256": digest,
        "cpu": CPU_CORES,
        "memory_bytes": MEMORY_BYTES,
        "io_mib_s": 36,
        "container": CONTAINER,
    }, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
