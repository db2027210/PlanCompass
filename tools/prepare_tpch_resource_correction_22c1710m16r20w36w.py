#!/usr/bin/env python3
"""Create the evidence-calibrated TPC-H resource-correction proposal."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def atomic(path: Path, content: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--sha-output", required=True, type=Path)
    parser.add_argument("--expected-memory-bytes", required=True, type=int)
    parser.add_argument("--memory-bytes", required=True, type=int)
    parser.add_argument("--runtime-version", required=True)
    args = parser.parse_args()

    if args.output.exists() or args.sha_output.exists():
        raise FileExistsError("refusing to overwrite an existing proposal")
    config = load(args.source)
    runtime = config.get("_tpch_runtime_resource_contract")
    if not isinstance(runtime, dict):
        raise RuntimeError("runtime resource contract is missing")
    observed = {
        "cpu_quota_cores": runtime.get("cpu_quota_cores"),
        "memory_limit_bytes": runtime.get("memory_limit_bytes"),
        "workers": runtime.get("workers"),
        "io_read_cap_mib_s": runtime.get("io_read_cap_mib_s"),
        "io_write_cap_mib_s": runtime.get("io_write_cap_mib_s"),
    }
    expected = {
        "cpu_quota_cores": 22,
        "memory_limit_bytes": args.expected_memory_bytes,
        "workers": 36,
        "io_read_cap_mib_s": 16,
        "io_write_cap_mib_s": 20,
    }
    if observed != expected:
        raise RuntimeError(f"source proposal state mismatch: {observed}")

    runtime.update({
        "version": args.runtime_version,
        "cpu_quota_cores": 22,
        "memory_limit_bytes": args.memory_bytes,
        "memory_swap_limit_bytes": args.memory_bytes,
        "workers": 36,
        "io_read_cap_mib_s": 16,
        "io_write_cap_mib_s": 20,
        "io_total_limit_mib_s": 36,
        "resource_change_authorized": False,
        "calibration_evidence": {
            "source": "rolling_v2_observed_inverse_memory_scaling",
            "observed_r0_utilization_pct": {
                "source_cap_bytes": args.expected_memory_bytes,
                "window_300_memory": 90.01767489538662,
                "window_330_memory": 90.27377869265757,
                "window_360_memory": 90.75703084080573,
                "window_390_memory": 90.79607325276152,
                "window_420_memory": 90.66359865886939,
            },
            "predicted_utilization_pct": {
                "window_300_memory": 90.01767489538662
                * args.expected_memory_bytes / args.memory_bytes,
                "window_420_memory": 90.66359865886939
                * args.expected_memory_bytes / args.memory_bytes,
            },
        },
    })
    config["_tpch_runtime_resource_contract"] = runtime
    rendered = json.dumps(config, indent=2, sort_keys=True) + "\n"
    digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    atomic(args.output, rendered)
    atomic(args.sha_output, f"{digest}  {args.output.name}\n")
    print(json.dumps({
        "output": str(args.output),
        "sha256": digest,
        "cpu": 22,
        "memory": args.memory_bytes,
        "read": 16,
        "write": 20,
        "workers": 36,
    }, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
