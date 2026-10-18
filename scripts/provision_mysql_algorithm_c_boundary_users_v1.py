#!/usr/bin/env python3
"""Verify all eight benchmark accounts for a boundary resume."""

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import provision_mysql_four_scene_benchmark_users_v5 as implementation


implementation.FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260811_v15"
    / "four_scene_effective_resource_successor.json"
)
implementation.PREPARED = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_live_rebind_boundary_outer5_v1_20260812_retry17/prepared_binding.json"
)
implementation.FREEZE_SCHEMA = (
    "mysql_disk2_four_scene_two_worker_effective_resource_successor_v15"
)
implementation.OUTPUT_SCHEMA = "mysql_algorithm_c_boundary_benchmark_accounts_v1"
implementation.AUTHORITY_VERSION = "v15_algorithm_c_outer3_boundary"


def _regular_binding(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    allowed = (ROOT / "history/mysql_four_scene_20260809").resolve()
    if path.is_symlink() or not path.is_file() or allowed not in path.parents:
        raise argparse.ArgumentTypeError(
            f"regular prepared binding below {allowed} required: {path}"
        )
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--prepared-binding", type=_regular_binding,
        default=implementation.PREPARED,
    )
    parser.add_argument("--verify-existing", action="store_true")
    parser.add_argument("--rotate-all", action="store_true")
    args = parser.parse_args()
    implementation.PREPARED = args.prepared_binding
    sys.argv = [sys.argv[0], "--run-id", args.run_id]
    if args.verify_existing:
        sys.argv.append("--verify-existing")
    if args.rotate_all:
        sys.argv.append("--rotate-all")
    return implementation.main()


if __name__ == "__main__":
    raise SystemExit(main())
