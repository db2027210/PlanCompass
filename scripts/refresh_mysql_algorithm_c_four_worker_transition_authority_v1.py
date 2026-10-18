#!/usr/bin/env python3
"""Refresh live four-worker C authority after normal container resets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import transition_mysql_algorithm_c_cpu_a_slots_to_four_workers_v1 as transition


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scene", choices=("hybench_xp_cpu", "hybench_xp_io"), required=True
    )
    parser.add_argument("--transition-root", type=Path, required=True)
    parser.add_argument("--authority-dirname", required=True)
    args = parser.parse_args()
    root = args.transition_root.expanduser().resolve()
    if transition.HISTORY.resolve() not in root.parents:
        raise RuntimeError("transition root beneath formal history required")
    if not args.authority_dirname.startswith("live_authority_v"):
        raise RuntimeError("versioned live authority directory required")
    output = root / args.authority_dirname
    if output.exists() or output.is_symlink():
        raise RuntimeError("fresh live authority output required")
    old = transition.AUTHORITY_DIRNAME
    transition.AUTHORITY_DIRNAME = args.authority_dirname
    try:
        paths, live = transition.build_authority(args.scene, root)
    finally:
        transition.AUTHORITY_DIRNAME = old
    print(json.dumps({
        "status": "PASS",
        "scene": args.scene,
        "authority_root": str(output),
        "paths": {name: str(path) for name, path in paths.items()},
        "worker_ids": sorted(live),
        "container_ids": {
            worker_id: row.get("container_id") for worker_id, row in live.items()
        },
        "accepted_state_credit_increment": 0,
        "recalibration_performed": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
