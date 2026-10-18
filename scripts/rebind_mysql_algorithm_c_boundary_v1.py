#!/usr/bin/env python3
"""Rebind all eight live workers after a successful boundary gate."""

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import rebind_mysql_four_scene_live_v8 as implementation


implementation.FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260811_v15"
    / "four_scene_effective_resource_successor.json"
)
implementation.V11_FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260811_v14"
    / "four_scene_effective_resource_successor.json"
)
implementation.PREFLIGHT = (
    ROOT / "preflight/mysql_four_scene_20260809/runtime_preflight_v57.json"
)
implementation.NON_XP_PREPARED = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_live_rebind_v30_20260812T010000p0800/prepared_binding.json"
)
implementation.XP_PREPARED = (
    ROOT / "history/mysql_four_scene_20260809"
    / "algorithm_c_boundary_outer5_xp_binding_v1_20260812_retry16/prepared_binding.json"
)
implementation.FREEZE_SCHEMA = (
    "mysql_disk2_four_scene_two_worker_effective_resource_successor_v15"
)
implementation.PREDECESSOR_FREEZE_SCHEMA = (
    "mysql_disk2_four_scene_two_worker_effective_resource_successor_v14"
)
implementation.OUTPUT_SCHEMA = "mysql_algorithm_c_boundary_live_rebind_v1"
implementation.AUTHORITY_VERSION = "v15_algorithm_c_outer3_boundary"
implementation.XP_ALLOWED_STATUSES = {"PREPARED", "PASS", "PASS_RECOVERY_ONLY"}


def _regular_preflight(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    allowed = (ROOT / "preflight/mysql_four_scene_20260809").resolve()
    if path.is_symlink() or not path.is_file() or path.parent != allowed:
        raise argparse.ArgumentTypeError(
            f"regular preflight below {allowed} required: {path}"
        )
    return path


def _regular_binding(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    allowed = (ROOT / "history/mysql_four_scene_20260809").resolve()
    if path.is_symlink() or not path.is_file() or allowed not in path.parents:
        raise argparse.ArgumentTypeError(
            f"regular binding below {allowed} required: {path}"
        )
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--runtime-preflight", type=_regular_preflight,
        default=implementation.PREFLIGHT,
    )
    parser.add_argument(
        "--non-xp-prepared", type=_regular_binding,
        default=implementation.NON_XP_PREPARED,
    )
    parser.add_argument(
        "--xp-prepared", type=_regular_binding,
        default=implementation.XP_PREPARED,
    )
    args = parser.parse_args()
    implementation.PREFLIGHT = args.runtime_preflight
    implementation.NON_XP_PREPARED = args.non_xp_prepared
    implementation.XP_PREPARED = args.xp_prepared
    sys.argv = [sys.argv[0], "--run-id", args.run_id]
    return implementation.main()


if __name__ == "__main__":
    raise SystemExit(main())
