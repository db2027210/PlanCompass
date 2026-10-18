#!/usr/bin/env python3
"""Seal a C-to-B release against the current formal runtime sources."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_b_from_algorithm_c_release_v1 as predecessor


SCHEMA = "mysql_algorithm_b_from_algorithm_c_interval_release_v2"
CONTROLLER = ROOT / "scripts/run_mysql_four_scene_algorithm_b_from_c_v2.py"
LAUNCHER = ROOT / "scripts/launch_mysql_four_scene_algorithm_b_from_c_v2.py"
AUTO_LAUNCH = ROOT / "scripts/wait_and_launch_mysql_algorithm_b_after_c_v3.py"
ORIGINAL_BUILD = predecessor.build_release


def _configure() -> None:
    predecessor.SCHEMA = SCHEMA
    predecessor.ALGORITHM_B_CONTROLLER_SOURCE = CONTROLLER
    predecessor.ALGORITHM_B_LAUNCHER_SOURCE = LAUNCHER
    predecessor.ALGORITHM_B_AUTO_LAUNCH_SOURCE = AUTO_LAUNCH


def build_release(checkpoint_path: Path) -> dict[str, Any]:
    _configure()
    release = ORIGINAL_BUILD(checkpoint_path)
    release["builder_source"] = predecessor._bound_regular(
        Path(__file__).resolve(), "Algorithm B v2 handoff builder",
    )
    release["algorithm_b_runtime_contract"].update({
        "release_version": "v2",
        "algorithm_c_executor_chain": "v27",
        "co_resident_isolation_release": "v11",
        "runtime_watchdog_release": "v11",
        "external_pg_affinity_observer_required": True,
        "durable_post_target_confirmation_required": True,
    })
    payload = copy.deepcopy(release)
    payload.pop("release_sha256", None)
    release["release_sha256"] = predecessor._canonical_sha(payload)
    return release


_configure()
predecessor.build_release = build_release


def verify_release(path: Path) -> dict[str, Any]:
    _configure()
    return predecessor.verify_release(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--algorithm-c-checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,180}", args.output.name) is None:
        parser.error("unsafe output file name")
    if args.output.exists():
        raise RuntimeError(f"fresh output required: {args.output}")
    release = build_release(args.algorithm_c_checkpoint)
    predecessor._atomic(args.output.resolve(), release)
    verify_release(args.output.resolve())
    print(json.dumps({
        "status": "PREPARED",
        "output": str(args.output.resolve()),
        "release_sha256": release["release_sha256"],
    }, sort_keys=True))
    return 0


SCENES = predecessor.SCENES
_atomic = predecessor._atomic


if __name__ == "__main__":
    raise SystemExit(main())
