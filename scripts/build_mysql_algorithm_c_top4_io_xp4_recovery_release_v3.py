#!/usr/bin/env python3
"""Freeze the import-safe v10 Top4 IO Algorithm C successor."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_top4_io_xp4_recovery_release_v2 as predecessor


predecessor.OUTPUT = (
    ROOT / "config/formal/frozen_mysql_algorithm_c_top4_io_xp4_recovery_20260826_v10"
)
predecessor.CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_async_scene_xp4_v3.py"


def main() -> int:
    return predecessor.main()


if __name__ == "__main__":
    raise SystemExit(main())
