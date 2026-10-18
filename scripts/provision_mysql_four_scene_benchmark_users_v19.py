#!/usr/bin/env python3
"""Provision benchmark accounts for a freshly prepared v19 binding."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import provision_mysql_four_scene_benchmark_users_v5 as base


def regular_file(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file() or path.is_symlink():
        raise argparse.ArgumentTypeError(f"regular file required: {path}")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--prepared", type=regular_file, required=True)
    parser.add_argument("--freeze", type=regular_file, required=True)
    args = parser.parse_args()

    base.PREPARED = args.prepared
    base.FREEZE = args.freeze
    base.FREEZE_SCHEMA = "mysql_four_scene_two_worker_qps_successor_v19"
    base.OUTPUT_SCHEMA = "mysql_four_scene_benchmark_accounts_v19"
    base.AUTHORITY_VERSION = "v19"
    old_argv = sys.argv
    sys.argv = [str(Path(__file__).resolve()), "--run-id", args.run_id]
    try:
        return base.main()
    finally:
        sys.argv = old_argv


if __name__ == "__main__":
    raise SystemExit(main())
