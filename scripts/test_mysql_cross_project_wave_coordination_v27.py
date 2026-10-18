#!/usr/bin/env python3
"""Live, read-only validation for the kernel/cgroup quiet gate."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile

from scripts import mysql_cross_project_wave_coordination_v27 as gate


SCENES = {
    "job": {
        "client": set(range(0, 8)) | set(range(256, 264)),
        "database": set(range(104, 112)) | set(range(360, 368)),
    },
    "tpch": {
        "client": set(range(8, 16)) | set(range(264, 272)),
        "database": set(range(200, 216)) | set(range(456, 472)),
    },
    "xp_cpu": {
        "client": set(range(16, 24)) | set(range(272, 280)),
        "database": set(range(176, 180)) | set(range(432, 436)),
    },
    "xp_io": {
        "client": set(range(24, 32)) | set(range(280, 288)),
        "database": set(range(216, 228)) | set(range(472, 484)),
    },
}


def atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("scene", choices=sorted(SCENES))
    parser.add_argument("output", type=Path)
    parser.add_argument("--maximum-wait", type=float, default=45.0)
    args = parser.parse_args()
    cpus = SCENES[args.scene]
    os.sched_setaffinity(0, cpus["client"])
    result = gate.rolling_quiet_gate(
        run_id=f"v27_live_test_{args.scene}",
        required_seconds=15.0,
        maximum_wait_seconds=args.maximum_wait,
        target_cpus=cpus["client"] | cpus["database"],
    )
    atomic(args.output, result)
    print(json.dumps({
        "status": result["status"],
        "errors": result["errors"],
        "observed_seconds": result["observed_seconds"],
        "sample_count": len(result["samples"]),
        "output": str(args.output),
    }, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

