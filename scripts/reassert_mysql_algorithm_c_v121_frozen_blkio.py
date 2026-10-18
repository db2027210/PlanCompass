#!/usr/bin/env python3
"""Restore frozen v20 blkio throttles lost after Docker container restart."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import prepare_mysql_four_scene_v4 as prepare


FREEZE = ROOT / (
    "config/formal/frozen_mysql_four_scene_two_worker_20260903_v20/"
    "four_scene_effective_resource_successor.json"
)
OUTPUT = ROOT / (
    "history/mysql_four_scene_20260809/"
    "c_v121_paper_baseline_authority_20260920/"
    "frozen_blkio_reassertion.json"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.output.is_symlink():
        raise RuntimeError("symlink blkio reassertion journal prohibited")
    output = args.output.resolve()
    if ROOT not in output.parents or output.exists():
        raise RuntimeError("fresh blkio reassertion journal required")
    freeze = json.loads(FREEZE.read_text())
    rows = []
    for worker in freeze["workers"]:
        completed = subprocess.run(
            ["docker", "inspect", str(worker["container"])],
            cwd=ROOT, capture_output=True, text=True, check=True, timeout=300,
        )
        inspected = json.loads(completed.stdout)
        if len(inspected) != 1 or not inspected[0]["State"]["Running"]:
            raise RuntimeError(f"{worker['worker_id']} not running")
        result = prepare._enforce_blkio(inspected[0], freeze, worker)
        rows.append({
            "worker_id": worker["worker_id"], "scene": worker["scene"],
            "container": worker["container"], "container_id": inspected[0]["Id"],
            "read_bps": result["read_bps"],
            "write_bps": result["write_bps"],
            "device_major_minor": result["device_major_minor"],
            "cgroup_path": result["cgroup_path"],
            "frozen_values_reasserted_and_read_back": True,
        })
    document = {
        "schema_version": "mysql_algorithm_c_v121_frozen_blkio_reassertion_v1",
        "status": "PASS", "created_at": datetime.now().astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_path": str(FREEZE),
        "freeze_sha256": hashlib.sha256(FREEZE.read_bytes()).hexdigest(),
        "resource_envelope_changed": False,
        "postgresql_process_control_or_stop_performed": False,
        "workers": rows,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, sort_keys=True, indent=2) + "\n")
    output.with_name(output.name + ".sha256").write_text(
        f"{hashlib.sha256(output.read_bytes()).hexdigest()}  {output.name}\n"
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
